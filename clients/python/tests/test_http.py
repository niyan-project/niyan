import io
import json
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from urllib.request import Request

from niyan.errors import ApiError, ConfigurationError
from niyan.http import ApiClient, SameOriginRedirectHandler, normalize_host


class FakeResponse:
    """Provide the bounded response interface consumed by ``ApiClient``."""

    def __init__(self, status=200, body=b'{}'):
        """Store one status and response body."""

        self.status = status
        self.body = body
        self.read_limit = None

    def read(self, limit):
        """Return the configured body and record the requested safety limit."""

        self.read_limit = limit
        return self.body


class ApiRequestTests(unittest.TestCase):
    """Exercise HTTP safety, serialization, and error translation."""

    def test_authenticated_json_request_builds_expected_http_request(self):
        """Send bearer credentials and JSON only to the selected origin."""

        response = FakeResponse(201, json.dumps({'id': 'dataset-id'}).encode())
        opener = Mock()
        opener.open.return_value = response
        client = ApiClient('https://data.example.test', token='secret-token', opener=opener, timeout=12)

        status, body = client.request('POST', '/api/v1/datasets', payload={'name': 'Images'})

        self.assertEqual((status, body), (201, {'id': 'dataset-id'}))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://data.example.test/api/v1/datasets')
        self.assertEqual(request.method, 'POST')
        self.assertEqual(json.loads(request.data), {'name': 'Images'})
        self.assertEqual(request.get_header('Authorization'), 'Bearer secret-token')
        self.assertEqual(request.get_header('Content-type'), 'application/json')
        self.assertTrue(request.get_header('User-agent').startswith('niyan/'))
        self.assertEqual(opener.open.call_args.kwargs, {'timeout': 12})
        self.assertEqual(response.read_limit, 1024 * 1024)

    def test_unauthenticated_request_omits_credential_and_content_headers(self):
        """Keep optional headers off public requests without a request body."""

        opener = Mock()
        opener.open.return_value = FakeResponse(204, b'')
        client = ApiClient('https://data.example.test', opener=opener)

        status, body = client.request('DELETE', '/api/v1/session')

        request = opener.open.call_args.args[0]
        self.assertEqual((status, body), (204, {}))
        self.assertIsNone(request.data)
        self.assertIsNone(request.get_header('Authorization'))
        self.assertIsNone(request.get_header('Content-type'))

    def test_explicit_accepted_statuses_replace_default_success_range(self):
        """Honor polling contracts without silently accepting unrelated statuses."""

        opener = Mock()
        opener.open.return_value = FakeResponse(202, b'{}')
        client = ApiClient('https://data.example.test', opener=opener)

        self.assertEqual(client.request('GET', '/poll', accepted_statuses={202}), (202, {}))
        opener.open.return_value = FakeResponse(200, b'{}')
        with self.assertRaisesRegex(ApiError, 'unexpected HTTP 200') as context:
            client.request('GET', '/poll', accepted_statuses={202})
        self.assertEqual(context.exception.status, 200)

    def test_request_refuses_cross_origin_absolute_url(self):
        """Reject paths that could forward an access token to another server."""

        opener = Mock()
        client = ApiClient('https://data.example.test', token='secret-token', opener=opener)

        with self.assertRaisesRegex(ApiError, 'outside the selected'):
            client.request('GET', 'https://attacker.example/api')
        opener.open.assert_not_called()

    def test_json_http_error_preserves_public_metadata(self):
        """Expose the server's safe detail, stable code, and HTTP status."""

        body = io.BytesIO(json.dumps({'detail': 'Access denied.', 'code': 'forbidden'}).encode())
        opener = Mock()
        opener.open.side_effect = HTTPError('https://data.example.test/private', 403, 'Forbidden', {}, body)
        client = ApiClient('https://data.example.test', opener=opener)

        with self.assertRaisesRegex(ApiError, 'Access denied') as context:
            client.request('GET', '/private')
        self.assertEqual(context.exception.status, 403)
        self.assertEqual(context.exception.code, 'forbidden')

    def test_invalid_http_error_body_uses_status_fallback(self):
        """Return a useful sanitized error when the server body is not JSON."""

        opener = Mock()
        opener.open.side_effect = HTTPError('https://data.example.test/failure', 502, 'Bad Gateway', {}, io.BytesIO(b'<html>failure</html>'))
        client = ApiClient('https://data.example.test', opener=opener)

        with self.assertRaisesRegex(ApiError, 'HTTP 502') as context:
            client.request('GET', '/failure')
        self.assertEqual(context.exception.status, 502)
        self.assertIsNone(context.exception.code)

    def test_transport_errors_do_not_leak_low_level_details(self):
        """Translate URL and operating-system failures into one stable error."""

        for error in (URLError('private DNS detail'), OSError('private socket detail')):
            with self.subTest(error=type(error).__name__):
                opener = Mock()
                opener.open.side_effect = error
                client = ApiClient('https://data.example.test', opener=opener)
                with self.assertRaisesRegex(ApiError, 'Could not reach.*data.example.test') as context:
                    client.request('GET', '/api/v1/capabilities')
                self.assertNotIn('private', str(context.exception))

    def test_success_response_requires_json_object(self):
        """Reject malformed JSON and valid non-object JSON response bodies."""

        for body in (b'not-json', b'[]'):
            with self.subTest(body=body):
                opener = Mock()
                opener.open.return_value = FakeResponse(200, body)
                client = ApiClient('https://data.example.test', opener=opener)
                with self.assertRaisesRegex(ApiError, 'invalid JSON') as context:
                    client.request('GET', '/api/v1/capabilities')
                self.assertEqual(context.exception.status, 200)


