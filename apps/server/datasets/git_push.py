import os
import re
import subprocess
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.authentication import access_token_permits
from datasets.models import Dataset, GitPushContext, GitPushLfsLease, GitPushRef, LfsObject, ProtectedRefRule
from datasets.policies import ROLE_LEVELS, get_dataset_role, required_protected_ref_role
from datasets.repositories import GitRepositoryStore, RepositoryReadError
from namespaces.models import NamespaceMembership


HEX_OID = re.compile(r'^[0-9a-f]+$')
POINTER_KEY = re.compile(r'^[a-z0-9.-]+$')
POINTER_OID = re.compile(r'^sha256:([0-9a-f]{64})$')
POINTER_VERSION = 'https://git-lfs.github.com/spec/v1'
MAX_POINTER_SIZE = 1023
MAX_LFS_SIZE = (1 << 63) - 1


class GitPushDenied(RuntimeError):
    """Report a bounded policy refusal safe to return through Git sideband."""


class GitPushUnavailable(RuntimeError):
    """Report that receive policy could not be evaluated safely."""


@dataclass(frozen=True)
class RefUpdate:
    """Represent one expected-old-object ref proposal supplied by receive-pack."""

    old_oid: str
    new_oid: str
    ref_name: str


def create_push_context(*, dataset, access_token):
    """Create one short-lived, single-use authorization handoff for receive-pack.

    Parameters
    ----------
    dataset : datasets.models.Dataset
        Authorized dataset selected by the immutable request route.
    access_token : accounts.models.AccessToken
        Active token used for the smart-HTTP request.

    Returns
    -------
    datasets.models.GitPushContext
        Opaque context whose identifier may be passed to the trusted hook.
    """

    role = get_dataset_role(user=access_token.user, dataset=dataset)
    if role is None or ROLE_LEVELS[role] < ROLE_LEVELS[NamespaceMembership.Role.CONTRIBUTOR]:
        raise GitPushDenied('The credential does not permit this repository operation.')
    return GitPushContext.objects.create(
        dataset=dataset,
        user=access_token.user,
        access_token=access_token,
        role=role,
        expires_at=timezone.now() + timedelta(seconds=settings.NIYAN_GIT_PUSH_CONTEXT_LIFETIME_SECONDS),
    )


def enforce_pre_receive(*, context_id, dataset_id, lines, repository_path=None):
    """Validate one complete receive proposal and lease every required LFS object.

    Parameters
    ----------
    context_id : uuid.UUID or str
        Opaque context created immediately before receive-pack.
    dataset_id : uuid.UUID or str
        Trusted immutable dataset identity supplied by the HTTP adapter.
    lines : iterable[str]
        Pre-receive input lines in ``old new ref`` form.
    repository_path : pathlib.Path, optional
        Explicit bare repository path used by tests.

    Returns
    -------
    list[RefUpdate]
        Validated complete proposal set.
    """

    normalized_dataset_id = UUID(str(dataset_id))
    repository = _trusted_repository_path(normalized_dataset_id, repository_path)
    context = _consume_context(context_id=context_id, dataset_id=normalized_dataset_id)
    oid_length = _object_id_length(repository)
    updates = _parse_updates(lines, oid_length=oid_length)
    rules = list(ProtectedRefRule.objects.filter(dataset_id=normalized_dataset_id).only('kind', 'pattern', 'minimum_role', 'deletion_minimum_role'))
    _enforce_ref_policy(repository=repository, updates=updates, role=context.role, oid_length=oid_length, rules=rules)
    pointers = _find_new_lfs_pointers(repository=repository, updates=updates)
    _record_validation(context_id=context.id, updates=updates, pointers=pointers)
    return updates


