import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from niyan.errors import GitError
from niyan.git import load_checkout_identity


LFS_POINTER_VERSION = b'version https://git-lfs.github.com/spec/v1'
LFS_OBJECT_ID_PATTERN = re.compile(br'^oid sha256:([0-9a-f]{64})$')


def show_status(*, cwd=None, stdout=None):
    """Show a validated dataset checkout's Git and LFS working state."""

    output = stdout or sys.stdout
    working_directory = Path(cwd or Path.cwd())
    identity = _require_checkout(working_directory)
    status_result = _run_git(working_directory, ['status', '--porcelain=v2', '--branch', '-z'], operation='read working-copy status')
    shallow_result = _run_git(working_directory, ['rev-parse', '--is-shallow-repository'], operation='inspect repository history')
    root_result = _run_git(working_directory, ['rev-parse', '--show-toplevel'], operation='locate the working tree')
    git_directory_result = _run_git(working_directory, ['rev-parse', '--path-format=absolute', '--git-common-dir'], operation='locate Git object storage')
    status = parse_porcelain_v2(status_result.stdout)
    lfs_paths = _list_lfs_paths(working_directory)
    root = Path(os.fsdecode(root_result.stdout).strip())
    lfs_storage = _lfs_storage_directory(working_directory, Path(os.fsdecode(git_directory_result.stdout).strip()))
    lfs_states = {path: _lfs_worktree_state(root=root, path=path, storage=lfs_storage) for path in lfs_paths}

    print(f'Dataset: {identity.dataset_path} ({identity.dataset_id})', file=output)
    branch = status['branch'] or '(detached)'
    branch_details = []
    if status['upstream']:
        branch_details.append(status['upstream'])
    if status['ahead'] or status['behind']:
        branch_details.append(f"ahead {status['ahead']}, behind {status['behind']}")
    rendered_branch_details = f" [{', '.join(branch_details)}]" if branch_details else ''
    print(f'Branch: {branch}{rendered_branch_details}', file=output)
    print(f"History: {'shallow' if shallow_result.stdout.strip() == b'true' else 'full'}", file=output)

    entries = status['entries']
    if entries:
        print('Changes:', file=output)
        for entry in entries:
            path = entry['path']
            displayed_path = f"{_display_path(entry['original_path'])} -> {_display_path(path)}" if entry['original_path'] else _display_path(path)
            lfs_state = lfs_states.get(path)
            annotation = f' [LFS: {lfs_state}]' if lfs_state else ''
            print(f"  {entry['code']} {_describe_status(entry)}: {displayed_path}{annotation}", file=output)
    else:
        print('Git working tree clean.', file=output)

    unavailable = [(path, state) for path, state in lfs_states.items() if state in ('content unavailable', 'pointer cached') and path not in {entry['path'] for entry in entries}]
    if unavailable:
        print('LFS content:', file=output)
        for path, state in sorted(unavailable):
            print(f'  {state}: {_display_path(path)}', file=output)
    return status


def show_diff(*, staged=False, cwd=None, stdout=None):
    """Stream an unstaged or staged Git diff for a validated checkout."""

    working_directory = Path(cwd or Path.cwd())
    _require_checkout(working_directory)
    arguments = ['diff']
    if staged:
        arguments.append('--cached')
    _run_git(working_directory, arguments, operation='render the dataset diff', capture_output=False, stdout=stdout)


def show_log(*, limit=20, cwd=None, stdout=None):
    """Show bounded commit history and indicate shallow repositories."""

    output = stdout or sys.stdout
    working_directory = Path(cwd or Path.cwd())
    identity = _require_checkout(working_directory)
    shallow_result = _run_git(working_directory, ['rev-parse', '--is-shallow-repository'], operation='inspect repository history')
    print(f'Dataset: {identity.dataset_path}', file=output)
    print(f"History: {'shallow' if shallow_result.stdout.strip() == b'true' else 'full'}", file=output)
    head_result = _run_git(working_directory, ['rev-parse', '--verify', 'HEAD'], operation='inspect dataset history', accepted_statuses={0, 128})
    if head_result.returncode == 128:
        print('No commits.', file=output)
        return
    history_result = _run_git(
        working_directory,
        ['log', f'--max-count={limit}', '--date=short', '--pretty=format:%h%x09%ad%x09%an%x09%s'],
        operation='read dataset history',
    )
    if history_result.stdout:
        print(os.fsdecode(history_result.stdout), file=output)


def parse_porcelain_v2(payload):
    """Parse NUL-delimited Git porcelain-v2 status records."""

    status = {'branch': None, 'upstream': None, 'ahead': 0, 'behind': 0, 'entries': []}
    records = payload.split(b'\0')
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if record.startswith(b'# '):
            _parse_status_header(record, status)
            continue
        record_type = record[:1]
        if record_type == b'1':
            fields = record.split(b' ', 8)
            if len(fields) != 9:
                raise GitError('Git returned malformed ordinary status metadata.')
            status['entries'].append({'code': os.fsdecode(fields[1]), 'path': os.fsdecode(fields[8]), 'original_path': None})
        elif record_type == b'2':
            fields = record.split(b' ', 9)
            if len(fields) != 10 or index >= len(records):
                raise GitError('Git returned malformed rename status metadata.')
            original_path = records[index]
            index += 1
            status['entries'].append({'code': os.fsdecode(fields[1]), 'path': os.fsdecode(fields[9]), 'original_path': os.fsdecode(original_path)})
        elif record_type == b'u':
            fields = record.split(b' ', 10)
            if len(fields) != 11:
                raise GitError('Git returned malformed conflict status metadata.')
            status['entries'].append({'code': os.fsdecode(fields[1]), 'path': os.fsdecode(fields[10]), 'original_path': None})
        elif record_type == b'?':
            status['entries'].append({'code': '??', 'path': os.fsdecode(record[2:]), 'original_path': None})
        elif record_type != b'!':
            raise GitError('Git returned an unknown working-copy status record.')
    return status