class ApiEndpointTests(unittest.TestCase):
    """Keep public client helpers aligned with the documented REST contract."""

    def setUp(self):
        """Create a client whose request boundary can be inspected directly."""

        self.client = ApiClient('https://data.example.test', opener=Mock())
        self.client.request = Mock(return_value=(200, {'ok': True}))

    def assert_request(self, expected, callback):
        """Assert one endpoint helper delegates with exact request arguments."""

        self.client.request.reset_mock()
        self.assertEqual(callback(), (200, {'ok': True}))
        self.client.request.assert_called_once_with(*expected[0], **expected[1])

    def test_authentication_endpoint_contracts(self):
        """Preserve device login, current-user, and revocation routes."""

        cases = [
            ((('POST', '/api/v1/auth/device'), {'payload': {'name': 'Laptop', 'scopes': ['read'], 'dataset_path': ''}}), lambda: self.client.start_device_authorization(name='Laptop', scopes=['read'])),
            ((('POST', '/api/v1/auth/device'), {'payload': {'name': 'Job', 'scopes': ['write'], 'dataset_path': 'lab/images'}}), lambda: self.client.start_device_authorization(name='Job', scopes=['write'], dataset_path='lab/images')),
            ((('POST', '/api/v1/auth/device/token'), {'payload': {'device_code': 'private-code'}, 'accepted_statuses': {200, 202}}), lambda: self.client.exchange_device_code('private-code')),
            ((('GET', '/api/v1/auth/me'), {}), self.client.current_user),
            ((('DELETE', '/api/v1/auth/tokens/token-id'), {}), lambda: self.client.revoke_access_token('token-id')),
        ]
        for expected, callback in cases:
            with self.subTest(path=expected[0][1]):
                self.assert_request(expected, callback)

    def test_repository_endpoint_contracts(self):
        """Preserve dataset resolution and repository browsing routes."""

        cases = [
            ((('GET', '/api/v1/datasets/resolve?path=lab%2Fimages'), {}), lambda: self.client.resolve_dataset('lab/images')),
            ((('GET', '/api/v1/capabilities'), {}), self.client.capabilities),
            ((('GET', '/api/v1/datasets/data-id/repository/revisions/resolve'), {}), lambda: self.client.resolve_revision('data-id')),
            ((('GET', '/api/v1/datasets/data-id/repository/revisions/resolve?revision=release%2Fv1'), {}), lambda: self.client.resolve_revision('data-id', revision='release/v1')),
            ((('GET', '/api/v1/datasets/data-id/repository/tree?revision=abc123&path=nested%2Fdata&limit=25&offset=50'), {}), lambda: self.client.list_repository_tree('data-id', revision='abc123', path='nested/data', limit=25, offset=50)),
            ((('GET', '/api/v1/datasets/data-id/repository/refs?kind=tags&limit=25&offset=50'), {}), lambda: self.client.list_repository_refs('data-id', kind='tags', limit=25, offset=50)),
            ((('GET', '/api/v1/datasets/data-id/repository/blob?revision=abc123&path=data%2Ffile.csv'), {}), lambda: self.client.get_repository_blob('data-id', revision='abc123', path='data/file.csv')),
            ((('GET', '/api/v1/datasets/data-id/repository/download?revision=abc123&path=data%2Ffile.csv'), {}), lambda: self.client.authorize_repository_download('data-id', revision='abc123', path='data/file.csv')),
            ((('GET', '/api/v1/datasets/data-id/repository/readme?revision=release%2Fv1'), {}), lambda: self.client.get_repository_readme('data-id', revision='release/v1')),
        ]
        for expected, callback in cases:
            with self.subTest(path=expected[0][1]):
                self.assert_request(expected, callback)

    def test_dataset_crud_endpoint_contracts(self):
        """Preserve namespace resolution and complete dataset CRUD routes."""

        cases = [
            ((('GET', '/api/v1/namespaces/resolve?path=research%2Fvision'), {}), lambda: self.client.resolve_namespace('research/vision')),
            ((('POST', '/api/v1/datasets'), {'payload': {'namespace_id': 'namespace-id', 'slug': 'images', 'name': 'Images'}}), lambda: self.client.create_dataset(namespace_id='namespace-id', slug='images', name='Images')),
            ((('GET', '/api/v1/datasets?limit=100&offset=0'), {}), self.client.list_datasets),
            ((('GET', '/api/v1/datasets?limit=25&offset=50&namespace_id=namespace-id'), {}), lambda: self.client.list_datasets(namespace_id='namespace-id', limit=25, offset=50)),
            ((('GET', '/api/v1/datasets/data-id'), {}), lambda: self.client.get_dataset('data-id')),
            ((('PATCH', '/api/v1/datasets/data-id'), {'payload': {}}), lambda: self.client.update_dataset('data-id')),
            ((('PATCH', '/api/v1/datasets/data-id'), {'payload': {'slug': 'new-slug', 'name': 'New name'}}), lambda: self.client.update_dataset('data-id', slug='new-slug', name='New name')),
            ((('DELETE', '/api/v1/datasets/data-id'), {}), lambda: self.client.delete_dataset('data-id')),
        ]
        for expected, callback in cases:
            with self.subTest(path=expected[0][1]):
                self.assert_request(expected, callback)

    def test_dataset_grant_endpoint_contracts(self):
        """Preserve user and group grant management routes."""

        cases = [
            ((('GET', '/api/v1/datasets/data-id/grants'), {}), lambda: self.client.list_dataset_grants('data-id')),
            ((('POST', '/api/v1/datasets/data-id/grants'), {'payload': {'role': 'reader', 'username': 'researcher'}}), lambda: self.client.create_dataset_grant('data-id', role='reader', username='researcher')),
            ((('POST', '/api/v1/datasets/data-id/grants'), {'payload': {'role': 'writer', 'group_path': 'research/vision'}}), lambda: self.client.create_dataset_grant('data-id', role='writer', group_path='research/vision')),
            ((('PATCH', '/api/v1/datasets/data-id/grants/grant-id'), {'payload': {'role': 'maintainer'}}), lambda: self.client.update_dataset_grant('data-id', 'grant-id', role='maintainer')),
            ((('DELETE', '/api/v1/datasets/data-id/grants/grant-id'), {}), lambda: self.client.delete_dataset_grant('data-id', 'grant-id')),
        ]
        for expected, callback in cases:
            with self.subTest(path=expected[0][1]):
                self.assert_request(expected, callback)