def record_post_receive(*, context_id, dataset_id, lines, repository_path=None):
    """Promote LFS objects and record refs Git reports as accepted.

    Parameters
    ----------
    context_id : uuid.UUID or str
        Opaque context previously consumed by pre-receive.
    dataset_id : uuid.UUID or str
        Trusted immutable dataset identity.
    lines : iterable[str]
        Post-receive accepted updates in ``old new ref`` form.
    repository_path : pathlib.Path, optional
        Explicit bare repository path used by tests.
    """

    normalized_dataset_id = UUID(str(dataset_id))
    repository = _trusted_repository_path(normalized_dataset_id, repository_path)
    updates = _parse_updates(lines, oid_length=_object_id_length(repository))
    _accept_updates(context_id=context_id, dataset_id=normalized_dataset_id, updates=updates, complete=True)


def reconcile_git_pushes(*, now=None, batch_size=100):
    """Replay missed post-receive work from current authoritative Git refs.

    Parameters
    ----------
    now : datetime.datetime, optional
        Clock override used by deterministic maintenance tests.
    batch_size : int, optional
        Maximum incomplete contexts considered in this pass.

    Returns
    -------
    int
        Number of ref proposals reconciled as reachable.
    """

    current_time = now or timezone.now()
    context_ids = list(
        GitPushContext.objects.filter(validated_at__isnull=False, completed_at__isnull=True, expires_at__lte=current_time)
        .order_by('expires_at', 'created_at')
        .values_list('id', flat=True)[:batch_size]
    )
    accepted_count = 0
    for context_id in context_ids:
        context = GitPushContext.objects.filter(pk=context_id).select_related('dataset').first()
        if context is None:
            continue
        try:
            repository = GitRepositoryStore().existing_path(context.dataset_id)
        except RepositoryReadError:
            continue
        accepted = []
        pending_refs = context.ref_updates.filter(accepted_at__isnull=True)
        for push_ref in pending_refs:
            if _ref_contains_proposal(repository, push_ref):
                accepted.append(RefUpdate(old_oid=push_ref.old_oid, new_oid=push_ref.new_oid, ref_name=push_ref.ref_name))
        if accepted:
            _accept_updates(context_id=context.id, dataset_id=context.dataset_id, updates=accepted, complete=False)
            accepted_count += len(accepted)
        GitPushContext.objects.filter(pk=context.id, completed_at__isnull=True).update(completed_at=current_time)
    return accepted_count


def parse_lfs_pointer(content):
    """Return a canonical Git LFS object's identifier and size, if present.

    Parameters
    ----------
    content : bytes
        Complete candidate Git blob smaller than the pointer limit.

    Returns
    -------
    tuple[str, int] or None
        SHA-256 object identifier and declared byte size for a canonical pointer.
    """

    if not content or len(content) > MAX_POINTER_SIZE or not content.endswith(b'\n'):
        return None
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError:
        return None
    fields = []
    for line in text[:-1].split('\n'):
        key, separator, value = line.partition(' ')
        if separator != ' ' or not key or not value or not POINTER_KEY.fullmatch(key):
            return None
        fields.append((key, value))
    if not fields or fields[0] != ('version', POINTER_VERSION):
        return None
    remaining_keys = [key for key, _ in fields[1:]]
    if 'version' in remaining_keys or remaining_keys != sorted(remaining_keys) or len(remaining_keys) != len(set(remaining_keys)):
        return None
    values = dict(fields)
    oid_match = POINTER_OID.fullmatch(values.get('oid', ''))
    size_text = values.get('size', '')
    if oid_match is None or not size_text.isascii() or not size_text.isdecimal():
        return None
    size = int(size_text)
    if str(size) != size_text or size > MAX_LFS_SIZE:
        return None
    return oid_match.group(1), size


def _trusted_repository_path(dataset_id, explicit_path):
    """Resolve and verify the bare repository selected by trusted route state."""

    try:
        expected = GitRepositoryStore().existing_path(dataset_id)
    except RepositoryReadError as error:
        raise GitPushUnavailable('Repository policy is temporarily unavailable.') from error
    if explicit_path is not None and Path(explicit_path).resolve() != expected:
        raise GitPushUnavailable('Repository policy is temporarily unavailable.')
    if explicit_path is None and Path.cwd().resolve() != expected:
        raise GitPushUnavailable('Repository policy is temporarily unavailable.')
    return expected


