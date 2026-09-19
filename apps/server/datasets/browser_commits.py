import os
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from datasets.models import BrowserCommitChange, BrowserCommitDraft, LfsObject, ProtectedRefRule
from datasets.policies import ROLE_LEVELS, get_dataset_role, required_protected_ref_role
from datasets.repositories import GitRepositoryStore, RepositoryReadError


MAX_GIT_BLOB_SIZE = 10 * 1024 * 1024


class BrowserCommitError(RuntimeError):
    """Base class for sanitized browser-commit failures."""


class BrowserDraftInvalid(BrowserCommitError):
    """Report malformed, expired, terminal, or inconsistent draft input."""


class BrowserDraftConflict(BrowserCommitError):
    """Report that the target branch moved after draft creation."""


class BrowserDraftForbidden(BrowserCommitError):
    """Report current authorization or protected-ref refusal."""


class BrowserCommitUnavailable(BrowserCommitError):
    """Report an unavailable Git repository or failed plumbing operation."""


def create_browser_draft(*, dataset, user, target_branch):
    """Create a creator-private draft pinned to the branch's current commit."""

    repository = _repository(dataset.id)
    branch = _validate_branch(target_branch, repository)
    ref_name = f'refs/heads/{branch}'
    current = _optional_ref(repository, ref_name)
    if current is None and _has_branches(repository):
        raise BrowserDraftInvalid('The target branch does not exist.')
    return BrowserCommitDraft.objects.create(
        dataset=dataset,
        created_by=user,
        target_branch=branch,
        base_commit=current or '',
        expires_at=timezone.now() + timedelta(seconds=settings.NIYAN_BROWSER_DRAFT_LIFETIME_SECONDS),
    )


def stage_git_blob(*, draft, path, size, stream):
    """Stream one bounded ordinary file into Git and stage its blob identity."""

    draft = _require_open_draft(draft)
    normalized_path = validate_repository_path(path)
    if size < 0 or size > MAX_GIT_BLOB_SIZE:
        raise BrowserDraftInvalid('Ordinary Git uploads must not exceed 10 MiB.')
    repository = _repository(draft.dataset_id)
    oid = _hash_stream(repository=repository, stream=stream, size=size)
    _validate_resulting_paths(draft=draft, proposed=(normalized_path, BrowserCommitChange.Operation.UPSERT))
    change, _ = BrowserCommitChange.objects.update_or_create(
        draft=draft,
        path=normalized_path,
        defaults={'operation': BrowserCommitChange.Operation.UPSERT, 'storage': BrowserCommitChange.Storage.GIT, 'size': size, 'git_blob_oid': oid, 'lfs_object': None},
    )
    return change


def stage_lfs_object(*, draft, path, oid, size):
    """Stage one dataset-scoped Git LFS identity before its direct upload."""

    draft = _require_open_draft(draft)
    normalized_path = validate_repository_path(path)
    if PurePosixPath(normalized_path).name == '.gitattributes':
        raise BrowserDraftInvalid('.gitattributes must remain an ordinary Git file.')
    if len(oid) != 64 or any(character not in '0123456789abcdef' for character in oid) or size < 0 or size >= 2**63:
        raise BrowserDraftInvalid('The Git LFS identity is invalid.')
    _validate_resulting_paths(draft=draft, proposed=(normalized_path, BrowserCommitChange.Operation.UPSERT))
    try:
        lfs_object, _ = LfsObject.objects.get_or_create(dataset=draft.dataset, oid=oid, defaults={'size': size})
    except IntegrityError:
        lfs_object = LfsObject.objects.get(dataset=draft.dataset, oid=oid)
    if lfs_object.size != size:
        raise BrowserDraftInvalid('The declared object size conflicts with existing metadata.')
    change, _ = BrowserCommitChange.objects.update_or_create(
        draft=draft,
        path=normalized_path,
        defaults={'operation': BrowserCommitChange.Operation.UPSERT, 'storage': BrowserCommitChange.Storage.LFS, 'size': size, 'git_blob_oid': '', 'lfs_object': lfs_object},
    )
    return change