class HostValidationTests(unittest.TestCase):
    """Verify installation origins and redirect targets remain safe."""

    def test_normalize_host_accepts_https_and_loopback_http(self):
        """Apply HTTPS by default while preserving local development origins."""

        cases = {
            'data.example.test': 'https://data.example.test',
            ' https://DATA.example.test:8443/ ': 'https://DATA.example.test:8443',
            'http://localhost:8000/': 'http://localhost:8000',
            'http://127.0.0.1:8000': 'http://127.0.0.1:8000',
            'http://[::1]:8000': 'http://[::1]:8000',
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_host(value), expected)

    def test_normalize_host_rejects_non_origins_and_insecure_hosts(self):
        """Reject paths, credentials, URL decorations, and remote plain HTTP."""

        values = [
            '',
            'ftp://data.example.test',
            'https://user:secret@data.example.test',
            'https://data.example.test/subpath',
            'https://data.example.test?query=yes',
            'https://data.example.test#fragment',
            'http://data.example.test',
        ]
        for value in values:
            with self.subTest(value=value):
                with self.assertRaises(ConfigurationError):
                    normalize_host(value)

    def test_redirect_handler_allows_same_origin_relative_redirect(self):
        """Retain credentials only across same-origin redirects."""

        request = Request('https://data.example.test/api/v1/start', headers={'Authorization': 'Bearer secret'})
        redirected = SameOriginRedirectHandler().redirect_request(request, io.BytesIO(), 302, 'Found', {}, 'https://data.example.test/api/v1/end')

        self.assertEqual(redirected.full_url, 'https://data.example.test/api/v1/end')
        self.assertEqual(redirected.get_header('Authorization'), 'Bearer secret')

    def test_redirect_handler_rejects_cross_origin_redirect(self):
        """Prevent a redirect from forwarding credentials to another origin."""

        request = Request('https://data.example.test/api/v1/start', headers={'Authorization': 'Bearer secret'})
        with self.assertRaisesRegex(HTTPError, 'Cross-origin redirect refused') as context:
            SameOriginRedirectHandler().redirect_request(request, io.BytesIO(), 302, 'Found', {}, 'https://attacker.example/end')
        self.assertEqual(context.exception.code, 400)


if __name__ == '__main__':
    unittest.main()
