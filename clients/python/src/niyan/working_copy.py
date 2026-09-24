import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from niyan.errors import GitConflictError, GitError
from niyan.git import configure_lfs_transfer, load_checkout_identity
from niyan.terminal import is_interactive, select_lfs_extensions, status as terminal_status, success as terminal_success


LFS_POINTER_VERSION = b'version https://git-lfs.github.com/spec/v1'
LFS_OBJECT_ID_PATTERN = re.compile(br'^oid sha256:([0-9a-f]{64})$')
LFS_SIZE_THRESHOLD = 10 * 1024 * 1024
BINARY_SAMPLE_SIZE = 8000
COMPRESSION_SUFFIXES = {'.br', '.bz2', '.gz', '.lz4', '.xz', '.zst'}
SAFE_EXTENSION_PATTERN = re.compile(r'^\.[A-Za-z0-9][A-Za-z0-9._+-]{0,31}$')


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


def stage_paths(paths, *, all_paths=False, force_lfs=False, force_git=False, verbose=False, cwd=None, stdin=None, stderr=None):
    """Stage dataset changes through Git and optional explicit LFS overrides.

    Parameters
    ----------
    paths : list[str]
        Git pathspecs selected by the user.
    all_paths : bool, optional
        Stage all working-tree changes.
    force_lfs : bool, optional
        Persist Git LFS rules for selected regular files.
    force_git : bool, optional
        Persist ordinary-Git rules for selected regular files.
    verbose : bool, optional
        Print each path as Git stages it.
    cwd : pathlib.Path, optional
        Directory inside the Niyān checkout.
    stdin : file-like object, optional
        Input stream used to decide whether the one-time selector is safe.
    stderr : file-like object, optional
        Warning destination.
    """

    if force_lfs and force_git:
        raise GitError('--lfs and --git cannot be used together.')
    if all_paths and paths:
        raise GitError('--all cannot be combined with explicit paths.')
    if not all_paths and not paths:
        raise GitError('At least one path or --all is required to stage dataset content.')

    working_directory = Path(cwd or Path.cwd())
    _require_checkout(working_directory)
    configure_lfs_transfer(working_directory)
    root = Path(os.fsdecode(_run_git(working_directory, ['rev-parse', '--show-toplevel'], operation='locate the working tree').stdout).strip())
    git_pathspecs = _git_pathspecs(working_directory, paths)
    if not force_lfs and not force_git:
        attributes_created = _initialize_lfs_policy(root, working_directory, stdin=stdin, stderr=stderr)
        # Ordinary staging deliberately delegates file selection and clean-filter behavior to Git. In particular, never open every selected file merely to guess whether it belongs in LFS; repository .gitattributes is the standard, scalable source of truth.
        stage_arguments = ['add']
        if verbose:
            stage_arguments.append('--verbose')
        if all_paths:
            stage_arguments.append('--all')
        else:
            stage_arguments.extend(['-A', '--', *git_pathspecs])
            if attributes_created:
                stage_arguments.append(':(literal).gitattributes')
        with terminal_status('Staging dataset changes…', stderr=stderr, enabled=not verbose):
            _run_git(working_directory, stage_arguments, operation='stage the requested dataset paths', capture_output=not verbose, timeout=None)
        terminal_success('Dataset changes staged.', stderr=stderr)
        return

    status_arguments = ['-c', 'status.relativePaths=false', 'status', '--porcelain=v2', '-z', '--untracked-files=all']
    if not all_paths:
        status_arguments.extend(['--', *git_pathspecs])
    status = parse_porcelain_v2(_run_git(working_directory, status_arguments, operation='inspect paths selected for staging', timeout=None).stdout)
    candidates = {entry['path']: entry for entry in status['entries']}
    if not all_paths:
        for path in paths:
            if path.startswith(':') or any(character in path for character in '*?['):
                continue
            selected_path = Path(path)
            selected_path = selected_path if selected_path.is_absolute() else working_directory / selected_path
            try:
                metadata = selected_path.lstat()
                canonical_path = selected_path.parent.resolve() / selected_path.name
                relative = canonical_path.relative_to(root.resolve())
            except (FileNotFoundError, OSError, ValueError):
                continue
            nested_repository = stat.S_ISDIR(metadata.st_mode) and relative != Path('.') and ((selected_path / '.git').exists() or (selected_path / '.git').is_symlink())
            if not stat.S_ISDIR(metadata.st_mode) or nested_repository:
                relative_path = relative.as_posix()
                candidates.setdefault(relative_path, {'code': '  ', 'path': relative_path, 'original_path': None})

    # Explicit overrides also apply to clean tracked files, because changing the
    # storage mode is itself a deliberate staged change.
    if force_lfs or force_git:
        listed_arguments = ['ls-files', '--full-name', '-c', '-o', '--exclude-standard', '-z']
        if not all_paths:
            listed_arguments.extend(['--', *git_pathspecs])
        listed = _run_git(working_directory, listed_arguments, operation='enumerate paths selected for staging', timeout=None).stdout
        for encoded_path in listed.split(b'\0'):
            if encoded_path:
                path = os.fsdecode(encoded_path)
                candidates.setdefault(path, {'code': '  ', 'path': path, 'original_path': None})

    inspected = _infer_unambiguous_renames(root, [_inspect_candidate(root, entry) for entry in candidates.values()])
    explicit_directories = _explicit_directories(root, working_directory, paths) if not all_paths and (force_lfs or force_git) else []
    changed_attributes = set()
    for directory in explicit_directories:
        changed_attributes.add(_write_attribute_rule(root, directory, 'lfs' if force_lfs else 'git', recursive=True))

    planned = []
    lfs_required = False
    for candidate in inspected:
        mode, source, changed_file = _storage_mode(root, candidate, explicit_directories=explicit_directories, force_lfs=force_lfs, force_git=force_git, all_paths=all_paths)
        if changed_file is not None:
            changed_attributes.add(changed_file)
        if mode == 'lfs':
            lfs_required = True
        if force_git and candidate['kind'] == 'regular' and candidate['size'] > LFS_SIZE_THRESHOLD:
            print(f"warning: {_display_path(candidate['path'])} is larger than 10 MiB but will be stored as an ordinary Git blob because --git was requested.", file=stderr or sys.stderr)
        planned.append((candidate, mode, source))

    if lfs_required or (force_lfs and (inspected or explicit_directories)):
        _require_git_lfs()
        configure_lfs_transfer(working_directory, install_filters=True)
    for candidate, mode, source in planned:
        if candidate['kind'] != 'regular' or mode != 'lfs' or source not in ('automatic', 'explicit-file', 'existing'):
            continue
        _track_lfs_filename(root, candidate['path'])
        changed_attributes.add(root / '.gitattributes')

    for attributes_path in changed_attributes:
        relative_attributes = attributes_path.relative_to(root).as_posix()
        if _effective_filter(root, relative_attributes) == 'lfs':
            raise GitError(f'{_display_path(relative_attributes)!r} is covered by Git LFS, but attribute control files must remain ordinary Git blobs.')

    # Rules must actually win under Git's normal nested attribute precedence.
    for candidate, mode, source in planned:
        if candidate['kind'] != 'regular' or mode not in ('lfs', 'git') or source not in ('automatic', 'explicit-file', 'explicit-directory'):
            continue
        effective = _effective_filter(root, candidate['path'])
        if (mode == 'lfs' and effective != 'lfs') or (mode == 'git' and effective in ('lfs', None)):
            raise GitError(f"The requested {mode.upper()} tracking rule did not become effective for {_display_path(candidate['path'])!r}; a higher-precedence .gitattributes rule may conflict with it.")

    for candidate, _, _ in planned:
        _verify_candidate_unchanged(root, candidate)

    stage_arguments = ['add']
    if verbose:
        stage_arguments.append('--verbose')
    if all_paths:
        stage_arguments.append('--all')
    else:
        stage_arguments.append('-A')
        selected = [str(path) for path in sorted(changed_attributes)]
        stage_arguments.extend(['--', *git_pathspecs, *selected])
    with terminal_status('Staging dataset changes…', stderr=stderr, enabled=not verbose):
        _run_git(working_directory, stage_arguments, operation='stage the requested dataset paths', capture_output=not verbose, timeout=None)
    terminal_success('Dataset changes staged.', stderr=stderr)