def _consume_context(*, context_id, dataset_id):
    """Atomically consume and reauthorize one opaque push context."""

    now = timezone.now()
    with transaction.atomic():
        context = (
            GitPushContext.objects.select_for_update()
            .select_related('dataset__namespace', 'user', 'access_token')
            .filter(pk=context_id, dataset_id=dataset_id)
            .first()
        )
        if context is None or context.consumed_at is not None or context.expires_at <= now:
            raise GitPushDenied('Push authorization is unavailable or expired. Retry niyan push.')
        _require_current_authorization(context, now=now)
        context.role = get_dataset_role(user=context.user, dataset=context.dataset)
        context.consumed_at = now
        context.save(update_fields=['role', 'consumed_at'])
        return context


def _require_current_authorization(context, *, now):
    """Fail closed when token, account, dataset, boundary, or role changed."""

    token = context.access_token
    role = get_dataset_role(user=context.user, dataset=context.dataset)
    if (
        context.dataset.deletion_started_at is not None
        or not context.user.is_active
        or not token.is_active(at=now)
        or not access_token_permits(access_token=token, scope='write_repository', dataset_id=context.dataset_id)
        or role is None
        or ROLE_LEVELS[role] < ROLE_LEVELS[NamespaceMembership.Role.CONTRIBUTOR]
    ):
        raise GitPushDenied('Push authorization changed or no longer permits this operation.')


def _object_id_length(repository):
    """Return the repository's configured object identifier width."""

    object_format = _git_output(repository, ['rev-parse', '--show-object-format'])
    if object_format == 'sha1':
        return 40
    if object_format == 'sha256':
        return 64
    raise GitPushUnavailable('Repository policy is temporarily unavailable.')


def _parse_updates(lines, *, oid_length):
    """Parse bounded Git hook records without trusting names or identifiers."""

    updates = []
    seen_refs = set()
    for raw_line in lines:
        line = raw_line.rstrip('\n')
        if len(line) > 4096 or '\x00' in line:
            raise GitPushDenied('The push contains a malformed ref update.')
        parts = line.split(' ')
        if len(parts) != 3:
            raise GitPushDenied('The push contains a malformed ref update.')
        old_oid, new_oid, ref_name = parts
        if not _valid_oid(old_oid, oid_length) or not _valid_oid(new_oid, oid_length):
            raise GitPushDenied('The push contains a malformed object identifier.')
        if ref_name in seen_refs:
            raise GitPushDenied('The push proposes the same ref more than once.')
        seen_refs.add(ref_name)
        updates.append(RefUpdate(old_oid=old_oid, new_oid=new_oid, ref_name=ref_name))
    if not updates:
        raise GitPushDenied('The push contains no ref updates.')
    return updates


def _valid_oid(value, length):
    """Return whether one hook-supplied object identifier is canonical."""

    return len(value) == length and HEX_OID.fullmatch(value) is not None