def stage_delete(*, draft, path):
    """Stage deletion of one regular file, or cancel a newly staged addition."""

    draft = _require_open_draft(draft)
    normalized_path = validate_repository_path(path)
    base_entries = _base_entries(_repository(draft.dataset_id), draft.base_commit)
    current_paths = _resulting_paths(draft)
    if normalized_path not in current_paths:
        raise BrowserDraftInvalid('The selected file does not exist in the draft tree.')
    if normalized_path not in base_entries:
        BrowserCommitChange.objects.filter(draft=draft, path=normalized_path).delete()
        return None
    if base_entries[normalized_path] != 'blob':
        raise BrowserDraftInvalid('Browser drafts can delete only regular files.')
    change, _ = BrowserCommitChange.objects.update_or_create(
        draft=draft,
        path=normalized_path,
        defaults={'operation': BrowserCommitChange.Operation.DELETE, 'storage': '', 'size': None, 'git_blob_oid': '', 'lfs_object': None},
    )
    return change


def discard_browser_draft(*, draft):
    """Mark one open creator-owned draft discarded without deleting shared objects."""

    draft = _require_open_draft(draft)
    draft.state = BrowserCommitDraft.State.DISCARDED
    draft.discarded_at = timezone.now()
    draft.full_clean()
    draft.save(update_fields=['state', 'discarded_at', 'updated_at'])
    draft.changes.all().delete()
    return draft


def publish_browser_draft(*, draft, user, message):
    """Construct and atomically publish one ordinary Git commit from a draft."""

    if not message or not message.strip():
        raise BrowserDraftInvalid('A non-empty commit message is required.')
    with transaction.atomic():
        locked = BrowserCommitDraft.objects.select_for_update().select_related('dataset__namespace', 'created_by').get(pk=draft.pk)
        _require_open_draft(locked)
        role = get_dataset_role(user=user, dataset=locked.dataset)
        if role is None or ROLE_LEVELS[role] < ROLE_LEVELS['contributor']:
            raise BrowserDraftForbidden('You cannot publish this browser draft.')
        rules = list(ProtectedRefRule.objects.filter(dataset=locked.dataset, kind=ProtectedRefRule.Kind.BRANCH).only('kind', 'pattern', 'minimum_role', 'deletion_minimum_role'))
        required_role = required_protected_ref_role(rules=rules, kind=ProtectedRefRule.Kind.BRANCH, short_name=locked.target_branch, deleting=False)
        if required_role is not None and ROLE_LEVELS[role] < ROLE_LEVELS[required_role]:
            raise BrowserDraftForbidden('The target branch requires a higher dataset role.')
        changes = list(locked.changes.select_related('lfs_object').order_by('path'))
        if not changes:
            raise BrowserDraftInvalid('The browser draft has no staged changes.')
        for change in changes:
            if change.storage == BrowserCommitChange.Storage.LFS and (change.lfs_object.dataset_id != locked.dataset_id or change.lfs_object.state not in {LfsObject.State.AVAILABLE, LfsObject.State.REFERENCED}):
                raise BrowserDraftInvalid(f'Git LFS content for {change.path!r} is not available.')

        repository = _repository(locked.dataset_id)
        ref_name = f'refs/heads/{locked.target_branch}'
        expected = locked.base_commit or None
        if _optional_ref(repository, ref_name) != expected:
            raise BrowserDraftConflict('The target branch moved after this draft was created.')
        commit_oid = _construct_commit(repository=repository, draft=locked, changes=changes, user=user, message=message.strip())
        zero = '0' * _object_id_length(repository)
        updated = _run_git(repository, ['update-ref', ref_name, commit_oid, expected or zero], check=False)
        if updated.returncode != 0:
            raise BrowserDraftConflict('The target branch moved while the draft was publishing.')
        now = timezone.now()
        lfs_ids = [change.lfs_object_id for change in changes if change.lfs_object_id is not None]
        if lfs_ids:
            LfsObject.objects.filter(pk__in=lfs_ids, state=LfsObject.State.AVAILABLE).update(state=LfsObject.State.REFERENCED, referenced_at=now, updated_at=now)
        locked.state = BrowserCommitDraft.State.COMMITTED
        locked.committed_oid = commit_oid
        locked.committed_at = now
        locked.full_clean()
        locked.save(update_fields=['state', 'committed_oid', 'committed_at', 'updated_at'])
        return locked


def validate_repository_path(value):
    """Validate and return one exact repository-relative POSIX file path."""

    if not isinstance(value, str) or not value or len(value) > 4096 or value.startswith('/') or value.endswith('/') or '\x00' in value:
        raise BrowserDraftInvalid('The repository path is invalid.')
    parts = value.split('/')
    if any(part in ('', '.', '..') or part.casefold() == '.git' for part in parts):
        raise BrowserDraftInvalid('The repository path is invalid.')
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise BrowserDraftInvalid('The repository path is invalid.')
    return value


