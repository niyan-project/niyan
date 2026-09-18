import os
import subprocess
import threading
from http import HTTPStatus

from django.http import HttpResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.authentication import access_token_permits, authenticate_git_basic
from datasets.policies import can_read_dataset, can_write_repository
from datasets.repositories import GitRepositoryStore, RepositoryReadError
from datasets.models import Dataset


ALLOWED_RESPONSE_HEADERS = {'content-type', 'content-length', 'expires', 'pragma', 'cache-control'}
UPLOAD_PACK = 'git-upload-pack'
RECEIVE_PACK = 'git-receive-pack'


def git_authentication_required():
    """Return a Git-compatible HTTPS Basic authentication challenge."""

    response = HttpResponse('Authentication is required.', status=401, content_type='text/plain')
    # HTTP auth scheme headers must remain ASCII; Django MIME-encodes non-ASCII header values, which Git's HTTP client cannot parse as a Basic challenge.
    response['WWW-Authenticate'] = 'Basic realm="Niyan Git"'
    return response


@csrf_exempt
def git_http_backend(request, dataset_id, git_path=''):
    """Authorize and stream one smart Git HTTP request.

    Parameters
    ----------
    request : django.http.HttpRequest
        Incoming Git smart-HTTP request.
    dataset_id : uuid.UUID
        Immutable dataset repository identity.
    git_path : str, optional
        Smart-HTTP endpoint beneath the repository URL.

    Returns
    -------
    django.http.HttpResponse
        Authentication error, sanitized repository error, or streamed Git CGI response.
    """

    if not git_path:
        return HttpResponse('Git smart HTTP endpoint not found.', status=404, content_type='text/plain')
    access_token = authenticate_git_basic(request)
    if access_token is None:
        return git_authentication_required()

    service = _requested_service(request, git_path)
    if service is None:
        return HttpResponse('Unsupported Git smart HTTP request.', status=405, content_type='text/plain')
    required_scope = 'write_repository' if service == RECEIVE_PACK else 'read_repository'
    if not access_token_permits(access_token=access_token, scope=required_scope, dataset_id=dataset_id):
        return HttpResponse('The credential does not permit this repository operation.', status=403, content_type='text/plain')

    dataset = Dataset.objects.select_related('namespace__parent').filter(pk=dataset_id, deletion_started_at__isnull=True).first()
    if dataset is None or not can_read_dataset(user=access_token.user, dataset=dataset):
        return HttpResponse('Dataset repository not found.', status=404, content_type='text/plain')
    if service == RECEIVE_PACK and not can_write_repository(user=access_token.user, dataset=dataset):
        return HttpResponse('The credential does not permit this repository operation.', status=403, content_type='text/plain')

    try:
        return GitHttpBackend().execute(request=request, dataset=dataset, git_path=git_path, service=service, remote_user=access_token.user)
    except RepositoryReadError:
        return HttpResponse('Dataset repository unavailable.', status=503, content_type='text/plain')