def _initialize_lfs_policy(root, working_directory, *, stdin=None, stderr=None):
    """Create the repository's initial Git LFS policy through one interactive prompt."""

    attributes_path = root / '.gitattributes'
    if attributes_path.exists() or attributes_path.is_symlink() or not is_interactive(stdin=stdin, stderr=stderr):
        return False

    with terminal_status('Discovering file formats…', stderr=stderr):
        extensions = _discover_extensions(root, working_directory)
    extension_counts = {extension: details['count'] for extension, details in extensions.items()}
    try:
        selected = select_lfs_extensions(extension_counts, stderr=stderr) if extension_counts else []
    except KeyboardInterrupt as error:
        raise GitError('Git LFS format selection cancelled; no files were staged.') from error

    attributes_path.write_text('# Git LFS tracking policy initialized by Niyān.\n')
    selected_patterns = sorted({pattern for extension in selected for pattern in extensions[extension]['patterns']})
    if selected_patterns:
        _require_git_lfs()
        configure_lfs_transfer(working_directory, install_filters=True)
        for pattern in selected_patterns:
            _run_git(root, ['lfs', 'track', '--', f'*{pattern}'], operation=f'configure Git LFS for {pattern!r}')
    return True


def _discover_extensions(root, working_directory):
    """Use Git's ignore rules to count repository file formats without opening files."""

    result = _run_git(working_directory, ['ls-files', '--cached', '--others', '--exclude-standard', '--deduplicate', '-z'], operation='discover repository file formats', timeout=None)
    extensions = {}
    for encoded_path in result.stdout.split(b'\0'):
        if not encoded_path:
            continue
        path = os.fsdecode(encoded_path)
        relative = Path(path)
        if relative.is_absolute() or '..' in relative.parts or relative.name == '.gitattributes':
            continue
        try:
            metadata = (root / relative).lstat()
        except OSError:
            continue
        if not stat.S_ISREG(metadata.st_mode):
            continue
        observed = _file_extension(relative.name)
        if observed is None:
            continue
        normalized = observed.casefold()
        details = extensions.setdefault(normalized, {'count': 0, 'patterns': set()})
        details['count'] += 1
        details['patterns'].add(observed)
    return extensions