def _require_open_draft(draft):
    """Reject terminal or expired drafts."""

    if draft.state != BrowserCommitDraft.State.OPEN:
        raise BrowserDraftInvalid('The browser draft is no longer open.')
    if draft.expires_at <= timezone.now():
        raise BrowserDraftInvalid('The browser draft has expired.')
    return draft


def _repository(dataset_id):
    """Resolve one safe existing bare repository."""

    try:
        return GitRepositoryStore().existing_path(dataset_id)
    except RepositoryReadError as error:
        raise BrowserCommitUnavailable('The dataset repository is unavailable.') from error


def _validate_branch(branch, repository):
    """Validate one short branch name through Git's authoritative grammar."""

    if not isinstance(branch, str) or not branch or branch.startswith('refs/') or len(branch) > 255:
        raise BrowserDraftInvalid('The target branch is invalid.')
    result = _run_git(repository, ['check-ref-format', f'refs/heads/{branch}'], check=False)
    if result.returncode != 0:
        raise BrowserDraftInvalid('The target branch is invalid.')
    return branch


def _optional_ref(repository, ref_name):
    """Return one exact ref commit or ``None`` when it is unborn or missing."""

    result = _run_git(repository, ['rev-parse', '--verify', '--end-of-options', f'{ref_name}^{{commit}}'], check=False)
    value = result.stdout.decode().strip()
    return value if result.returncode == 0 else None


def _has_branches(repository):
    """Return whether the repository currently contains any branch."""

    return bool(_run_git(repository, ['for-each-ref', '--count=1', '--format=%(refname)', 'refs/heads']).stdout.strip())


