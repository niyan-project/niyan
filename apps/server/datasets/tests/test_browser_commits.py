import io
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from datasets.browser_commits import BrowserDraftConflict, BrowserDraftInvalid, create_browser_draft, publish_browser_draft, stage_delete, stage_git_blob, stage_lfs_object
from datasets.models import BrowserCommitDraft, LfsObject
from datasets.services import create_dataset


@override_settings(NIYAN_BROWSER_DRAFT_LIFETIME_SECONDS=86400)
class BrowserCommitTests(TestCase):
    """Verify explicit browser drafts produce ordinary race-safe Git commits."""

    def setUp(self):
        """Create an owner, an empty dataset repository, and browser session."""

        self.repository_directory = TemporaryDirectory()
        self.repository_root = Path(self.repository_directory.name)
        self.settings_override = override_settings(REPOSITORIES_ROOT=self.repository_root)
        self.settings_override.enable()
        self.user = User.objects.create_user(username='researcher', email='researcher@example.test', password='password')
        self.dataset = create_dataset(namespace=self.user.personal_namespace, slug='images', name='Images', created_by=self.user)
        self.repository = self.repository_root / f'{self.dataset.id}.git'
        self.client = Client()
        self.client.force_login(self.user)

    def tearDown(self):
        """Restore repository settings and remove Git data."""

        self.settings_override.disable()
        self.repository_directory.cleanup()

    def test_publish_initial_git_file_creates_one_parentless_commit(self):
        """A bounded ordinary upload becomes a real Git blob and initial branch."""

        draft = create_browser_draft(dataset=self.dataset, user=self.user, target_branch='main')
        stage_git_blob(draft=draft, path='notes/readme.txt', size=6, stream=io.BytesIO(b'hello\n'))
        committed = publish_browser_draft(draft=draft, user=self.user, message='Add notes')

        self.assertEqual(committed.state, BrowserCommitDraft.State.COMMITTED)
        self.assertEqual(self._git('show', f'{committed.committed_oid}:notes/readme.txt').stdout, b'hello\n')
        parents = self._git('show', '-s', '--format=%P', committed.committed_oid).stdout.strip()
        self.assertEqual(parents, b'')

    def test_publish_rejects_branch_movement_and_leaves_draft_open(self):
        """Expected-old-object publication never commits onto a changed branch."""

        first = create_browser_draft(dataset=self.dataset, user=self.user, target_branch='main')
        stage_git_blob(draft=first, path='one.txt', size=4, stream=io.BytesIO(b'one\n'))
        first = publish_browser_draft(draft=first, user=self.user, message='First')
        draft = create_browser_draft(dataset=self.dataset, user=self.user, target_branch='main')
        stage_git_blob(draft=draft, path='two.txt', size=4, stream=io.BytesIO(b'two\n'))
        competing = self._git('commit-tree', self._git('show', '-s', '--format=%T', first.committed_oid).stdout.strip(), '-p', first.committed_oid, input_data=b'Competing\n', environment={'GIT_AUTHOR_NAME': 'Other', 'GIT_AUTHOR_EMAIL': 'other@example.test', 'GIT_COMMITTER_NAME': 'Other', 'GIT_COMMITTER_EMAIL': 'other@example.test'}).stdout.decode().strip()
        self._git('update-ref', 'refs/heads/main', competing, first.committed_oid)

        with self.assertRaises(BrowserDraftConflict):
            publish_browser_draft(draft=draft, user=self.user, message='Stale')

        draft.refresh_from_db()
        self.assertEqual(draft.state, BrowserCommitDraft.State.OPEN)

    def test_lfs_commit_writes_pointer_and_nearest_standard_attributes(self):
        """A verified LFS identity produces interoperable pointer and attribute blobs."""

        draft = create_browser_draft(dataset=self.dataset, user=self.user, target_branch='main')
        change = stage_lfs_object(draft=draft, path='raw/large file.bin', oid='a' * 64, size=123)
        lfs_object = change.lfs_object
        lfs_object.state = LfsObject.State.AVAILABLE
        lfs_object.verification_method = LfsObject.VerificationMethod.SIZE
        lfs_object.available_at = timezone.now()
        lfs_object.full_clean()
        lfs_object.save()
        committed = publish_browser_draft(draft=draft, user=self.user, message='Add LFS data')

        pointer = self._git('show', f'{committed.committed_oid}:raw/large file.bin').stdout.decode()
        attributes = self._git('show', f'{committed.committed_oid}:raw/.gitattributes').stdout.decode()
        self.assertIn(f'oid sha256:{"a" * 64}', pointer)
        self.assertIn('"/large file.bin" filter=lfs diff=lfs merge=lfs -text', attributes)
        lfs_object.refresh_from_db()
        self.assertEqual(lfs_object.state, LfsObject.State.REFERENCED)

    def test_path_collisions_and_nonexistent_deletes_are_rejected(self):
        """Draft paths cannot become both a file and directory or delete nothing."""

        draft = create_browser_draft(dataset=self.dataset, user=self.user, target_branch='main')
        stage_git_blob(draft=draft, path='table', size=1, stream=io.BytesIO(b'x'))
        with self.assertRaises(BrowserDraftInvalid):
            stage_git_blob(draft=draft, path='table/part.csv', size=1, stream=io.BytesIO(b'y'))
        with self.assertRaises(BrowserDraftInvalid):
            stage_delete(draft=draft, path='missing.txt')

    def test_api_keeps_drafts_private_and_streams_bounded_git_content(self):
        """Only the creator can stage and inspect one browser draft."""

        created = self.client.post(f'/api/v1/datasets/{self.dataset.id}/drafts', {'target_branch': 'main'}, content_type='application/json')
        self.assertEqual(created.status_code, 201)
        draft_id = created.json()['id']
        staged = self.client.put(f'/api/v1/datasets/{self.dataset.id}/drafts/{draft_id}/files/git?path=sample.txt&size=7', b'sample\n', content_type='application/octet-stream')
        self.assertEqual(staged.status_code, 200)
        self.assertEqual(staged.json()['changes'][0]['path'], 'sample.txt')

        intruder = User.objects.create_user(username='intruder', password='password')
        self.client.force_login(intruder)
        hidden = self.client.get(f'/api/v1/datasets/{self.dataset.id}/drafts/{draft_id}')
        self.assertIn(hidden.status_code, (403, 404))

    def test_api_rejects_ordinary_git_content_above_the_limit_before_reading(self):
        """A client cannot force oversized content through Django."""

        created = self.client.post(f'/api/v1/datasets/{self.dataset.id}/drafts', {'target_branch': 'main'}, content_type='application/json')
        response = self.client.put(f'/api/v1/datasets/{self.dataset.id}/drafts/{created.json()["id"]}/files/git?path=large.bin&size={10 * 1024 * 1024 + 1}', b'', content_type='application/octet-stream')

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()['code'], 'git_blob_too_large')

    def _git(self, *arguments, input_data=None, environment=None):
        """Run one test Git plumbing command against the fixture bare repository."""

        child_environment = None
        if environment is not None:
            child_environment = dict(environment)
            child_environment['PATH'] = str(Path('/usr/bin')) + ':' + str(Path('/opt/homebrew/bin'))
        return subprocess.run(['git', '--git-dir', str(self.repository), *[argument.decode() if isinstance(argument, bytes) else argument for argument in arguments]], input=input_data, env=child_environment, check=True, capture_output=True)