def _file_extension(filename):
    """Return a normalized format suffix, preserving compressed compound formats."""

    suffixes = Path(filename).suffixes
    if not suffixes:
        return None
    extension = f'{suffixes[-2]}{suffixes[-1]}' if len(suffixes) > 1 and suffixes[-1].casefold() in COMPRESSION_SUFFIXES else suffixes[-1]
    return extension if SAFE_EXTENSION_PATTERN.fullmatch(extension) else None


def _git_pathspecs(working_directory, paths):
    """Treat an existing explicit filesystem path literally while preserving deliberate pathspecs."""

    encoded = []
    for path in paths:
        if path.startswith(':'):
            encoded.append(path)
            continue
        candidate = Path(path)
        candidate = candidate if candidate.is_absolute() else working_directory / candidate
        try:
            candidate.lstat()
        except (FileNotFoundError, OSError):
            tracked = _run_git(working_directory, ['ls-files', '--error-unmatch', '--', f':(literal){path}'], operation=f'inspect pathspec {_display_path(path)!r}', accepted_statuses={0, 1})
            encoded.append(f':(literal){path}' if tracked.returncode == 0 else path)
        else:
            encoded.append(f':(literal){path}')
    return encoded


def _inspect_candidate(root, entry):
    """Inspect one Git-selected path without following symlinks."""

    path = entry['path']
    relative = Path(path)
    if relative.is_absolute() or '..' in relative.parts:
        raise GitError('Git returned an unsafe path while preparing dataset content.')
    candidate = root / relative
    try:
        metadata = candidate.lstat()
    except FileNotFoundError:
        return {**entry, 'kind': 'deleted', 'size': 0, 'fingerprint': None}
    except OSError as error:
        raise GitError(f'Could not inspect {_display_path(path)!r} before staging.') from error

    file_type = stat.S_IFMT(metadata.st_mode)
    if file_type == stat.S_IFLNK:
        kind = 'symlink'
    elif file_type == stat.S_IFREG:
        kind = 'regular'
    elif file_type == stat.S_IFDIR:
        if (candidate / '.git').exists() or (candidate / '.git').is_symlink():
            raise GitError(f'Nested Git repository {_display_path(path)!r} cannot be added to a Niyān dataset.')
        raise GitError(f'Directory {_display_path(path)!r} could not be expanded into supported dataset files.')
    else:
        raise GitError(f'Unsupported filesystem entry {_display_path(path)!r}; only regular files and symlinks can be added.')
    fingerprint = (metadata.st_mode, metadata.st_size, metadata.st_mtime_ns, metadata.st_ino)
    return {**entry, 'kind': kind, 'size': metadata.st_size, 'fingerprint': fingerprint}


