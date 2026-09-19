from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase, override_settings


class HealthCheckTests(TestCase):
    """Protect the unauthenticated process and dependency probes."""

    def test_liveness_does_not_probe_dependencies(self):
        """Keep liveness usable while a dependency is recovering."""

        response = self.client.get('/health/live')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        self.assertEqual(response['Cache-Control'], 'max-age=0, no-cache, no-store, must-revalidate, private')

    def test_readiness_requires_database_and_writable_git_root(self):
        """Declare readiness only when both authoritative stores are usable."""

        with TemporaryDirectory() as directory, override_settings(REPOSITORIES_ROOT=Path(directory)):
            response = self.client.get('/health/ready')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok', 'checks': {'database': True, 'git_root': True}})

    @override_settings(REPOSITORIES_ROOT=Path('/a/path/that/does/not/exist'))
    def test_readiness_failure_does_not_disclose_backend_details(self):
        """Return a bounded status document for an unavailable dependency."""

        response = self.client.get('/health/ready')

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'unavailable', 'checks': {'database': True, 'git_root': False}})
        self.assertNotIn('/a/path', response.content.decode())

    def test_readiness_hides_database_exceptions(self):
        """Never expose connection strings or driver errors in health output."""

        with TemporaryDirectory() as directory, override_settings(REPOSITORIES_ROOT=Path(directory)), patch('project.health.connection.cursor', side_effect=RuntimeError('secret database detail')):
            response = self.client.get('/health/ready')

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()['checks']['database'])
        self.assertNotIn('secret database detail', response.content.decode())
