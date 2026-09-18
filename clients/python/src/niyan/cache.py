import os
import re
import stat
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from niyan.auth import select_credential
from niyan.errors import GitError
from niyan.git import _authenticated_git, _git_environment, _require_checkout, _require_dependency


PRUNABLE_LINE = re.compile(r'^\s*\*\s+([0-9a-f]{64})\s+\(', re.MULTILINE)
PRUNABLE_SUMMARY = re.compile(r'\b([0-9]+) files? would be pruned\b')


@dataclass(frozen=True)
class CacheReport:
    """Describe local Git LFS storage under Git LFS's own retention policy."""

    location: Path
    total_objects: int
    total_bytes: int
    protected_objects: int
    protected_bytes: int
    reclaimable_objects: int
    reclaimable_bytes: int


def show_cache_status(*, paths, stores, cwd=None, environment=None, stdout=None):
    """Inspect the current checkout's Git LFS cache without mutating it.

    Parameters
    ----------
    paths : niyan.config.AppPaths
        CLI configuration and data paths.
    stores : niyan.credentials.CredentialStores
        Credential storage adapters.
    cwd : pathlib.Path, optional
        Directory inside the Niyān checkout.
    environment : mapping, optional
        Child process environment override used by tests.
    stdout : file-like object, optional
        Human-facing report destination.

    Returns
    -------
    CacheReport
        Verified local storage summary.
    """

    report = _inspect_cache(paths=paths, stores=stores, cwd=cwd, environment=environment)
    _print_report(report, output=stdout or sys.stdout)
    return report


def prune_cache(*, paths, stores, dry_run=False, cwd=None, environment=None, stdin=None, stdout=None):
    """Preview and optionally run Git LFS's safe local pruning policy.

    Parameters
    ----------
    paths : niyan.config.AppPaths
        CLI configuration and data paths.
    stores : niyan.credentials.CredentialStores
        Credential storage adapters.
    dry_run : bool, optional
        Report verified candidates without deleting them.
    cwd : pathlib.Path, optional
        Directory inside the Niyān checkout.
    environment : mapping, optional
        Child process environment override used by tests.
    stdin : file-like object, optional
        Explicit confirmation input.
    stdout : file-like object, optional
        Human-facing report and prompt destination.

    Returns
    -------
    CacheReport
        Preview used for the pruning decision.
    """

    output = stdout or sys.stdout
    with _authenticated_cache(paths=paths, stores=stores, cwd=cwd, environment=environment) as session:
        checkout, identity, command_prefix, child_environment = session
        report = _build_cache_report(
            checkout=checkout,
            command_prefix=command_prefix,
            remote=identity.remote,
            environment=child_environment,
        )
        _print_report(report, output=output)
        if dry_run:
            print('Dry run complete. No files were deleted.', file=output)
            return report
        if report.reclaimable_objects == 0:
            print('Nothing is currently safe to prune.', file=output)
            return report
        if not _confirm_prune(report, input_stream=stdin or sys.stdin, output=output):
            print('Prune cancelled.', file=output)
            return report

        before = report.total_bytes
        result = _run_lfs_prune(
            checkout=checkout,
            command_prefix=command_prefix,
            remote=identity.remote,
            environment=child_environment,
            dry_run=False,
        )
        if result.returncode != 0:
            raise GitError('Git LFS could not safely verify and prune the local cache. No unverified object was intentionally removed.')
        after = sum(_scan_cache_objects(report.location).values())
        print(f'Prune complete. Reclaimed {_format_bytes(max(0, before - after))}.', file=output)
        return report


def _inspect_cache(*, paths, stores, cwd, environment):
    """Return the report portion of an authenticated cache inspection."""

    with _authenticated_cache(paths=paths, stores=stores, cwd=cwd, environment=environment) as session:
        checkout, identity, command_prefix, child_environment = session
        return _build_cache_report(
            checkout=checkout,
            command_prefix=command_prefix,
            remote=identity.remote,
            environment=child_environment,
        )


@contextmanager
def _authenticated_cache(*, paths, stores, cwd, environment):
    """Yield one authenticated Git LFS execution context."""

    checkout = Path(cwd or Path.cwd())
    identity = _require_checkout(checkout)
    _require_dependency('git-lfs', 'Git LFS is required to inspect or prune the local dataset cache.')
    credential = select_credential(
        host=identity.host,
        dataset_path=identity.dataset_path,
        dataset_id=identity.dataset_id,
        paths=paths,
        stores=stores,
        cwd=checkout,
        environment=environment,
    )
    child_environment = _git_environment(environment)
    child_environment['LANG'] = 'C'
    child_environment['LC_ALL'] = 'C'
    with _authenticated_git(host=identity.host, credential=credential) as command_prefix:
        # Environment-token authentication uses an ephemeral secret file whose lifetime is deliberately bounded by this context.
        yield checkout, identity, command_prefix, child_environment


