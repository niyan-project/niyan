import os
import re
import subprocess
from dataclasses import dataclass

from datasets.repositories import GitRepositoryStore, RepositoryReadError


OBJECT_ID_PATTERN = re.compile(r'^[0-9a-f]{40,64}$')
LFS_OBJECT_ID_PATTERN = re.compile(r'^oid sha256:([0-9a-f]{64})$')
README_NAMES = ('README.md', 'README.rst', 'README.txt', 'README', 'readme.md', 'readme.rst', 'readme.txt', 'readme')
README_MAX_BYTES = 1024 * 1024
TEXT_PREVIEW_MAX_BYTES = 1024 * 1024
PATH_HISTORY_MAX_BYTES = 32 * 1024 * 1024
MAX_REVISION_LENGTH = 1024
MAX_PATH_LENGTH = 4096


class RepositoryBrowseError(RuntimeError):
    """Base error for a sanitized repository browsing failure."""


class RevisionNotFound(RepositoryBrowseError):
    """Report a revision that cannot resolve to a commit."""


class RepositoryPathNotFound(RepositoryBrowseError):
    """Report a path absent at the resolved commit."""


class InvalidRepositoryInput(RepositoryBrowseError):
    """Report unsafe or malformed revision and path input."""


class LfsContentUnavailable(RepositoryBrowseError):
    """Report content that requires the not-yet-implemented LFS data plane."""


class TextPreviewUnavailable(RepositoryBrowseError):
    """Report content that cannot be rendered safely as bounded UTF-8 text."""


@dataclass(frozen=True)
class BlobMetadata:
    """Describe one Git blob and optional standard LFS pointer."""

    path: str
    object_id: str
    size: int
    lfs_object_id: str | None
    lfs_size: int | None