def _parse_status_header(record, status):
    """Apply one recognized branch header to parsed status metadata."""

    key, separator, value = record[2:].partition(b' ')
    if not separator:
        return
    if key == b'branch.head':
        status['branch'] = None if value == b'(detached)' else os.fsdecode(value)
    elif key == b'branch.upstream':
        status['upstream'] = os.fsdecode(value)
    elif key == b'branch.ab':
        fields = value.split()
        if len(fields) == 2 and fields[0].startswith(b'+') and fields[1].startswith(b'-'):
            try:
                status['ahead'] = int(fields[0][1:])
                status['behind'] = int(fields[1][1:])
            except ValueError as error:
                raise GitError('Git returned malformed branch divergence metadata.') from error


def _describe_status(entry):
    """Describe one porcelain status code in working-copy terms."""

    code = entry['code']
    if code == '??':
        return 'Untracked'
    if code in ('DD', 'AU', 'UD', 'UA', 'DU', 'AA', 'UU') or 'U' in code:
        return 'Conflict'
    if entry['original_path'] is not None or 'R' in code:
        change = 'Renamed'
    elif 'D' in code:
        change = 'Deleted'
    elif 'A' in code:
        change = 'Added'
    else:
        change = 'Modified'
    locations = []
    if code[:1] not in ('', '.', ' '):
        locations.append('staged')
    if code[1:2] not in ('', '.', ' '):
        locations.append('unstaged')
    return f"{change} ({', '.join(locations)})" if locations else change


def _display_path(path):
    """Escape control characters while leaving ordinary paths readable."""

    return path if path.isprintable() else repr(path)


def _require_checkout(cwd):
    """Return validated Niyān checkout identity or reject a generic Git tree."""

    identity = load_checkout_identity(cwd)
    if identity is None:
        raise GitError('This command must run inside a Niyān dataset checkout.')
    return identity


def _list_lfs_paths(cwd):
    """Find current-index LFS pointer paths without requiring Git LFS."""

    result = _run_git(cwd, ['grep', '-l', '-z', '--cached', '-e', '^version https://git-lfs.github.com/spec/v1$', '--'], operation='inspect Git LFS pointers', accepted_statuses={0, 1})
    return {os.fsdecode(path) for path in result.stdout.split(b'\0') if path}


def _lfs_storage_directory(cwd, git_directory):
    """Resolve configured or default local Git LFS object storage."""

    result = _run_git(cwd, ['config', '--path', '--get', 'lfs.storage'], operation='inspect Git LFS storage', accepted_statuses={0, 1})
    if result.returncode == 1 or not result.stdout.strip():
        return git_directory / 'lfs'
    configured = Path(os.fsdecode(result.stdout).strip())
    return configured if configured.is_absolute() else git_directory / configured


def _lfs_worktree_state(*, root, path, storage):
    """Classify one LFS path without reading materialized large files."""

    candidate = (root / path).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise GitError('Git returned an unsafe working-tree path.') from error
    try:
        size = candidate.stat().st_size
    except FileNotFoundError:
        return 'missing'
    except OSError as error:
        raise GitError(f'Could not inspect LFS path {path!r}.') from error
    if size > 4096 or not candidate.is_file():
        return 'materialized'
    try:
        pointer = candidate.read_bytes()
    except OSError as error:
        raise GitError(f'Could not inspect LFS path {path!r}.') from error
    object_id = _parse_lfs_pointer(pointer)
    if object_id is None:
        return 'materialized'
    cached_object = storage / 'objects' / object_id[:2] / object_id[2:4] / object_id
    return 'pointer cached' if cached_object.is_file() else 'content unavailable'


def _parse_lfs_pointer(content):
    """Return the SHA-256 identifier from a canonical Git LFS pointer."""

    lines = content.rstrip(b'\n').splitlines()
    if len(lines) < 3 or lines[0] != LFS_POINTER_VERSION:
        return None
    object_id_match = LFS_OBJECT_ID_PATTERN.fullmatch(lines[1])
    if object_id_match is None or not lines[2].startswith(b'size ') or not lines[2][5:].isdigit():
        return None
    return object_id_match.group(1).decode('ascii')


def _run_git(cwd, arguments, *, operation, accepted_statuses=frozenset({0}), capture_output=True, stdout=None):
    """Run one bounded Git command without a shell and sanitize failures."""

    if shutil.which('git') is None:
        raise GitError('Git is required for Niyān working-copy commands.')
    command = ['git', '-C', str(cwd), *arguments]
    try:
        if capture_output:
            result = subprocess.run(command, check=False, capture_output=True, timeout=60)
        else:
            result = subprocess.run(command, check=False, stdout=stdout, stderr=subprocess.PIPE)
    except (OSError, subprocess.SubprocessError) as error:
        raise GitError(f'Git could not {operation}.') from error
    if result.returncode not in accepted_statuses:
        raise GitError(f'Git could not {operation}.')
    return result