def _enforce_ref_policy(*, repository, updates, role, oid_length, rules=()):
    """Apply the accepted namespace, type, role, deletion, and fast-forward rules."""

    zero = '0' * oid_length
    for update in updates:
        checked = _run_git(repository, ['check-ref-format', update.ref_name])
        if checked.returncode != 0:
            raise GitPushDenied('The push contains an invalid ref name.')
        created = update.old_oid == zero
        deleted = update.new_oid == zero
        if update.ref_name.startswith('refs/heads/'):
            kind = ProtectedRefRule.Kind.BRANCH
            short_name = update.ref_name.removeprefix('refs/heads/')
            if deleted:
                if update.ref_name == 'refs/heads/main':
                    raise GitPushDenied('The default branch cannot be deleted.')
                _enforce_protected_ref_rules(rules=rules, kind=kind, short_name=short_name, role=role, deleting=True)
                continue
            if _git_output(repository, ['cat-file', '-t', update.new_oid]) != 'commit':
                raise GitPushDenied(f'{update.ref_name} must point to a commit.')
            if not created and _run_git(repository, ['merge-base', '--is-ancestor', update.old_oid, update.new_oid]).returncode != 0:
                raise GitPushDenied(f'{update.ref_name} is not a fast-forward update. Pull and resolve the divergence before retrying.')
            _enforce_protected_ref_rules(rules=rules, kind=kind, short_name=short_name, role=role, deleting=False)
        elif update.ref_name.startswith('refs/tags/'):
            kind = ProtectedRefRule.Kind.TAG
            short_name = update.ref_name.removeprefix('refs/tags/')
            if deleted:
                if ROLE_LEVELS[role] < ROLE_LEVELS[NamespaceMembership.Role.MAINTAINER]:
                    raise GitPushDenied(f'{update.ref_name} may only be deleted by a maintainer or owner.')
                _enforce_protected_ref_rules(rules=rules, kind=kind, short_name=short_name, role=role, deleting=True)
                continue
            if not created:
                raise GitPushDenied(f'{update.ref_name} already exists and tags cannot be updated in place.')
            if _run_git(repository, ['rev-parse', '--verify', '--quiet', f'{update.new_oid}^{{commit}}']).returncode != 0:
                raise GitPushDenied(f'{update.ref_name} must ultimately point to a commit.')
            _enforce_protected_ref_rules(rules=rules, kind=kind, short_name=short_name, role=role, deleting=False)
        else:
            raise GitPushDenied(f'{update.ref_name} is outside the supported branch and tag namespaces.')


def _enforce_protected_ref_rules(*, rules, kind, short_name, role, deleting):
    """Apply the strictest matching optional rule after immutable baseline policy."""

    required_role = required_protected_ref_role(rules=rules, kind=kind, short_name=short_name, deleting=deleting)
    if required_role is None:
        return
    if ROLE_LEVELS[role] < ROLE_LEVELS[required_role]:
        operation = 'deletion' if deleting else 'creation or update'
        raise GitPushDenied(f'{kind} {short_name} requires the {required_role} role for {operation}.')


def _find_new_lfs_pointers(*, repository, updates):
    """Scan every newly reachable object and map canonical pointers to affected refs."""

    pointers = {}
    inspected = {}
    with _GitBatchReader(repository) as objects:
        for update in updates:
            if set(update.new_oid) == {'0'}:
                continue
            for oid in _iter_new_object_ids(repository, update.new_oid):
                if oid not in inspected:
                    object_type, size = objects.inspect(oid)
                    inspected[oid] = None if object_type != 'blob' or size == 0 or size > MAX_POINTER_SIZE else parse_lfs_pointer(objects.read(oid, size))
                pointer = inspected[oid]
                if pointer is None:
                    continue
                existing = next((key for key in pointers if key[0] == pointer[0]), None)
                if existing is not None and existing[1] != pointer[1]:
                    raise GitPushDenied(f'{update.ref_name} contains inconsistent sizes for Git LFS object {pointer[0]}.')
                pointers.setdefault(pointer, set()).add(update.ref_name)
    return pointers