def _infer_unambiguous_renames(root, candidates):
    """Preserve storage for exact one-to-one filesystem moves Git can identify safely."""

    deleted = [candidate for candidate in candidates if candidate['kind'] == 'deleted']
    additions = [candidate for candidate in candidates if candidate['kind'] == 'regular' and _existing_storage_mode(root, candidate['path']) is None]
    if not deleted or not additions:
        return candidates

    ordinary_objects = {}
    pointer_objects = {}
    lfs_objects_by_size = {}
    for candidate in deleted:
        metadata = _existing_object_metadata(root, candidate['path'])
        if metadata is None:
            continue
        object_id, content = metadata
        pointer = _parse_lfs_pointer_details(content) if content is not None else None
        if pointer is None:
            ordinary_objects.setdefault(object_id, []).append(candidate['path'])
        else:
            pointer_objects.setdefault(object_id, []).append(candidate['path'])
            lfs_objects_by_size.setdefault(pointer[1], []).append((pointer[0], candidate['path']))

    for candidate in additions:
        object_result = _run_git(root, ['hash-object', '--no-filters', '--', candidate['path']], operation=f'identify a possible rename for {_display_path(candidate["path"])!r}', timeout=None)
        object_id = os.fsdecode(object_result.stdout).strip()
        matches = [(path, 'git') for path in ordinary_objects.get(object_id, [])]
        matches.extend((path, 'lfs') for path in pointer_objects.get(object_id, []))
        possible_lfs = lfs_objects_by_size.get(candidate['size'], [])
        if possible_lfs:
            digest = hashlib.sha256()
            try:
                with (root / candidate['path']).open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
            except OSError as error:
                raise GitError(f'Could not inspect {_display_path(candidate["path"])!r} for rename preservation.') from error
            matches.extend((path, 'lfs') for expected, path in possible_lfs if expected == digest.hexdigest())
        unique = {(path, mode) for path, mode in matches}
        if len(unique) == 1:
            candidate['original_path'] = next(iter(unique))[0]
    return candidates


def _explicit_directories(root, working_directory, paths):
    """Return literal directory arguments as repository-relative paths."""

    directories = []
    for path in paths:
        if path.startswith(':'):
            continue
        candidate = Path(path)
        candidate = candidate if candidate.is_absolute() else working_directory / candidate
        try:
            metadata = candidate.lstat()
        except (FileNotFoundError, OSError):
            continue
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            continue
        try:
            relative = candidate.resolve().relative_to(root.resolve())
        except ValueError as error:
            raise GitError(f'Path {path!r} is outside the dataset checkout.') from error
        directories.append(relative)
    return directories


def _storage_mode(root, candidate, *, explicit_directories, force_lfs, force_git, all_paths):
    """Choose storage mode, decision source, and any manually changed attribute file."""

    path = candidate['path']
    if candidate['kind'] in ('deleted', 'symlink'):
        return 'git', candidate['kind'], None
    within_explicit_directory = any(directory == Path('.') or Path(path).is_relative_to(directory) for directory in explicit_directories)
    if Path(path).name == '.gitattributes':
        if force_lfs and not all_paths and not within_explicit_directory:
            raise GitError('.gitattributes must remain an ordinary Git blob and cannot be selected with --lfs.')
        if force_lfs or force_git:
            return 'git', 'control-file', _write_attribute_rule(root, Path(path), 'git')
        if _effective_filter(root, path) == 'lfs':
            raise GitError(f'{_display_path(path)!r} is incorrectly covered by a Git LFS rule; .gitattributes must remain an ordinary Git blob.')
        return 'git', 'control-file', None

    if force_lfs:
        return 'lfs', 'explicit-directory' if within_explicit_directory else 'explicit-file', None
    if force_git:
        if within_explicit_directory:
            return 'git', 'explicit-directory', None
        return 'git', 'explicit-file', _write_attribute_rule(root, Path(path), 'git')

    effective_filter = _effective_filter(root, path)
    if effective_filter == 'lfs':
        return 'lfs', 'attributes', None
    if effective_filter is not None:
        return 'git', 'attributes', None

    existing = _existing_storage_mode(root, path)
    if existing is None and candidate.get('original_path'):
        existing = _existing_storage_mode(root, candidate['original_path'])
    if existing is not None:
        return existing, 'existing', None
    if candidate['size'] > LFS_SIZE_THRESHOLD:
        return 'lfs', 'automatic', None
    try:
        with (root / path).open('rb') as stream:
            sample = stream.read(BINARY_SAMPLE_SIZE)
    except OSError as error:
        raise GitError(f'Could not read {_display_path(path)!r} for binary classification.') from error
    return ('lfs', 'automatic', None) if b'\0' in sample else ('git', 'fallback', None)


