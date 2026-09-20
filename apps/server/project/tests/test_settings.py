import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase


SERVER_ROOT = Path(__file__).resolve().parents[2]


class DatabaseConfigurationTests(SimpleTestCase):
    """Keep PostgreSQL as the only supported control-plane database."""

    def test_sqlite_database_url_is_rejected(self):
        """Fail during settings loading rather than running against SQLite semantics."""

        environment = dict(os.environ)
        environment.update(
            {
                'NIYAN_DATABASE_URL': 'sqlite:///:memory:',
                'NIYAN_GIT_ROOT': '/tmp/niyan-settings-test-repositories',
                'NIYAN_S3_BUCKET': 'settings-test',
                'NIYAN_SECRET_KEY': 'settings-test-only-secret',
            }
        )

        result = subprocess.run(
            [sys.executable, '-c', 'import project.settings'],
            cwd=SERVER_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn('NIYAN_DATABASE_URL must use PostgreSQL.', result.stderr)