def _iter_new_object_ids(repository, new_oid):
    """Yield objects made reachable by one proposal but not by existing refs."""

    try:
        process = subprocess.Popen(
            ['git', 'rev-list', '--objects', '--no-object-names', new_oid, '--not', '--all'],
            cwd=repository,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError as error:
        raise GitPushUnavailable('Repository policy is temporarily unavailable.') from error
    try:
        for line in process.stdout:
            oid = line.strip()
            if not HEX_OID.fullmatch(oid):
                raise GitPushUnavailable('Repository policy is temporarily unavailable.')
            yield oid
    finally:
        process.stdout.close()
        return_code = process.wait()
    if return_code != 0:
        raise GitPushUnavailable('Repository policy is temporarily unavailable.')


class _GitBatchReader:
    """Read Git object metadata and small blob content through persistent plumbing."""

    def __init__(self, repository):
        """Remember the trusted bare repository path."""

        self.repository = repository
        self.checker = None
        self.reader = None

    def __enter__(self):
        """Start metadata and content batch processes."""

        environment = os.environ.copy()
        try:
            self.checker = subprocess.Popen(
                ['git', 'cat-file', '--batch-check=%(objectname) %(objecttype) %(objectsize)'],
                cwd=self.repository,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            self.reader = subprocess.Popen(
                ['git', 'cat-file', '--batch'],
                cwd=self.repository,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as error:
            self._terminate()
            raise GitPushUnavailable('Repository policy is temporarily unavailable.') from error
        return self

    def __exit__(self, exception_type, exception, traceback):
        """Close both plumbing processes without leaking their diagnostics."""

        if exception_type is not None:
            self._terminate()
            return False
        for process in (self.checker, self.reader):
            process.stdin.close()
            if process.wait() != 0:
                raise GitPushUnavailable('Repository policy is temporarily unavailable.')
        return False

    def inspect(self, oid):
        """Return one object's type and byte size."""

        try:
            self.checker.stdin.write(f'{oid}\n')
            self.checker.stdin.flush()
            response = self.checker.stdout.readline().strip().split(' ')
            if len(response) != 3 or response[0] != oid:
                raise ValueError
            return response[1], int(response[2])
        except (BrokenPipeError, OSError, ValueError) as error:
            raise GitPushUnavailable('Repository policy is temporarily unavailable.') from error

    def read(self, oid, expected_size):
        """Return complete content for one already bounded small blob."""

        try:
            self.reader.stdin.write(f'{oid}\n'.encode())
            self.reader.stdin.flush()
            header = self.reader.stdout.readline().decode('ascii').strip().split(' ')
            if len(header) != 3 or header[0] != oid or header[1] != 'blob' or int(header[2]) != expected_size:
                raise ValueError
            content = self.reader.stdout.read(expected_size)
            if len(content) != expected_size or self.reader.stdout.read(1) != b'\n':
                raise ValueError
            return content
        except (BrokenPipeError, OSError, UnicodeDecodeError, ValueError) as error:
            raise GitPushUnavailable('Repository policy is temporarily unavailable.') from error

    def _terminate(self):
        """Terminate whichever batch processes were started."""

        for process in (self.checker, self.reader):
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait()


def _record_validation(*, context_id, updates, pointers):
    """Reauthorize, persist proposals, verify LFS state, and acquire leases atomically."""

    now = timezone.now()
    lease_expiry = now + timedelta(seconds=settings.NIYAN_GIT_PUSH_LEASE_LIFETIME_SECONDS)
    with transaction.atomic():
        context = GitPushContext.objects.select_for_update().select_related('dataset__namespace', 'user', 'access_token').get(pk=context_id)
        if context.validated_at is not None or context.consumed_at is None:
            raise GitPushDenied('Push authorization is unavailable or expired. Retry niyan push.')
        _require_current_authorization(context, now=now)
        current_role = get_dataset_role(user=context.user, dataset=context.dataset)
        if any(update.ref_name.startswith('refs/tags/') and set(update.new_oid) == {'0'} for update in updates) and ROLE_LEVELS[current_role] < ROLE_LEVELS[NamespaceMembership.Role.MAINTAINER]:
            raise GitPushDenied('Tags may only be deleted by a maintainer or owner.')

        objects_by_oid = {}
        pointer_oids = [oid for oid, _ in pointers]
        for offset in range(0, len(pointer_oids), 500):
            for lfs_object in LfsObject.objects.select_for_update().filter(dataset=context.dataset, oid__in=pointer_oids[offset : offset + 500]):
                objects_by_oid[lfs_object.oid] = lfs_object
        unavailable = []
        for oid, size in pointers:
            lfs_object = objects_by_oid.get(oid)
            if lfs_object is None or lfs_object.size != size or lfs_object.state not in {LfsObject.State.AVAILABLE, LfsObject.State.REFERENCED}:
                unavailable.append((oid, sorted(pointers[(oid, size)])[0]))
        if unavailable:
            sample = ', '.join(f'{ref_name}: {oid}' for oid, ref_name in unavailable[:5])
            suffix = '' if len(unavailable) <= 5 else f' and {len(unavailable) - 5} more'
            raise GitPushDenied(f'Required Git LFS objects are unavailable or inconsistent: {sample}{suffix}. Retry niyan push after uploading them.')

        refs = {
            update.ref_name: GitPushRef.objects.create(
                push_context=context,
                ref_name=update.ref_name,
                old_oid=update.old_oid,
                new_oid=update.new_oid,
            )
            for update in updates
        }
        leases = []
        for (oid, _), ref_names in pointers.items():
            for ref_name in ref_names:
                leases.append(GitPushLfsLease(push_ref=refs[ref_name], lfs_object=objects_by_oid[oid], expires_at=lease_expiry))
        GitPushLfsLease.objects.bulk_create(leases, batch_size=500)
        context.validated_at = now
        context.save(update_fields=['validated_at'])


def _accept_updates(*, context_id, dataset_id, updates, complete):
    """Idempotently record accepted refs and promote their leased LFS objects."""

    now = timezone.now()
    with transaction.atomic():
        context = GitPushContext.objects.select_for_update().filter(pk=context_id, dataset_id=dataset_id, validated_at__isnull=False).first()
        if context is None:
            raise GitPushUnavailable('Repository policy is temporarily unavailable.')
        accepted_refs = []
        for update in updates:
            push_ref = context.ref_updates.select_for_update().filter(ref_name=update.ref_name, old_oid=update.old_oid, new_oid=update.new_oid).first()
            if push_ref is None:
                raise GitPushUnavailable('Repository policy is temporarily unavailable.')
            if push_ref.accepted_at is None:
                push_ref.accepted_at = now
                push_ref.save(update_fields=['accepted_at'])
            accepted_refs.append(push_ref)

        lfs_ids = GitPushLfsLease.objects.filter(push_ref__in=accepted_refs).values_list('lfs_object_id', flat=True)
        lfs_objects = LfsObject.objects.select_for_update().filter(pk__in=lfs_ids)
        for lfs_object in lfs_objects:
            if lfs_object.state == LfsObject.State.AVAILABLE:
                lfs_object.state = LfsObject.State.REFERENCED
                lfs_object.referenced_at = now
                lfs_object.full_clean()
                lfs_object.save(update_fields=['state', 'referenced_at', 'updated_at'])
        if complete and context.completed_at is None:
            context.completed_at = now
            context.save(update_fields=['completed_at'])


def _ref_contains_proposal(repository, push_ref):
    """Return whether the current ref proves a missed accepted proposal is reachable."""

    zero = '0' * len(push_ref.new_oid)
    current = _run_git(repository, ['show-ref', '--verify', '--hash', push_ref.ref_name])
    if push_ref.new_oid == zero:
        return current.returncode != 0
    if current.returncode != 0:
        return False
    current_oid = current.stdout.strip()
    if current_oid == push_ref.new_oid:
        return True
    if push_ref.ref_name.startswith('refs/heads/'):
        return _run_git(repository, ['merge-base', '--is-ancestor', push_ref.new_oid, current_oid]).returncode == 0
    return False


def _run_git(repository, arguments):
    """Run trusted Git plumbing without a shell or client-visible diagnostics."""

    try:
        return subprocess.run(
            ['git', *arguments],
            cwd=repository,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GitPushUnavailable('Repository policy is temporarily unavailable.') from error


def _git_output(repository, arguments):
    """Return one successful bounded Git plumbing value."""

    result = _run_git(repository, arguments)
    if result.returncode != 0:
        raise GitPushUnavailable('Repository policy is temporarily unavailable.')
    return result.stdout.strip()
