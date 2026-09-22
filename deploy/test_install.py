"""Exercise the deployment bootstrap without public network access."""

import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / 'deploy' / 'install.sh'


class DeploymentBootstrapTests(unittest.TestCase):
    """Verify the reviewable and non-destructive bootstrap contract."""

    def setUp(self):
        """Create an isolated fake curl executable and destination root."""

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.fake_bin = self.root / 'bin'
        self.fake_bin.mkdir()
        fake_curl = self.fake_bin / 'curl'
        fake_curl.write_text(
            """#!/usr/bin/env python3
import pathlib
import sys

output = pathlib.Path(sys.argv[sys.argv.index('--output') + 1])
name = sys.argv[-1].rsplit('/', 1)[-1]
content = {
    'compose.yml': 'name: niyan\\n',
    'Caddyfile': '{$NIYAN_PUBLIC_HOST} { respond \\"test\\" }\\n',
    '.env.example': 'NIYAN_IMAGE=ghcr.io/niyan-project/niyan:0.3.0\\n',
    'README.md': '# Deployment\\n',
}[name]
output.write_text(content)
"""
        )
        fake_curl.chmod(0o755)
        self.environment = os.environ | {'PATH': f'{self.fake_bin}{os.pathsep}{os.environ["PATH"]}'}

    def tearDown(self):
        """Remove the isolated bootstrap filesystem."""

        self.temporary_directory.cleanup()

    def test_installs_only_the_deployment_bundle_and_private_environment(self):
        """Download the expected files and create a mode-0600 working environment."""

        destination = self.root / 'niyan-deploy'
        completed = subprocess.run(['/bin/sh', INSTALLER, destination], env=self.environment, check=True, capture_output=True, text=True)

        self.assertEqual({path.name for path in destination.iterdir()}, {'compose.yml', 'Caddyfile', '.env.example', '.env', 'README.md'})
        self.assertEqual((destination / '.env').read_text(), (destination / '.env.example').read_text())
        self.assertEqual(stat.S_IMODE((destination / '.env').stat().st_mode), 0o600)
        self.assertIn('has not started Docker', completed.stdout)

    def test_refuses_to_overwrite_a_nonempty_destination(self):
        """Preserve an existing operator directory without downloading or replacing files."""

        destination = self.root / 'occupied'
        destination.mkdir()
        marker = destination / 'keep.txt'
        marker.write_text('operator data')

        completed = subprocess.run(['/bin/sh', INSTALLER, destination], env=self.environment, capture_output=True, text=True)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('destination is not empty', completed.stderr)
        self.assertEqual(marker.read_text(), 'operator data')


if __name__ == '__main__':
    unittest.main()
