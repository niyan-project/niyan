from pathlib import Path
from tempfile import TemporaryDirectory

from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, override_settings

from project.web import spa_entry


class SpaEntryTests(SimpleTestCase):
    """Verify the generated Nuxt application boundary."""

    def setUp(self):
        """Create a disposable generated entry document."""

        self.temporary_directory = TemporaryDirectory()
        self.web_root = Path(self.temporary_directory.name)
        (self.web_root / 'index.html').write_text('<!doctype html><title>Niyān</title>')
        self.requests = RequestFactory()

    def tearDown(self):
        """Remove the disposable frontend build."""

        self.temporary_directory.cleanup()

    @override_settings()
    def test_client_side_route_serves_entry_document(self):
        """Serve the same SPA document for an ordinary deep link."""

        with override_settings(NIYAN_WEB_DIST_ROOT=self.web_root):
            response = spa_entry(self.requests.get('/aryan/dataset'), route='aryan/dataset')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'<!doctype html><title>Niy\xc4\x81n</title>')

    def test_reserved_route_is_not_handled_by_spa(self):
        """Keep Django-owned URL prefixes outside the client router."""

        with self.assertRaises(Http404):
            spa_entry(self.requests.get('/api/missing'), route='api/missing')

    def test_mutating_method_is_rejected(self):
        """Never accept mutations through the static entry view."""

        response = spa_entry(self.requests.post('/aryan/dataset'), route='aryan/dataset')

        self.assertEqual(response.status_code, 405)