def _effective_filter(root, path):
    """Return Git's effective filter attribute, or ``None`` when unspecified."""

    result = _run_git(root, ['check-attr', '-z', 'filter', '--', path], operation=f'inspect attributes for {_display_path(path)!r}')
    fields = result.stdout.split(b'\0')
    if len(fields) < 4 or os.fsdecode(fields[0]) != path or fields[1] != b'filter':
        raise GitError(f'Git returned malformed attributes for {_display_path(path)!r}.')
    value = os.fsdecode(fields[2])
    return None if value == 'unspecified' else value


def _existing_storage_mode(root, path):
    """Preserve the indexed or committed storage mode for an existing path."""

    metadata = _existing_object_metadata(root, path)
    if metadata is None:
        return None
    _, content = metadata
    return 'lfs' if content is not None and _parse_lfs_pointer(content) is not None else 'git'


def _existing_object_metadata(root, path):
    """Return an existing path's object ID and bounded content when available."""

    for revision in (f':{path}', f'HEAD:{path}'):
        object_result = _run_git(root, ['rev-parse', '--verify', revision], operation=f'inspect existing storage for {_display_path(path)!r}', accepted_statuses={0, 128})
        if object_result.returncode != 0:
            continue
        object_id = os.fsdecode(object_result.stdout).strip()
        size_result = _run_git(root, ['cat-file', '-s', object_id], operation=f'inspect existing storage for {_display_path(path)!r}')
        try:
            size = int(size_result.stdout.strip())
        except ValueError as error:
            raise GitError(f'Git returned malformed object metadata for {_display_path(path)!r}.') from error
        if size > 4096:
            return object_id, None
        content = _run_git(root, ['cat-file', 'blob', object_id], operation=f'inspect existing storage for {_display_path(path)!r}').stdout
        return object_id, content
    return None


def _require_git_lfs():
    """Require stock Git LFS only when selected content needs its filters."""

    if shutil.which('git-lfs') is None:
        raise GitError('Git LFS is required for the selected dataset files. Install Git LFS or use --git explicitly.')


def _track_lfs_filename(root, path):
    """Persist one literal filename through stock Git LFS."""

    _run_git(root, ['lfs', 'track', '--filename', path], operation=f'persist Git LFS tracking for {_display_path(path)!r}')


def _write_attribute_rule(root, path, mode, *, recursive=False):
    """Append one narrowly scoped standard Git attribute rule without reordering user lines."""

    attributes_path = root / '.gitattributes'
    pattern = _attribute_pattern(path, recursive=recursive)
    attributes = 'filter=lfs diff=lfs merge=lfs -text' if mode == 'lfs' else '-filter -diff -merge'
    line = f'{pattern} {attributes}'
    try:
        existing = attributes_path.read_text() if attributes_path.exists() else ''
        if line not in existing.splitlines():
            prefix = '' if not existing or existing.endswith('\n') else '\n'
            with attributes_path.open('a') as stream:
                stream.write(f'{prefix}{line}\n')
    except OSError as error:
        raise GitError(f'Could not update {attributes_path.name!r} for the requested storage mode.') from error
    return attributes_path


def _attribute_pattern(path, *, recursive=False):
    """Encode a literal root-relative path as one Git attribute pattern."""

    value = Path(path).as_posix()
    if value in ('', '.'):
        value = '**' if recursive else value
    else:
        value = ''.join(f'\\{character}' if character in '\\*?[]' else character for character in value)
        if value.startswith(('!', '#')):
            value = f'\\{value}'
        if recursive:
            value = f'{value}/**'
    return json.dumps(value, ensure_ascii=False)


