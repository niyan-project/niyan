import io
import subprocess
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from niyan.cache import _parse_prunable_oids, _run_lfs_prune, prune_cache, show_cache_status
from niyan.cli import build_parser
from niyan.config import CheckoutIdentity
from niyan.errors import GitError


class CacheCommandTests(unittest.TestCase):
    """Verify Niyān delegates cache eligibility to safe Git LFS pruning."""

    def setUp(self):
        """Create a canonical local Git LFS object directory."""

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.checkout = self.root / 'checkout'
        self.checkout.mkdir()
        self.cache = self.checkout / '.git' / 'lfs' / 'objects'
        self.protected_oid = 'a' * 64
        self.reclaimable_oid = 'b' * 64
        self._write_object(self.protected_oid, b'protected')
        self._write_object(self.reclaimable_oid, b'reclaimable-data')
        temporary = self.cache / 'incomplete'
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(b'ignored')
        self.identity = CheckoutIdentity(
            host='https://niyan.example',
            dataset_id='22222222-2222-2222-2222-222222222222',
            dataset_path='researcher/images',
        )

    def tearDown(self):
        """Remove isolated object storage."""

        self.temporary_directory.cleanup()

    def test_status_reports_location_total_protected_and_reclaimable_bytes(self):
        """Render cache accounting from Git LFS's verified dry-run candidates."""

        output = io.StringIO()
        with self._session(), patch('niyan.cache._run_lfs', side_effect=self._preview_results()):
            report = show_cache_status(paths=None, stores=None, cwd=self.checkout, stdout=output)

        self.assertEqual(report.total_objects, 2)
        self.assertEqual(report.protected_bytes, len(b'protected'))
        self.assertEqual(report.reclaimable_bytes, len(b'reclaimable-data'))
        self.assertIn(f'Location: {self.cache.resolve()}', output.getvalue())
        self.assertIn('Reachable/protected:', output.getvalue())
        self.assertIn('Reclaimable:', output.getvalue())

    def test_dry_run_never_invokes_mutating_prune(self):
        """Stop after Git LFS's verified preview when --dry-run is selected."""

        output = io.StringIO()
        run_lfs = self._run_lfs_mock(self._preview_results())
        with self._session(), patch('niyan.cache._run_lfs', side_effect=run_lfs):
            prune_cache(paths=None, stores=None, dry_run=True, cwd=self.checkout, stdout=output)

        self.assertEqual(run_lfs.call_count, 2)
        self.assertTrue((self.cache / self.reclaimable_oid[:2] / self.reclaimable_oid[2:4] / self.reclaimable_oid).exists())
        self.assertIn('No files were deleted.', output.getvalue())

    def test_prune_requires_explicit_confirmation(self):
        """Leave every object untouched when the answer is not affirmative."""

        output = io.StringIO()
        run_lfs = self._run_lfs_mock(self._preview_results())
        with self._session(), patch('niyan.cache._run_lfs', side_effect=run_lfs):
            prune_cache(paths=None, stores=None, cwd=self.checkout, stdin=io.StringIO('no\n'), stdout=output)

        self.assertEqual(run_lfs.call_count, 2)
        self.assertIn('Prune cancelled.', output.getvalue())

    def test_confirmed_prune_rechecks_policy_and_reports_actual_bytes(self):
        """Run a fresh verified Git LFS prune only after affirmative input."""

        target = self.cache / self.reclaimable_oid[:2] / self.reclaimable_oid[2:4] / self.reclaimable_oid
        responses = self._preview_results()

        def run_lfs(**kwargs):
            if responses:
                return responses.pop(0)
            self.assertFalse(kwargs['arguments'][0:2] == ['prune', '--dry-run'])
            target.unlink()
            return subprocess.CompletedProcess([], 0, stdout='', stderr='')

        output = io.StringIO()
        with self._session(), patch('niyan.cache._run_lfs', side_effect=run_lfs):
            prune_cache(paths=None, stores=None, cwd=self.checkout, stdin=io.StringIO('yes\n'), stdout=output)

        self.assertFalse(target.exists())
        self.assertIn(f'Reclaimed {len(b"reclaimable-data")} B.', output.getvalue())

    def test_unfamiliar_verbose_preview_fails_closed(self):
        """Refuse deletion when candidate detail cannot be reconciled with the summary."""

        with self.assertRaisesRegex(GitError, 'unfamiliar prune preview'):
            _parse_prunable_oids('2 files would be pruned (10 B)\n * aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa (4 B)\n')

    def test_prune_invocation_uses_remote_verification_without_force(self):
        """Keep Git LFS's current, recent, stash, worktree, and unpushed protections."""

        result = subprocess.CompletedProcess([], 0, stdout='', stderr='')
        with patch('niyan.cache.subprocess.run', return_value=result) as run:
            _run_lfs_prune(
                checkout=self.checkout,
                command_prefix=['git', '-c', 'credential.helper=test'],
                remote='origin',
                environment={'PATH': '/usr/bin'},
                dry_run=False,
            )

        command = run.call_args.args[0]
        self.assertIn('lfs.pruneremotetocheck=origin', command)
        self.assertIn('--verify-remote', command)
        self.assertIn('--when-unverified=halt', command)
        self.assertNotIn('--force', command)
        self.assertNotIn('--recent', command)

    def test_cli_exposes_cache_status_and_dry_run_prune(self):
        """Keep cache reclamation behind the accepted public command grammar."""

        parser = build_parser()

        status = parser.parse_args(['cache', 'status'])
        prune = parser.parse_args(['cache', 'prune', '--dry-run'])

        self.assertEqual((status.command, status.cache_command), ('cache', 'status'))
        self.assertEqual((prune.command, prune.cache_command, prune.dry_run), ('cache', 'prune', True))

    def _write_object(self, oid, content):
        """Write one canonical Git LFS cache object."""

        path = self.cache / oid[:2] / oid[2:4] / oid
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def _preview_results(self):
        """Return deterministic Git LFS env and verbose prune output."""

        return [
            subprocess.CompletedProcess([], 0, stdout=f'LocalMediaDir={self.cache}\n', stderr=''),
            subprocess.CompletedProcess([], 0, stdout=f'2 local objects, 1 retained, done.\n1 file would be pruned (16 B)\n * {self.reclaimable_oid} (16 B)\n', stderr=''),
        ]

    def _session(self):
        """Patch authenticated checkout setup while retaining the context boundary."""

        return patch(
            'niyan.cache._authenticated_cache',
            return_value=nullcontext((self.checkout, self.identity, ['git'], {'PATH': '/usr/bin'})),
        )

    @staticmethod
    def _run_lfs_mock(responses):
        """Return a call-counting mock with ordered process results."""

        from unittest.mock import Mock

        return Mock(side_effect=responses)


class CacheReportParsingTests(unittest.TestCase):
    """Exercise report parsing independently of authentication and subprocess setup."""

    def test_zero_candidate_preview_is_valid_without_verbose_summary(self):
        """Treat Git LFS's retained-only output as an empty reclaimable set."""

        self.assertEqual(_parse_prunable_oids('3 local objects, 3 retained, done.\n'), ())