class GitHttpBackend:
    """Adapt authorized Django requests to Git's CGI smart-HTTP backend."""

    def __init__(self, repository_store=None):
        """Initialize the adapter with an overridable repository store.

        Parameters
        ----------
        repository_store : datasets.repositories.GitRepositoryStore, optional
            Repository boundary override for tests.
        """

        self.repository_store = repository_store or GitRepositoryStore()

    def execute(self, *, request, dataset, git_path, service, remote_user):
        """Start Git's backend and return its streamed CGI response.

        Parameters
        ----------
        request : django.http.HttpRequest
            Authorized smart-HTTP request.
        dataset : datasets.models.Dataset
            Dataset whose repository will be exposed.
        git_path : str
            Validated smart-HTTP endpoint.
        service : str
            Validated Git service selected from the request path and query.
        remote_user : accounts.models.User
            User recorded in the CGI environment without credentials.

        Returns
        -------
        django.http.StreamingHttpResponse
            Response whose iterator drains Git stdout incrementally.

        Raises
        ------
        RepositoryReadError
            If Git cannot start or emits malformed CGI headers.
        """

        repository_path = self.repository_store.existing_path(dataset.id)
        environment = self._environment(request=request, repository_path=repository_path, git_path=git_path, service=service, remote_user=remote_user)
        command = ['git']
        if service == RECEIVE_PACK:
            # Enable receive-pack for this authorized subprocess only; never mutate repository configuration or make the service globally anonymous.
            command.extend(['-c', 'http.receivepack=true'])
        command.append('http-backend')
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=environment,
            )
        except OSError as error:
            raise RepositoryReadError('Git HTTP backend is unavailable.') from error

        input_thread = threading.Thread(target=self._pump_request, args=(request, process), daemon=True)
        input_thread.start()
        try:
            status, headers = self._read_headers(process)
        except Exception:
            process.kill()
            process.wait()
            raise

        response = StreamingHttpResponse(self._stream_output(process, input_thread), status=status)
        for name, value in headers:
            if name.lower() in ALLOWED_RESPONSE_HEADERS:
                response[name] = value
        return response

    def _environment(self, *, request, repository_path, git_path, service, remote_user):
        """Build a CGI environment without forwarding authorization data.

        Parameters
        ----------
        request : django.http.HttpRequest
            Authorized smart-HTTP request.
        repository_path : pathlib.Path
            Verified bare repository path.
        git_path : str
            Supported endpoint beneath the repository URL.
        service : str
            Validated Git service selected from the endpoint.
        remote_user : accounts.models.User
            Authenticated token owner.

        Returns
        -------
        dict[str, str]
            Minimal CGI and Git subprocess environment.
        """

        environment = {
            'PATH': os.environ.get('PATH', ''),
            'HOME': str(repository_path.parent),
            'LANG': 'C.UTF-8',
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_NO_LAZY_FETCH': '1',
            'GIT_PROJECT_ROOT': str(repository_path.parent),
            'GIT_HTTP_EXPORT_ALL': '1',
            'PATH_INFO': f'/{repository_path.name}/{git_path}',
            'QUERY_STRING': f'service={service}' if git_path == 'info/refs' else '',
            'REQUEST_METHOD': request.method,
            'CONTENT_TYPE': request.content_type or '',
            'CONTENT_LENGTH': request.META.get('CONTENT_LENGTH', ''),
            'REMOTE_USER': str(remote_user.pk),
            'REMOTE_ADDR': request.META.get('REMOTE_ADDR', ''),
            'SERVER_PROTOCOL': request.META.get('SERVER_PROTOCOL', 'HTTP/1.1'),
        }
        git_protocol = request.headers.get('Git-Protocol')
        if git_protocol:
            environment['HTTP_GIT_PROTOCOL'] = git_protocol
        return environment

    def _pump_request(self, request, process):
        """Copy the request body to Git without buffering it in Django.

        Parameters
        ----------
        request : django.http.HttpRequest
            Incoming request body source.
        process : subprocess.Popen
            Git backend process receiving the body.
        """

        try:
            while True:
                chunk = request.read(64 * 1024)
                if not chunk:
                    break
                process.stdin.write(chunk)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            if process.stdin is not None:
                process.stdin.close()

    def _read_headers(self, process):
        """Parse a bounded CGI header block emitted by Git.

        Parameters
        ----------
        process : subprocess.Popen
            Git backend process with piped standard output.

        Returns
        -------
        tuple[int, list[tuple[str, str]]]
            HTTP status and parsed CGI response headers.

        Raises
        ------
        RepositoryReadError
            If the header block or status is malformed.
        """

        status = 200
        headers = []
        consumed = 0
        while True:
            line = process.stdout.readline(8193)
            consumed += len(line)
            if not line or consumed > 64 * 1024:
                raise RepositoryReadError('Git returned an invalid HTTP response.')
            if line in (b'\n', b'\r\n'):
                break
            try:
                name, value = line.decode('latin-1').rstrip('\r\n').split(':', 1)
            except ValueError as error:
                raise RepositoryReadError('Git returned an invalid HTTP response.') from error
            if name.lower() == 'status':
                try:
                    status = int(value.strip().split(' ', 1)[0])
                    HTTPStatus(status)
                except (ValueError, KeyError) as error:
                    raise RepositoryReadError('Git returned an invalid HTTP status.') from error
            else:
                headers.append((name.strip(), value.strip()))
        return status, headers

    def _stream_output(self, process, input_thread):
        """Yield Git response bytes and always reap the child process.

        Parameters
        ----------
        process : subprocess.Popen
            Git backend process being drained.
        input_thread : threading.Thread
            Concurrent request-body pump to join during cleanup.

        Yields
        ------
        bytes
            Incremental smart-HTTP response content.
        """

        try:
            while True:
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            input_thread.join(timeout=1)
            if process.poll() is None:
                process.terminate()
            process.wait()


def _requested_service(request, git_path):
    """Return the validated smart-HTTP service selected by path and method."""

    if request.method == 'GET' and git_path == 'info/refs':
        service = request.GET.get('service')
        return service if service in {UPLOAD_PACK, RECEIVE_PACK} else None
    if request.method == 'POST' and git_path in {UPLOAD_PACK, RECEIVE_PACK}:
        expected_content_type = f'application/x-{git_path}-request'
        return git_path if request.content_type == expected_content_type else None
    return None