def _verify_candidate_unchanged(root, candidate):
    """Reject a path that changed type or metadata during classification."""

    if candidate['fingerprint'] is None:
        if (root / candidate['path']).exists() or (root / candidate['path']).is_symlink():
            raise GitError(f'{_display_path(candidate["path"])!r} changed while Niyān was preparing it for staging.')
        return
    try:
        metadata = (root / candidate['path']).lstat()
    except OSError as error:
        raise GitError(f'{_display_path(candidate["path"])!r} changed while Niyān was preparing it for staging.') from error
    fingerprint = (metadata.st_mode, metadata.st_size, metadata.st_mtime_ns, metadata.st_ino)
    if fingerprint != candidate['fingerprint']:
        raise GitError(f'{_display_path(candidate["path"])!r} changed while Niyān was preparing it for staging.')


def show_diff(*, staged=False, cwd=None, stdout=None):
    """Stream an unstaged or staged Git diff for a validated checkout."""

    working_directory = Path(cwd or Path.cwd())
    _require_checkout(working_directory)
    arguments = ['diff']
    if staged:
        arguments.append('--cached')
    _run_git(working_directory, arguments, operation='render the dataset diff', capture_output=False, stdout=stdout)


def restore_paths(paths, *, staged=False, cwd=None):
    """Restore working-tree paths or remove them from the index."""

    working_directory = Path(cwd or Path.cwd())
    _require_checkout(working_directory)
    if not paths:
        raise GitError('At least one path is required to restore dataset content.')
    arguments = ['restore']
    if staged:
        arguments.append('--staged')
    arguments.extend(['--', *paths])
    _run_git(working_directory, arguments, operation='restore the requested dataset paths')


def commit_changes(*, message=None, cwd=None, stdout=None, stderr=None):
    """Create one local Git commit from the staged dataset changes."""

    working_directory = Path(cwd or Path.cwd())
    _require_checkout(working_directory)
    staged_result = _run_git(working_directory, ['diff', '--cached', '--quiet', '--exit-code'], operation='inspect staged dataset changes', accepted_statuses={0, 1})
    if staged_result.returncode == 0:
        raise GitError('There are no staged dataset changes to commit.')
    arguments = ['commit']
    if message is not None:
        arguments.extend(['--message', message])
    _run_git(working_directory, arguments, operation='create the dataset commit', capture_output=False, stdout=stdout, stderr=stderr)


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


def list_branches(*, cwd=None, stdout=None):
    """List local branches, their current marker, and configured upstream."""

    output = stdout or sys.stdout
    working_directory = Path(cwd or Path.cwd())
    _require_checkout(working_directory)
    result = _run_git(working_directory, ['for-each-ref', '--sort=refname', '--format=%(if)%(HEAD)%(then)*%(else) %(end)%09%(refname:short)%09%(upstream:short)', 'refs/heads'], operation='list local branches')
    rendered = os.fsdecode(result.stdout)
    if rendered:
        print(rendered, end='' if rendered.endswith('\n') else '\n', file=output)


def create_branch(name, *, start_point=None, cwd=None):
    """Create one local branch without switching to it."""

    working_directory = Path(cwd or Path.cwd())
    _require_checkout(working_directory)
    arguments = ['branch', '--', name]
    if start_point is not None:
        arguments.append(start_point)
    _run_git(working_directory, arguments, operation=f'create branch {name!r}')


def rename_branch(old_name, new_name, *, cwd=None):
    """Rename one local branch through Git's ordinary move operation."""

    working_directory = Path(cwd or Path.cwd())
    _require_checkout(working_directory)
    _run_git(working_directory, ['branch', '--move', old_name, new_name], operation=f'rename branch {old_name!r}')


def delete_branch(name, *, cwd=None, stdin=None, stdout=None):
    """Safely delete a merged local branch after interactive confirmation."""

    working_directory = Path(cwd or Path.cwd())
    _require_checkout(working_directory)
    _confirm_interactive_deletion(kind='branch', name=name, stdin=stdin, stdout=stdout)
    _run_git(working_directory, ['branch', '--delete', '--', name], operation=f'delete branch {name!r}')


def switch_branch(name, *, create=False, start_point=None, cwd=None, stdout=None, stderr=None):
    """Switch to an existing branch or create and switch to a new branch."""

    working_directory = Path(cwd or Path.cwd())
    _require_checkout(working_directory)
    arguments = ['switch']
    if create:
        arguments.extend(['--create', name])
        if start_point is not None:
            arguments.append(start_point)
    else:
        if start_point is not None:
            raise GitError('A start point may only be used with --create.')
        arguments.extend(['--', name])
    _run_git(working_directory, arguments, operation=f'switch to branch {name!r}', capture_output=False, stdout=stdout, stderr=stderr)