def _hash_stream(*, repository, stream, size):
    """Write exactly one bounded request stream into the bare Git object database."""

    try:
        process = subprocess.Popen(['git', '--git-dir', str(repository), 'hash-object', '-w', '--stdin'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        remaining = size
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                process.kill()
                process.wait()
                raise BrowserDraftInvalid('The upload ended before its declared size.')
            process.stdin.write(chunk)
            remaining -= len(chunk)
        if stream.read(1):
            process.kill()
            process.wait()
            raise BrowserDraftInvalid('The upload exceeds its declared size.')
        process.stdin.close()
        oid = process.stdout.read().decode().strip()
        return_code = process.wait(timeout=30)
    except BrowserDraftInvalid:
        raise
    except (OSError, subprocess.SubprocessError) as error:
        raise BrowserCommitUnavailable('Git could not store the uploaded file.') from error
    if return_code != 0 or len(oid) not in (40, 64) or any(character not in '0123456789abcdef' for character in oid):
        raise BrowserCommitUnavailable('Git could not store the uploaded file.')
    return oid


def _base_entries(repository, base_commit):
    """Return every recursively tracked base entry and its Git object type."""

    if not base_commit:
        return {}
    result = _run_git(repository, ['ls-tree', '-r', '-z', '--full-tree', base_commit])
    entries = {}
    for record in result.stdout.split(b'\0'):
        if not record:
            continue
        metadata, encoded_path = record.split(b'\t', 1)
        _, object_type, _ = metadata.decode().split(' ', 2)
        entries[os.fsdecode(encoded_path)] = object_type
    return entries


def _resulting_paths(draft):
    """Apply current draft operations to the base tree and return file-like paths."""

    paths = set(_base_entries(_repository(draft.dataset_id), draft.base_commit))
    for change in draft.changes.only('path', 'operation'):
        if change.operation == BrowserCommitChange.Operation.DELETE:
            paths.discard(change.path)
        else:
            paths.add(change.path)
    return paths


def _validate_resulting_paths(*, draft, proposed):
    """Reject file/directory collisions after one proposed upsert."""

    path, operation = proposed
    paths = _resulting_paths(draft)
    if operation == BrowserCommitChange.Operation.DELETE:
        paths.discard(path)
    else:
        paths.add(path)
    for candidate in paths:
        parts = PurePosixPath(candidate).parts
        for index in range(1, len(parts)):
            if PurePosixPath(*parts[:index]).as_posix() in paths:
                raise BrowserDraftInvalid('The staged paths collide as both a file and directory.')


def _construct_commit(*, repository, draft, changes, user, message):
    """Build a one-parent commit through a temporary Git index."""

    index_file = tempfile.NamedTemporaryFile(prefix='niyan-browser-index-', delete=False)
    index_path = Path(index_file.name)
    index_file.close()
    index_path.unlink()
    environment = dict(os.environ)
    environment['GIT_INDEX_FILE'] = str(index_path)
    try:
        if draft.base_commit:
            _run_git(repository, ['read-tree', draft.base_commit], environment=environment)
        else:
            _run_git(repository, ['read-tree', '--empty'], environment=environment)
        lfs_paths = []
        for change in changes:
            if change.operation == BrowserCommitChange.Operation.DELETE:
                _run_git(repository, ['update-index', '--force-remove', '--', change.path], environment=environment)
                continue
            object_id = change.git_blob_oid
            if change.storage == BrowserCommitChange.Storage.LFS:
                pointer = f'version https://git-lfs.github.com/spec/v1\noid sha256:{change.lfs_object.oid}\nsize {change.size}\n'.encode()
                object_id = _hash_bytes(repository, pointer)
                lfs_paths.append(change.path)
            _run_git(repository, ['update-index', '--add', '--cacheinfo', f'100644,{object_id},{change.path}'], environment=environment)
        for attribute_path, patterns in _attribute_updates(lfs_paths).items():
            existing = _read_index_blob(repository, environment, attribute_path)
            content = existing
            if content and not content.endswith(b'\n'):
                content += b'\n'
            for pattern in patterns:
                line = f'{_quote_attribute_pattern(pattern)} filter=lfs diff=lfs merge=lfs -text\n'.encode()
                if line not in content.splitlines(keepends=True):
                    content += line
            object_id = _hash_bytes(repository, content)
            _run_git(repository, ['update-index', '--add', '--cacheinfo', f'100644,{object_id},{attribute_path}'], environment=environment)
        tree_oid = _run_git(repository, ['write-tree'], environment=environment).stdout.decode().strip()
        name = user.get_full_name().strip() or user.username
        email = user.email.strip() or f'{user.username}@users.niyan.invalid'
        environment.update({'GIT_AUTHOR_NAME': name, 'GIT_AUTHOR_EMAIL': email, 'GIT_COMMITTER_NAME': name, 'GIT_COMMITTER_EMAIL': email})
        arguments = ['commit-tree', tree_oid]
        if draft.base_commit:
            arguments.extend(['-p', draft.base_commit])
        return _run_git(repository, arguments, environment=environment, input_data=f'{message}\n'.encode()).stdout.decode().strip()
    finally:
        index_path.unlink(missing_ok=True)


def _attribute_updates(lfs_paths):
    """Group exact LFS tracking patterns by nearest attribute file."""

    updates = {}
    for path in lfs_paths:
        pure_path = PurePosixPath(path)
        attribute_path = (pure_path.parent / '.gitattributes').as_posix() if pure_path.parent != PurePosixPath('.') else '.gitattributes'
        updates.setdefault(attribute_path, []).append(pure_path.name)
    return updates


def _quote_attribute_pattern(filename):
    """Return one exact, quoted Git-attributes basename pattern."""

    escaped = filename.replace('\\', '\\\\').replace('"', '\\"')
    return f'"/{escaped}"'


def _read_index_blob(repository, environment, path):
    """Read one ordinary blob from the temporary index, or return empty bytes."""

    listed = _run_git(repository, ['ls-files', '--stage', '-z', '--', path], environment=environment).stdout
    if not listed:
        return b''
    metadata = listed.split(b'\t', 1)[0].decode().split()
    if len(metadata) < 2:
        raise BrowserCommitUnavailable('Git could not inspect the staged attribute file.')
    return _run_git(repository, ['cat-file', 'blob', metadata[1]], environment=environment).stdout


def _hash_bytes(repository, content):
    """Write one trusted small blob and return its object identifier."""

    return _run_git(repository, ['hash-object', '-w', '--stdin'], input_data=content).stdout.decode().strip()


def _object_id_length(repository):
    """Return the repository's configured object identifier width."""

    return {'sha1': 40, 'sha256': 64}.get(_run_git(repository, ['rev-parse', '--show-object-format']).stdout.decode().strip(), 40)


def _run_git(repository, arguments, *, environment=None, input_data=None, check=True):
    """Run one bounded shell-free Git plumbing command against a bare repository."""

    try:
        result = subprocess.run(['git', '--git-dir', str(repository), *arguments], input=input_data, env=environment, check=False, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        raise BrowserCommitUnavailable('The dataset repository operation failed.') from error
    if check and result.returncode != 0:
        raise BrowserCommitUnavailable('The dataset repository operation failed.')
    return result