class RepositoryBrowser:
    """Read Git repository metadata without creating a parallel history model."""

    def __init__(self, dataset, repository_store=None):
        """Open a dataset repository through the safe storage boundary.

        Parameters
        ----------
        dataset : datasets.models.Dataset
            Dataset whose bare repository will be inspected.
        repository_store : datasets.repositories.GitRepositoryStore, optional
            Repository boundary override for tests.
        """

        self.dataset = dataset
        self.repository_store = repository_store or GitRepositoryStore()
        try:
            self.repository_path = self.repository_store.existing_path(dataset.id)
        except RepositoryReadError as error:
            raise RepositoryBrowseError('The dataset repository is unavailable.') from error

    def resolve_revision(self, revision):
        """Resolve a branch, tag, or object name to an exact commit identifier.

        Parameters
        ----------
        revision : str
            User-supplied Git revision.

        Returns
        -------
        str
            Full commit object identifier.

        Raises
        ------
        InvalidRepositoryInput
            If the revision is malformed.
        RevisionNotFound
            If the revision does not resolve to a commit.
        """

        _validate_component(revision, allow_empty=False, maximum_length=MAX_REVISION_LENGTH)
        result = self._run(['rev-parse', '--verify', '--end-of-options', f'{revision}^{{commit}}'], check=False)
        object_id = result.stdout.decode().strip()
        if result.returncode != 0 or not OBJECT_ID_PATTERN.fullmatch(object_id):
            raise RevisionNotFound('The requested revision does not exist.')
        return object_id

    def list_refs(self, *, kind, limit, offset):
        """Return one bounded page of branches or tags.

        Parameters
        ----------
        kind : str
            Either ``branches`` or ``tags``.
        limit : int
            Maximum number of refs in the page.
        offset : int
            Ordered refs to skip.

        Returns
        -------
        tuple[list[dict], int or None]
            Ref representations and the next offset when another page exists.
        """

        prefix = {'branches': 'refs/heads', 'tags': 'refs/tags'}.get(kind)
        if prefix is None:
            raise InvalidRepositoryInput('The ref kind is invalid.')
        requested_count = offset + limit + 1
        result = self._run(['for-each-ref', '--sort=refname', f'--count={requested_count}', '--format=%(refname) %(objectname) %(objecttype)', prefix])
        rows = []
        for line in result.stdout.decode('utf-8', errors='replace').splitlines()[offset:]:
            ref_name, object_id, object_type = line.split(' ', 2)
            rows.append({'name': ref_name[len(prefix) + 1 :], 'full_name': ref_name, 'object_id': object_id, 'object_type': object_type})
        has_more = len(rows) > limit
        return rows[:limit], offset + limit if has_more else None

    def list_commits(self, *, revision, limit, offset):
        """Return commits reachable from a resolved revision.

        Parameters
        ----------
        revision : str
            Branch, tag, or commit to traverse.
        limit : int
            Maximum number of commits in the page.
        offset : int
            Reachable commits to skip.

        Returns
        -------
        tuple[str, list[dict], int or None]
            Resolved commit, commit representations, and optional next offset.
        """

        resolved_commit = self.resolve_revision(revision)
        result = self._run(
            [
                'log',
                f'--skip={offset}',
                f'--max-count={limit + 1}',
                '--format=%H%x00%P%x00%an%x00%ae%x00%aI%x00%s%x00%b%x1e',
                resolved_commit,
            ]
        )
        records = []
        encoded_records = result.stdout.split(b'\x1e\n') if result.stdout else []
        if encoded_records and encoded_records[-1] == b'':
            encoded_records.pop()
        for encoded_record in encoded_records:
            encoded_fields = encoded_record.split(b'\x00')
            if len(encoded_fields) != 7:
                raise RepositoryBrowseError('Git returned malformed commit metadata.')
            fields = [field.decode('utf-8', errors='replace') for field in encoded_fields]
            object_id, parents, author_name, author_email, authored_at, subject, body = fields
            records.append(
                {
                    'object_id': object_id,
                    'parent_ids': parents.split() if parents else [],
                    'author_name': author_name,
                    'author_email': author_email,
                    'authored_at': authored_at,
                    'subject': subject,
                    'body': body,
                }
            )
        has_more = len(records) > limit
        return resolved_commit, records[:limit], offset + limit if has_more else None

    def get_commit(self, *, revision):
        """Return one exact commit representation.

        Parameters
        ----------
        revision : str
            Branch, tag, or commit to resolve.

        Returns
        -------
        tuple[str, dict]
            Resolved commit identifier and its metadata.
        """

        resolved_commit, records, _ = self.list_commits(revision=revision, limit=1, offset=0)
        if not records or records[0]['object_id'] != resolved_commit:
            raise RepositoryBrowseError('Git returned inconsistent commit metadata.')
        return resolved_commit, records[0]

    def list_tree(self, *, revision, path, limit, offset):
        """Return direct children of a directory at one exact commit.

        Parameters
        ----------
        revision : str
            Branch, tag, or commit containing the tree.
        path : str
            Repository-relative directory, or an empty root path.
        limit : int
            Maximum number of entries in the page.
        offset : int
            Ordered entries to skip.

        Returns
        -------
        tuple[str, list[dict], int or None]
            Resolved commit, tree entries, and optional next offset.
        """

        resolved_commit = self.resolve_revision(revision)
        normalized_path = _normalize_path(path, allow_empty=True)
        treeish = resolved_commit if not normalized_path else f'{resolved_commit}:{normalized_path}'
        records = self._read_nul_records(['ls-tree', '-l', '-z', treeish], maximum=offset + limit + 1, missing_error=RepositoryPathNotFound)
        parsed_entries = []
        for record in records[offset:]:
            try:
                metadata, name = record.split(b'\t', 1)
                mode, object_type, object_id, size = metadata.decode().split()
            except ValueError as error:
                raise RepositoryBrowseError('Git returned malformed tree metadata.') from error
            decoded_name = name.decode('utf-8', errors='replace')
            git_blob_size = int(size) if size != '-' else None
            parsed_entries.append((decoded_name, mode, object_type, object_id, git_blob_size))
        lfs_metadata = self._get_lfs_metadata_batch([(object_id, size) for _, _, object_type, object_id, size in parsed_entries if object_type == 'blob' and size is not None and size <= 1024])
        latest_commits = self._latest_path_commit_ids(resolved_commit=resolved_commit, directory=normalized_path, child_names={name for name, *_ in parsed_entries})
        entries = []
        for decoded_name, mode, object_type, object_id, git_blob_size in parsed_entries:
            lfs_object_id, lfs_size = lfs_metadata.get(object_id, (None, None))
            entries.append(
                {
                    'name': decoded_name,
                    'path': f'{normalized_path}/{decoded_name}' if normalized_path else decoded_name,
                    'mode': mode,
                    'object_type': object_type,
                    'object_id': object_id,
                    'size': lfs_size if lfs_size is not None else git_blob_size,
                    'git_blob_size': git_blob_size,
                    'is_lfs': lfs_object_id is not None,
                    'lfs_object_id': lfs_object_id,
                    'lfs_size': lfs_size,
                    'last_commit_id': latest_commits.get(decoded_name),
                }
            )
        has_more = len(entries) > limit
        return resolved_commit, entries[:limit], offset + limit if has_more else None

    def get_blob_metadata(self, *, revision, path):
        """Return exact Git and optional LFS metadata for one file path.

        Parameters
        ----------
        revision : str
            Branch, tag, or commit containing the file.
        path : str
            Repository-relative file path.

        Returns
        -------
        tuple[str, BlobMetadata]
            Resolved commit and immutable blob metadata.
        """

        resolved_commit = self.resolve_revision(revision)
        normalized_path = _normalize_path(path, allow_empty=False)
        object_result = self._run(['rev-parse', '--verify', '--end-of-options', f'{resolved_commit}:{normalized_path}'], check=False)
        object_id = object_result.stdout.decode().strip()
        if object_result.returncode != 0 or not OBJECT_ID_PATTERN.fullmatch(object_id):
            raise RepositoryPathNotFound('The requested file does not exist.')
        type_result = self._run(['cat-file', '-t', object_id])
        if type_result.stdout.strip() != b'blob':
            raise RepositoryPathNotFound('The requested path is not a file.')
        size = int(self._run(['cat-file', '-s', object_id]).stdout.strip())
        lfs_object_id, lfs_size = self._get_lfs_metadata(object_id=object_id, size=size)
        return resolved_commit, BlobMetadata(path=normalized_path, object_id=object_id, size=size, lfs_object_id=lfs_object_id, lfs_size=lfs_size)

    def read_text(self, *, revision, path):
        """Return one bounded ordinary Git blob as UTF-8 text.

        Parameters
        ----------
        revision : str
            Branch, tag, or commit containing the file.
        path : str
            Repository-relative file path.

        Returns
        -------
        tuple[str, BlobMetadata, str]
            Resolved commit, immutable blob metadata, and decoded text.

        Raises
        ------
        TextPreviewUnavailable
            If the blob is stored through LFS, too large, binary, or not UTF-8.
        """

        resolved_commit, metadata = self.get_blob_metadata(revision=revision, path=path)
        if metadata.lfs_object_id is not None or metadata.size > TEXT_PREVIEW_MAX_BYTES:
            raise TextPreviewUnavailable('The file cannot be previewed as text.')
        content = self._run(['cat-file', 'blob', metadata.object_id], maximum_output=TEXT_PREVIEW_MAX_BYTES).stdout
        if b'\x00' in content:
            raise TextPreviewUnavailable('The file cannot be previewed as text.')
        try:
            decoded = content.decode('utf-8')
        except UnicodeDecodeError as error:
            raise TextPreviewUnavailable('The file cannot be previewed as text.') from error
        return resolved_commit, metadata, decoded

    def _latest_path_commit_ids(self, *, resolved_commit, directory, child_names):
        """Find the newest commit affecting each direct child with one Git walk.

        Parameters
        ----------
        resolved_commit : str
            Exact commit containing the listed tree.
        directory : str
            Normalized repository directory being listed.
        child_names : set[str]
            Direct child names that need attribution.

        Returns
        -------
        dict[str, str]
            Child names mapped to their newest affecting commit when found.
        """

        if not child_names:
            return {}
        arguments = ['log', '--format=%x1e%H', '--name-only', '-z', resolved_commit, '--']
        if directory:
            arguments.append(directory)
        process = self._popen(arguments)
        remaining = set(child_names)
        commits = {}
        buffer = b''
        bytes_read = 0
        intentionally_stopped = False

        def consume(record):
            fields = record.split(b'\x00')
            if len(fields) < 2:
                return
            commit_id = fields[0].decode('ascii', errors='ignore')
            if not OBJECT_ID_PATTERN.fullmatch(commit_id):
                return
            prefix = f'{directory}/' if directory else ''
            for encoded_path in fields[1:]:
                path = encoded_path.lstrip(b'\n').decode('utf-8', errors='replace')
                if not path or (prefix and not path.startswith(prefix)):
                    continue
                relative_path = path[len(prefix) :]
                child_name = relative_path.split('/', 1)[0]
                if child_name in remaining:
                    commits[child_name] = commit_id
                    remaining.remove(child_name)

        try:
            while remaining:
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    break
                bytes_read += len(chunk)
                if bytes_read > PATH_HISTORY_MAX_BYTES:
                    intentionally_stopped = True
                    process.terminate()
                    break
                buffer += chunk
                records = buffer.split(b'\x1e')
                buffer = records.pop()
                for record in records:
                    if record:
                        consume(record)
                if not remaining:
                    intentionally_stopped = True
                    process.terminate()
                    break
            if not intentionally_stopped and buffer:
                consume(buffer)
            return_code = process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError) as error:
            if process.poll() is None:
                process.kill()
            process.wait()
            raise RepositoryBrowseError('The repository could not be read.') from error
        if return_code != 0 and not intentionally_stopped:
            raise RepositoryBrowseError('The repository could not be read.')
        return commits

    def _get_lfs_metadata(self, *, object_id, size):
        """Parse a small Git blob when it may be a standard LFS pointer.

        Parameters
        ----------
        object_id : str
            Immutable Git blob identifier.
        size : int or None
            Git-resident blob size.

        Returns
        -------
        tuple[str or None, int or None]
            LFS SHA-256 identifier and logical size when the blob is a pointer.
        """

        if size is None or size > 1024:
            return None, None
        pointer = self._run(['cat-file', 'blob', object_id], maximum_output=1024).stdout.decode('utf-8', errors='replace')
        return _parse_lfs_pointer(pointer)

    def _get_lfs_metadata_batch(self, candidates):
        """Parse possible LFS pointers through one bounded Git batch process.

        Parameters
        ----------
        candidates : list[tuple[str, int]]
            Small Git blobs that may contain LFS pointers.

        Returns
        -------
        dict[str, tuple[str or None, int or None]]
            Candidate object IDs mapped to parsed LFS metadata.
        """

        if not candidates:
            return {}
        unique_candidates = dict(candidates)
        standard_input = ''.join(f'{object_id}\n' for object_id in unique_candidates).encode()
        maximum_output = sum(size + 128 for size in unique_candidates.values())
        output = self._run(['cat-file', '--batch'], standard_input=standard_input, maximum_output=maximum_output).stdout
        offset = 0
        metadata = {}
        for expected_object_id, expected_size in unique_candidates.items():
            header_end = output.find(b'\n', offset)
            if header_end < 0:
                raise RepositoryBrowseError('Git returned malformed batch metadata.')
            try:
                object_id, object_type, encoded_size = output[offset:header_end].decode().split()
                size = int(encoded_size)
            except (UnicodeDecodeError, ValueError) as error:
                raise RepositoryBrowseError('Git returned malformed batch metadata.') from error
            content_start = header_end + 1
            content_end = content_start + size
            if object_id != expected_object_id or object_type != 'blob' or size != expected_size or output[content_end : content_end + 1] != b'\n':
                raise RepositoryBrowseError('Git returned inconsistent batch metadata.')
            metadata[object_id] = _parse_lfs_pointer(output[content_start:content_end].decode('utf-8', errors='replace'))
            offset = content_end + 1
        return metadata

    def open_blob(self, *, revision, path):
        """Open one ordinary Git blob as a streaming child-process response.

        Parameters
        ----------
        revision : str
            Branch, tag, or commit containing the file.
        path : str
            Repository-relative file path.

        Returns
        -------
        tuple[str, BlobMetadata, collections.abc.Iterator[bytes]]
            Resolved commit, metadata, and incremental content iterator.

        Raises
        ------
        LfsContentUnavailable
            If the Git blob is an LFS pointer rather than inline content.
        """

        resolved_commit, metadata = self.get_blob_metadata(revision=revision, path=path)
        if metadata.lfs_object_id is not None:
            raise LfsContentUnavailable('Git LFS content is not available yet.')
        process = self._popen(['cat-file', 'blob', metadata.object_id])
        return resolved_commit, metadata, _stream_process(process)

    def open_blob_range(self, *, revision, path, start, end):
        """Open a bounded byte range from one ordinary Git blob.

        Parameters
        ----------
        revision : str
            Branch, tag, or exact commit containing the file.
        path : str
            Repository-relative file path.
        start : int
            Inclusive byte offset.
        end : int
            Exclusive byte offset.

        Returns
        -------
        tuple[str, BlobMetadata, collections.abc.Iterator[bytes]]
            Resolved commit, metadata, and bounded incremental content.
        """

        resolved_commit, metadata = self.get_blob_metadata(revision=revision, path=path)
        if metadata.lfs_object_id is not None:
            raise LfsContentUnavailable('Git LFS content is not available yet.')
        if not 0 <= start <= end <= metadata.size:
            raise InvalidRepositoryInput('The requested byte range is invalid.')
        process = self._popen(['cat-file', 'blob', metadata.object_id])
        return resolved_commit, metadata, _stream_process_range(process, start=start, end=end)

    def read_readme(self, *, revision):
        """Return a conventional root README capped to a safe response size.

        Parameters
        ----------
        revision : str
            Branch, tag, or commit containing the README.

        Returns
        -------
        tuple[str, BlobMetadata, str]
            Resolved commit, README metadata, and decoded text.
        """

        resolved_commit = self.resolve_revision(revision)
        metadata = None
        for candidate in README_NAMES:
            try:
                _, metadata = self.get_blob_metadata(revision=resolved_commit, path=candidate)
                break
            except RepositoryPathNotFound:
                continue
        if metadata is None:
            raise RepositoryPathNotFound('The repository has no root README.')
        if metadata.lfs_object_id is not None or metadata.size > README_MAX_BYTES:
            raise LfsContentUnavailable('The README cannot be returned inline.')
        content = self._run(['cat-file', 'blob', metadata.object_id], maximum_output=README_MAX_BYTES).stdout.decode('utf-8', errors='replace')
        return resolved_commit, metadata, content

    def _run(self, arguments, *, check=True, maximum_output=4 * 1024 * 1024, standard_input=None):
        """Run a bounded, non-shell Git read command.

        Parameters
        ----------
        arguments : list[str]
            Git arguments following the fixed repository selector.
        check : bool, optional
            Treat a non-zero exit status as an unavailable repository.
        maximum_output : int, optional
            Maximum captured standard-output bytes.
        standard_input : bytes, optional
            Bounded bytes supplied to Git standard input.

        Returns
        -------
        subprocess.CompletedProcess
            Completed Git process with captured output.
        """

        try:
            result = subprocess.run(self._command(arguments), input=standard_input, check=False, capture_output=True, timeout=30, env=self._environment())
        except (OSError, subprocess.SubprocessError) as error:
            raise RepositoryBrowseError('The repository could not be read.') from error
        if len(result.stdout) > maximum_output:
            raise RepositoryBrowseError('The repository response exceeds the supported size.')
        if check and result.returncode != 0:
            raise RepositoryBrowseError('The repository could not be read.')
        return result

    def _popen(self, arguments):
        """Start a non-shell Git read command for incremental output.

        Parameters
        ----------
        arguments : list[str]
            Git arguments following the fixed repository selector.

        Returns
        -------
        subprocess.Popen
            Running Git process with piped standard output.
        """

        try:
            return subprocess.Popen(self._command(arguments), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=self._environment())
        except OSError as error:
            raise RepositoryBrowseError('The repository could not be read.') from error

    def _read_nul_records(self, arguments, *, maximum, missing_error):
        """Read only the required number of NUL-delimited Git records.

        Parameters
        ----------
        arguments : list[str]
            Git arguments that produce NUL-delimited output.
        maximum : int
            Maximum number of complete records to retain.
        missing_error : type[RepositoryBrowseError]
            Error type used when Git returns no records unsuccessfully.

        Returns
        -------
        list[bytes]
            Complete records without their NUL terminators.
        """

        process = self._popen(arguments)
        records = []
        buffer = b''
        try:
            while len(records) < maximum:
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    break
                buffer += chunk
                while b'\x00' in buffer and len(records) < maximum:
                    record, buffer = buffer.split(b'\x00', 1)
                    records.append(record)
            if len(records) >= maximum and process.poll() is None:
                process.terminate()
            return_code = process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError) as error:
            if process.poll() is None:
                process.kill()
            process.wait()
            raise RepositoryBrowseError('The repository could not be read.') from error
        if return_code != 0 and not records:
            raise missing_error('The requested repository path does not exist.')
        return records

    def _command(self, arguments):
        """Build a Git command fixed to this bare repository.

        Parameters
        ----------
        arguments : list[str]
            Repository read operation arguments.

        Returns
        -------
        list[str]
            Complete non-shell Git command.
        """

        return ['git', '--no-pager', '--git-dir', str(self.repository_path), *arguments]

    def _environment(self):
        """Return a deterministic environment without user-level Git config.

        Returns
        -------
        dict[str, str]
            Minimal environment for repository reads.
        """

        return {
            'PATH': os.environ.get('PATH', ''),
            'HOME': os.environ.get('HOME', ''),
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_OPTIONAL_LOCKS': '0',
            'GIT_NO_LAZY_FETCH': '1',
            'LC_ALL': 'C.UTF-8',
        }


