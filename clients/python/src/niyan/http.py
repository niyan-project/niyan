import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from niyan import __version__
from niyan.errors import ApiError, ConfigurationError


class SameOriginRedirectHandler(HTTPRedirectHandler):
    """Reject redirects that could forward credentials to another origin."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        """Follow only redirects preserving scheme and network location.

        Parameters
        ----------
        request : urllib.request.Request
            Original request.
        file_pointer : file-like object
            Redirect response body.
        code : int
            Redirect HTTP status.
        message : str
            HTTP status text.
        headers : email.message.Message
            Redirect response headers.
        new_url : str
            Target supplied by the server.

        Returns
        -------
        urllib.request.Request
            Safe redirected request.

        Raises
        ------
        HTTPError
            If the redirect changes origin.
        """

        original_origin = _origin(request.full_url)
        redirected_origin = _origin(urljoin(request.full_url, new_url))
        if original_origin != redirected_origin:
            raise HTTPError(request.full_url, 400, 'Cross-origin redirect refused', headers, file_pointer)
        return super().redirect_request(request, file_pointer, code, message, headers, new_url)


class ApiClient:
    """Call the public Niyān REST API without server implementation imports."""

    def __init__(self, host, *, token=None, opener=None, timeout=30):
        """Initialize one installation-scoped client.

        Parameters
        ----------
        host : str
            Normalized Niyān installation origin.
        token : str, optional
            Bearer credential used for authenticated calls.
        opener : urllib.request.OpenerDirector, optional
            HTTP test double or configured opener.
        timeout : int or float, optional
            Request timeout in seconds.
        """

        self.host = normalize_host(host)
        self.token = token
        self.opener = opener or build_opener(SameOriginRedirectHandler())
        self.timeout = timeout

    def start_device_authorization(self, *, name, scopes, dataset_path=None):
        """Start browser-assisted CLI authentication.

        Parameters
        ----------
        name : str
            Human-facing device label.
        scopes : list[str]
            Requested access-token scopes.
        dataset_path : str, optional
            Requested single-dataset resource boundary.

        Returns
        -------
        tuple[int, dict]
            HTTP status and device authorization metadata.
        """

        return self.request('POST', '/api/v1/auth/device', payload={'name': name, 'scopes': scopes, 'dataset_path': dataset_path or ''})

    def exchange_device_code(self, device_code):
        """Poll and exchange one private device code.

        Parameters
        ----------
        device_code : str
            Private code returned when authorization began.

        Returns
        -------
        tuple[int, dict]
            Pending status or approved access-token response.
        """

        return self.request('POST', '/api/v1/auth/device/token', payload={'device_code': device_code}, accepted_statuses={200, 202})

    def current_user(self):
        """Return current user and token metadata for this credential.

        Returns
        -------
        tuple[int, dict]
            HTTP status and authenticated account metadata.
        """

        return self.request('GET', '/api/v1/auth/me')

    def revoke_access_token(self, token_id):
        """Revoke the active access token by immutable identifier.

        Parameters
        ----------
        token_id : str
            Server-issued access-token UUID.

        Returns
        -------
        tuple[int, dict]
            Empty successful response metadata.
        """

        return self.request('DELETE', f'/api/v1/auth/tokens/{token_id}')

    def resolve_dataset(self, dataset_path):
        """Resolve a human-facing dataset path to immutable Git identity.

        Parameters
        ----------
        dataset_path : str
            Namespace and dataset path supplied by the user.

        Returns
        -------
        tuple[int, dict]
            HTTP status and repository location metadata.
        """

        query = urlencode({'path': dataset_path})
        return self.request('GET', f'/api/v1/datasets/resolve?{query}')

    def resolve_namespace(self, namespace_path):
        """Resolve a human-facing namespace path to immutable identity.

        Parameters
        ----------
        namespace_path : str
            Root or nested namespace path supplied by the user.

        Returns
        -------
        tuple[int, dict]
            HTTP status and namespace metadata.
        """

        query = urlencode({'path': namespace_path})
        return self.request('GET', f'/api/v1/namespaces/resolve?{query}')

    def create_dataset(self, *, namespace_id, slug, name):
        """Create one empty remote dataset repository.

        Parameters
        ----------
        namespace_id : str
            Immutable containing namespace UUID.
        slug : str
            Requested dataset path component.
        name : str
            Dataset display name.

        Returns
        -------
        tuple[int, dict]
            Created status and dataset representation.
        """

        return self.request('POST', '/api/v1/datasets', payload={'namespace_id': namespace_id, 'slug': slug, 'name': name})

    def list_datasets(self, *, namespace_id=None, limit=100, offset=0):
        """Return one page of datasets visible to this credential.

        Parameters
        ----------
        namespace_id : str, optional
            Namespace UUID used to restrict the list.
        limit : int, optional
            Maximum records to return.
        offset : int, optional
            Ordered records to skip.

        Returns
        -------
        tuple[int, dict]
            Successful status and paginated dataset representation.
        """

        parameters = {'limit': limit, 'offset': offset}
        if namespace_id is not None:
            parameters['namespace_id'] = namespace_id
        return self.request('GET', f'/api/v1/datasets?{urlencode(parameters)}')

    def get_dataset(self, dataset_id):
        """Retrieve public metadata for one visible dataset UUID.

        Parameters
        ----------
        dataset_id : str
            Immutable dataset UUID.

        Returns
        -------
        tuple[int, dict]
            Successful status and dataset representation.
        """

        return self.request('GET', f'/api/v1/datasets/{dataset_id}')

    def update_dataset(self, dataset_id, *, slug=None, name=None):
        """Change selected mutable metadata on one dataset.

        Parameters
        ----------
        dataset_id : str
            Immutable dataset UUID.
        slug : str, optional
            Replacement dataset path component.
        name : str, optional
            Replacement display name.

        Returns
        -------
        tuple[int, dict]
            Successful status and updated dataset representation.
        """

        payload = {}
        if slug is not None:
            payload['slug'] = slug
        if name is not None:
            payload['name'] = name
        return self.request('PATCH', f'/api/v1/datasets/{dataset_id}', payload=payload)

    def delete_dataset(self, dataset_id):
        """Permanently delete one dataset and its repository.

        Parameters
        ----------
        dataset_id : str
            Immutable dataset UUID.

        Returns
        -------
        tuple[int, dict]
            Empty successful response metadata.
        """

        return self.request('DELETE', f'/api/v1/datasets/{dataset_id}')

    def get_repository_readme(self, dataset_id, *, revision='main'):
        """Return a root README at one repository revision.

        Parameters
        ----------
        dataset_id : str
            Immutable dataset UUID.
        revision : str, optional
            Branch, tag, or commit containing the README.

        Returns
        -------
        tuple[int, dict]
            Successful status and README representation.
        """

        query = urlencode({'revision': revision})
        return self.request('GET', f'/api/v1/datasets/{dataset_id}/repository/readme?{query}')

    def request(self, method, path, *, payload=None, accepted_statuses=None):
        """Send one JSON API request and return status with decoded content.

        Parameters
        ----------
        method : str
            HTTP request method.
        path : str
            Absolute API path relative to this installation.
        payload : object, optional
            JSON-serializable request body.
        accepted_statuses : set[int], optional
            Successful statuses beyond the default 2xx handling.

        Returns
        -------
        tuple[int, dict]
            HTTP status and decoded JSON body.

        Raises
        ------
        ApiError
            If transport, JSON, or API validation fails.
        """

        url = urljoin(f'{self.host}/', path.lstrip('/'))
        if _origin(url) != _origin(self.host):
            raise ApiError('Refusing to send credentials outside the selected Niyān host.')
        encoded_payload = json.dumps(payload).encode() if payload is not None else None
        headers = {'Accept': 'application/json', 'User-Agent': f'niyan/{__version__}'}
        if encoded_payload is not None:
            headers['Content-Type'] = 'application/json'
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        request = Request(url, data=encoded_payload, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=self.timeout)
            status = response.status
            body = response.read(1024 * 1024)
        except HTTPError as error:
            status = error.code
            body = error.read(1024 * 1024)
            parsed_error = _decode_json(body)
            detail = parsed_error.get('detail') if isinstance(parsed_error, dict) else None
            code = parsed_error.get('code') if isinstance(parsed_error, dict) else None
            raise ApiError(detail or f'Niyān returned HTTP {status}.', status=status, code=code) from error
        except (URLError, OSError) as error:
            raise ApiError(f'Could not reach the Niyān installation at {self.host}.') from error

        allowed = accepted_statuses if accepted_statuses is not None else set(range(200, 300))
        if status not in allowed:
            raise ApiError(f'Niyān returned unexpected HTTP {status}.', status=status)
        decoded = _decode_json(body)
        if not isinstance(decoded, dict):
            raise ApiError('Niyān returned an invalid JSON response.', status=status)
        return status, decoded


def normalize_host(host):
    """Normalize and validate one installation origin."""

    value = host.strip()
    if '://' not in value:
        value = f'https://{value}'
    parsed = urlparse(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.params or parsed.query or parsed.fragment:
        raise ConfigurationError('Niyān host must be an HTTP or HTTPS origin without a path.')
    if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ConfigurationError('Niyān hosts require HTTPS except for loopback development installations.')
    return f'{parsed.scheme}://{parsed.netloc}'.rstrip('/')


def _decode_json(body):
    """Decode a bounded JSON body or return ``None`` when invalid."""

    if not body:
        return {}
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _origin(url):
    """Return normalized scheme and authority for redirect comparison."""

    parsed = urlparse(url)
    return parsed.scheme.lower(), parsed.netloc.lower()