def _build_cache_report(*, checkout, command_prefix, remote, environment):
    """Resolve local media, ask Git LFS for safe candidates, and total bytes."""

    environment_result = _run_lfs(
        checkout=checkout,
        command_prefix=command_prefix,
        remote=remote,
        arguments=['env'],
        environment=environment,
    )
    if environment_result.returncode != 0:
        raise GitError('Git LFS could not report the local cache location.')
    location = _parse_media_directory(environment_result.stdout, checkout=checkout)
    objects = _scan_cache_objects(location)
    preview = _run_lfs_prune(
        checkout=checkout,
        command_prefix=command_prefix,
        remote=remote,
        environment=environment,
        dry_run=True,
    )
    if preview.returncode != 0:
        raise GitError('Git LFS could not verify which local objects are safe to prune.')
    candidates = _parse_prunable_oids(f'{preview.stdout}\n{preview.stderr}')
    reclaimable = {oid: objects[oid] for oid in candidates if oid in objects}
    total_bytes = sum(objects.values())
    reclaimable_bytes = sum(reclaimable.values())
    return CacheReport(
        location=location,
        total_objects=len(objects),
        total_bytes=total_bytes,
        protected_objects=len(objects) - len(reclaimable),
        protected_bytes=total_bytes - reclaimable_bytes,
        reclaimable_objects=len(reclaimable),
        reclaimable_bytes=reclaimable_bytes,
    )


def _run_lfs_prune(*, checkout, command_prefix, remote, environment, dry_run):
    """Run Git LFS's retention engine with remote verification enabled."""

    arguments = ['prune', '--verbose', '--verify-remote', '--when-unverified=halt']
    if dry_run:
        arguments.insert(1, '--dry-run')
    return _run_lfs(checkout=checkout, command_prefix=command_prefix, remote=remote, arguments=arguments, environment=environment)


def _run_lfs(*, checkout, command_prefix, remote, arguments, environment):
    """Invoke Git LFS without a shell and retain diagnostics for bounded parsing."""

    command = [*command_prefix, '-c', f'lfs.pruneremotetocheck={remote}', '-C', str(checkout), 'lfs', *arguments]
    try:
        return subprocess.run(command, cwd=checkout, env=environment, check=False, capture_output=True, text=True)
    except OSError as error:
        raise GitError('Git LFS could not inspect the local cache.') from error


def _parse_media_directory(output, *, checkout):
    """Extract Git LFS's resolved object directory from machine-like env output."""

    values = [line.removeprefix('LocalMediaDir=') for line in output.splitlines() if line.startswith('LocalMediaDir=')]
    if len(values) != 1 or not values[0] or '\x00' in values[0]:
        raise GitError('Git LFS returned an invalid local cache location.')
    location = Path(values[0]).expanduser()
    if not location.is_absolute():
        location = checkout / location
    return location.resolve(strict=False)


def _scan_cache_objects(location):
    """Return canonical Git LFS object sizes while ignoring temporary files."""

    objects = {}
    if not location.exists():
        return objects
    try:
        for path in location.glob('*/*/*'):
            relative = path.relative_to(location)
            if len(relative.parts) != 3:
                continue
            first, second, oid = relative.parts
            if len(oid) != 64 or any(character not in '0123456789abcdef' for character in oid) or first != oid[:2] or second != oid[2:4]:
                continue
            metadata = path.lstat()
            if stat.S_ISREG(metadata.st_mode):
                objects[oid] = metadata.st_size
    except OSError as error:
        raise GitError(f'Could not inspect the Git LFS cache at {location}.') from error
    return objects


def _parse_prunable_oids(output):
    """Parse the documented verbose dry-run listing and fail closed on drift."""

    candidates = tuple(dict.fromkeys(PRUNABLE_LINE.findall(output)))
    summary = PRUNABLE_SUMMARY.search(output)
    if summary is None:
        return ()
    expected = int(summary.group(1))
    if expected != len(candidates):
        raise GitError('This Git LFS version returned an unfamiliar prune preview. Nothing was deleted.')
    return candidates


def _confirm_prune(report, *, input_stream, output):
    """Require an explicit affirmative answer after showing reclaimable bytes."""

    print(f'Permanently delete {report.reclaimable_objects} local object(s) totaling {_format_bytes(report.reclaimable_bytes)}? [y/N] ', end='', file=output, flush=True)
    answer = input_stream.readline()
    return answer.strip().lower() in {'y', 'yes'}


def _print_report(report, *, output):
    """Render one stable, concise cache summary."""

    print(f'Location: {report.location}', file=output)
    print(f'Total: {_format_bytes(report.total_bytes)} ({report.total_objects} objects)', file=output)
    print(f'Reachable/protected: {_format_bytes(report.protected_bytes)} ({report.protected_objects} objects)', file=output)
    print(f'Reclaimable: {_format_bytes(report.reclaimable_bytes)} ({report.reclaimable_objects} objects)', file=output)


def _format_bytes(value):
    """Format a non-negative byte count using compact binary units."""

    units = ('B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB')
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f'{int(amount)} {unit}' if unit == 'B' else f'{amount:.1f} {unit}'
        amount /= 1024