def merge_revision(revision, *, cwd=None, stdout=None, stderr=None):
    """Merge one Git revision and report unresolved paths without altering them."""

    working_directory = Path(cwd or Path.cwd())
    _require_checkout(working_directory)
    result = _run_git(working_directory, ['merge', '--no-edit', '--', revision], operation=f'merge {revision!r}', accepted_statuses={0, 1, 128}, capture_output=False, stdout=stdout, stderr=stderr, timeout=None)
    if result.returncode == 0:
        return
    conflicts = _run_git(working_directory, ['diff', '--name-only', '--diff-filter=U', '-z'], operation='inspect merge conflicts').stdout
    paths = [os.fsdecode(path) for path in conflicts.split(b'\0') if path]
    if paths:
        displayed = ', '.join(_display_path(path) for path in paths)
        raise GitConflictError(f'Merge stopped with whole-file conflicts in: {displayed}. Resolve them, stage the chosen files, and commit the merge.')
    raise GitError(f'Git could not merge {revision!r}.')


def list_tags(*, cwd=None, stdout=None):
    """List local tag names in deterministic order."""

    output = stdout or sys.stdout
    working_directory = Path(cwd or Path.cwd())
    _require_checkout(working_directory)
    result = _run_git(working_directory, ['tag', '--list', '--sort=refname'], operation='list local tags')
    rendered = os.fsdecode(result.stdout)
    if rendered:
        print(rendered, end='' if rendered.endswith('\n') else '\n', file=output)


def create_tag(name, *, message=None, cwd=None, stdout=None, stderr=None):
    """Create an annotated tag at the current commit."""

    working_directory = Path(cwd or Path.cwd())
    _require_checkout(working_directory)
    arguments = ['tag', '--annotate']
    if message is not None:
        arguments.extend(['--message', message])
    arguments.extend(['--', name])
    _run_git(working_directory, arguments, operation=f'create tag {name!r}', capture_output=False, stdout=stdout, stderr=stderr)


def delete_tag(name, *, cwd=None, stdin=None, stdout=None):
    """Delete one local tag after interactive confirmation."""

    working_directory = Path(cwd or Path.cwd())
    _require_checkout(working_directory)
    _confirm_interactive_deletion(kind='tag', name=name, stdin=stdin, stdout=stdout)
    _run_git(working_directory, ['tag', '--delete', '--', name], operation=f'delete tag {name!r}')


def _confirm_interactive_deletion(*, kind, name, stdin=None, stdout=None):
    """Require an exact-name confirmation only when standard input is a terminal."""

    input_stream = stdin or sys.stdin
    try:
        interactive = input_stream.isatty()
    except (AttributeError, OSError):
        interactive = False
    if not interactive:
        return
    output = stdout or sys.stdout
    print(f'Type {name} to delete local {kind}: ', end='', flush=True, file=output)
    if input_stream.readline().strip() != name:
        raise GitError(f'{kind.capitalize()} deletion cancelled.')


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

    details = _parse_lfs_pointer_details(content)
    return details[0] if details is not None else None


def _parse_lfs_pointer_details(content):
    """Return the identifier and size from a canonical Git LFS pointer."""

    lines = content.rstrip(b'\n').splitlines()
    if len(lines) < 3 or lines[0] != LFS_POINTER_VERSION:
        return None
    object_id_match = LFS_OBJECT_ID_PATTERN.fullmatch(lines[1])
    if object_id_match is None or not lines[2].startswith(b'size ') or not lines[2][5:].isdigit():
        return None
    return object_id_match.group(1).decode('ascii'), int(lines[2][5:])


def _run_git(cwd, arguments, *, operation, accepted_statuses=frozenset({0}), capture_output=True, stdout=None, stderr=None, timeout=60):
    """Run one bounded Git command without a shell and sanitize failures."""

    if shutil.which('git') is None:
        raise GitError('Git is required for Niyān working-copy commands.')
    command = ['git', '-C', str(cwd), *arguments]
    try:
        if capture_output:
            result = subprocess.run(command, check=False, capture_output=True, timeout=timeout)
        else:
            result = subprocess.run(command, check=False, stdout=stdout, stderr=stderr)
    except (OSError, subprocess.SubprocessError) as error:
        raise GitError(f'Git could not {operation}.') from error
    if result.returncode not in accepted_statuses:
        raise GitError(f'Git could not {operation}.')
    return result