def _normalize_path(path, *, allow_empty):
    """Validate and normalize a repository-relative path."""

    _validate_component(path, allow_empty=allow_empty, maximum_length=MAX_PATH_LENGTH)
    normalized = path.strip('/')
    if (not allow_empty and not normalized) or (normalized and any(part in ('', '.', '..') for part in normalized.split('/'))):
        raise InvalidRepositoryInput('The repository path is invalid.')
    return normalized


def _validate_component(value, *, allow_empty, maximum_length):
    """Reject empty or control-character-bearing Git input."""

    if not isinstance(value, str) or len(value) > maximum_length or (not allow_empty and not value) or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise InvalidRepositoryInput('The repository input is invalid.')


def _parse_lfs_pointer(content):
    """Return standard LFS object metadata when content is a valid pointer."""

    lines = content.splitlines()
    if len(lines) != 3 or lines[0] != 'version https://git-lfs.github.com/spec/v1':
        return None, None
    object_match = LFS_OBJECT_ID_PATTERN.fullmatch(lines[1])
    if object_match is None or not lines[2].startswith('size '):
        return None, None
    try:
        size = int(lines[2][5:])
    except ValueError:
        return None, None
    if size < 0:
        return None, None
    return object_match.group(1), size


def _stream_process(process):
    """Yield child-process stdout and reliably reap it."""

    try:
        while True:
            chunk = process.stdout.read(64 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait()


def _stream_process_range(process, *, start, end):
    """Discard a bounded prefix and yield only the requested byte interval."""

    remaining_skip = start
    remaining_content = end - start
    try:
        while remaining_skip:
            chunk = process.stdout.read(min(64 * 1024, remaining_skip))
            if not chunk:
                raise RepositoryBrowseError('The Git blob ended before the requested range.')
            remaining_skip -= len(chunk)
        while remaining_content:
            chunk = process.stdout.read(min(64 * 1024, remaining_content))
            if not chunk:
                raise RepositoryBrowseError('The Git blob ended before the requested range.')
            remaining_content -= len(chunk)
            yield chunk
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait()
