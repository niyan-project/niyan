import subprocess
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AccessToken, User
from accounts.tokens import create_access_token
from datasets.git_push import GitPushDenied, create_push_context, enforce_pre_receive, parse_lfs_pointer, reconcile_git_pushes, record_post_receive
from datasets.models import DatasetGrant, GitPushLfsLease, GitPushRef, LfsObject
from datasets.services import create_dataset
from namespaces.models import NamespaceMembership


class GitPushPolicyTests(TestCase):
    """Exercise ref and LFS policy against real Git object graphs."""

    def setUp(self):
        """Create one empty dataset repository and one local object producer."""

        self.repository_directory = TemporaryDirectory()
        self.working_directory = TemporaryDirectory()
        self.repository_root = Path(self.repository_directory.name)
        self.settings_override = override_settings(REPOSITORIES_ROOT=self.repository_root)
        self.settings_override.enable()
        self.user = User.objects.create_user(username='researcher')
        self.dataset = create_dataset(namespace=self.user.personal_namespace, slug='images', name='Images', created_by=self.user)
        self.access_token, _ = create_access_token(user=self.user, name='Push token', scopes=['write_repository'], origin=AccessToken.Origin.CLI)
        self.repository = self.repository_root / f'{self.dataset.id}.git'
        self.working = Path(self.working_directory.name)
        self._git('init', '--initial-branch=main', str(self.working))
        self._git('-C', str(self.working), 'config', 'user.name', 'Researcher')
        self._git('-C', str(self.working), 'config', 'user.email', 'researcher@example.test')

    def tearDown(self):
        """Restore repository settings and remove temporary Git data."""

        self.settings_override.disable()
        self.working_directory.cleanup()
        self.repository_directory.cleanup()

    def test_unborn_main_and_fast_forward_updates_are_valid(self):
        """Allow contributor branch creation and an ordinary descendant update."""

        initial = self._commit('data.txt', 'initial\n', 'Initial')
        self._import(initial)
        first_context = create_push_context(dataset=self.dataset, access_token=self.access_token)

        enforce_pre_receive(
            context_id=first_context.id,
            dataset_id=self.dataset.id,
            lines=[f'{"0" * 40} {initial} refs/heads/main\n'],
            repository_path=self.repository,
        )
        self._git('--git-dir', str(self.repository), 'update-ref', 'refs/heads/main', initial)
        record_post_receive(
            context_id=first_context.id,
            dataset_id=self.dataset.id,
            lines=[f'{"0" * 40} {initial} refs/heads/main\n'],
            repository_path=self.repository,
        )

        updated = self._commit('data.txt', 'updated\n', 'Update')
        self._import(updated)
        second_context = create_push_context(dataset=self.dataset, access_token=self.access_token)
        enforce_pre_receive(
            context_id=second_context.id,
            dataset_id=self.dataset.id,
            lines=[f'{initial} {updated} refs/heads/main\n'],
            repository_path=self.repository,
        )

        self.assertTrue(GitPushRef.objects.filter(push_context=second_context, ref_name='refs/heads/main', accepted_at__isnull=True).exists())

    def test_unavailable_lfs_pointer_rejects_the_entire_proposal(self):
        """Keep refs invisible when newly reachable history needs missing content."""

        oid = 'a' * 64
        pointer = f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize 12\n'
        commit = self._commit('large.bin', pointer, 'Add pointer')
        self._import(commit)
        context = create_push_context(dataset=self.dataset, access_token=self.access_token)

        with self.assertRaisesRegex(GitPushDenied, f'refs/heads/main: {oid}'):
            enforce_pre_receive(
                context_id=context.id,
                dataset_id=self.dataset.id,
                lines=[f'{"0" * 40} {commit} refs/heads/main\n'],
                repository_path=self.repository,
            )

        self.assertFalse(GitPushRef.objects.filter(push_context=context).exists())
        self.assertNotEqual(self._git('--git-dir', str(self.repository), 'show-ref', '--verify', 'refs/heads/main', check=False).returncode, 0)

    def test_available_lfs_object_is_leased_then_promoted_after_acceptance(self):
        """Close cleanup races before publication and mark accepted content referenced."""

        oid = 'b' * 64
        pointer = f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize 12\n'
        commit = self._commit('large.bin', pointer, 'Add pointer')
        self._import(commit)
        lfs_object = LfsObject.objects.create(
            dataset=self.dataset,
            oid=oid,
            size=12,
            state=LfsObject.State.AVAILABLE,
            verification_method=LfsObject.VerificationMethod.SIZE,
            available_at=timezone.now(),
        )
        context = create_push_context(dataset=self.dataset, access_token=self.access_token)
        update = f'{"0" * 40} {commit} refs/heads/main\n'

        enforce_pre_receive(context_id=context.id, dataset_id=self.dataset.id, lines=[update], repository_path=self.repository)

        lease = GitPushLfsLease.objects.get(push_ref__push_context=context, lfs_object=lfs_object)
        self.assertGreater(lease.expires_at, timezone.now())
        self._git('--git-dir', str(self.repository), 'update-ref', 'refs/heads/main', commit)
        record_post_receive(context_id=context.id, dataset_id=self.dataset.id, lines=[update], repository_path=self.repository)
        lfs_object.refresh_from_db()
        context.refresh_from_db()
        self.assertEqual(lfs_object.state, LfsObject.State.REFERENCED)
        self.assertIsNotNone(context.completed_at)
        self.assertIsNotNone(context.ref_updates.get().accepted_at)

    def test_shared_history_is_leased_for_each_proposed_ref(self):
        """Keep shared LFS content protected whichever proposed ref Git accepts."""

        oid = '1' * 64
        pointer = f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize 12\n'
        commit = self._commit('large.bin', pointer, 'Add pointer')
        self._import(commit)
        lfs_object = LfsObject.objects.create(
            dataset=self.dataset,
            oid=oid,
            size=12,
            state=LfsObject.State.AVAILABLE,
            verification_method=LfsObject.VerificationMethod.SIZE,
            available_at=timezone.now(),
        )
        context = create_push_context(dataset=self.dataset, access_token=self.access_token)
        zero = '0' * 40

        enforce_pre_receive(
            context_id=context.id,
            dataset_id=self.dataset.id,
            lines=[f'{zero} {commit} refs/heads/main\n', f'{zero} {commit} refs/heads/secondary\n'],
            repository_path=self.repository,
        )

        self.assertEqual(GitPushLfsLease.objects.filter(push_ref__push_context=context, lfs_object=lfs_object).count(), 2)

    def test_reconciliation_recovers_a_missed_post_receive_update(self):
        """Use authoritative Git refs to replay promotion after post-receive failure."""

        oid = 'f' * 64
        pointer = f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize 12\n'
        commit = self._commit('large.bin', pointer, 'Add pointer')
        self._import(commit)
        lfs_object = LfsObject.objects.create(
            dataset=self.dataset,
            oid=oid,
            size=12,
            state=LfsObject.State.AVAILABLE,
            verification_method=LfsObject.VerificationMethod.SIZE,
            available_at=timezone.now(),
        )
        context = create_push_context(dataset=self.dataset, access_token=self.access_token)
        update = f'{"0" * 40} {commit} refs/heads/main\n'
        enforce_pre_receive(context_id=context.id, dataset_id=self.dataset.id, lines=[update], repository_path=self.repository)
        self._git('--git-dir', str(self.repository), 'update-ref', 'refs/heads/main', commit)

        reconciled = reconcile_git_pushes(now=context.expires_at + timedelta(seconds=1))

        lfs_object.refresh_from_db()
        context.refresh_from_db()
        self.assertEqual(reconciled, 1)
        self.assertEqual(lfs_object.state, LfsObject.State.REFERENCED)
        self.assertIsNotNone(context.ref_updates.get().accepted_at)
        self.assertIsNotNone(context.completed_at)

    def test_force_update_default_deletion_tag_update_and_other_namespaces_are_denied(self):
        """Apply the immutable baseline even when a client explicitly requests force."""

        initial = self._commit('data.txt', 'initial\n', 'Initial')
        self._git('-C', str(self.working), 'checkout', '--orphan', 'unrelated')
        self._git('-C', str(self.working), 'rm', '-rf', '.')
        unrelated = self._commit('other.txt', 'other\n', 'Unrelated')
        self._import(initial)
        self._import(unrelated)
        self._git('--git-dir', str(self.repository), 'update-ref', 'refs/heads/main', initial)
        self._git('--git-dir', str(self.repository), 'update-ref', 'refs/tags/v1', initial)

        proposals = [
            f'{initial} {unrelated} refs/heads/main\n',
            f'{initial} {"0" * 40} refs/heads/main\n',
            f'{initial} {unrelated} refs/tags/v1\n',
            f'{"0" * 40} {unrelated} refs/notes/test\n',
        ]
        for proposal in proposals:
            context = create_push_context(dataset=self.dataset, access_token=self.access_token)
            with self.assertRaises(GitPushDenied):
                enforce_pre_receive(context_id=context.id, dataset_id=self.dataset.id, lines=[proposal], repository_path=self.repository)

    def test_contributor_may_delete_feature_branch_but_not_tag(self):
        """Keep destructive tag administration at maintainer while allowing branch cleanup."""

        commit = self._commit('data.txt', 'initial\n', 'Initial')
        self._import(commit)
        self._git('--git-dir', str(self.repository), 'update-ref', 'refs/heads/feature', commit)
        self._git('--git-dir', str(self.repository), 'update-ref', 'refs/tags/v1', commit)
        collaborator = User.objects.create_user(username='collaborator')
        grant = DatasetGrant.objects.create(dataset=self.dataset, user=collaborator, role=NamespaceMembership.Role.CONTRIBUTOR)
        token, _ = create_access_token(user=collaborator, name='Contributor push', scopes=['write_repository'], origin=AccessToken.Origin.CLI)
        zero = '0' * 40

        branch_context = create_push_context(dataset=self.dataset, access_token=token)
        enforce_pre_receive(
            context_id=branch_context.id,
            dataset_id=self.dataset.id,
            lines=[f'{commit} {zero} refs/heads/feature\n'],
            repository_path=self.repository,
        )
        tag_context = create_push_context(dataset=self.dataset, access_token=token)
        with self.assertRaisesRegex(GitPushDenied, 'maintainer'):
            enforce_pre_receive(
                context_id=tag_context.id,
                dataset_id=self.dataset.id,
                lines=[f'{commit} {zero} refs/tags/v1\n'],
                repository_path=self.repository,
            )

        grant.role = NamespaceMembership.Role.MAINTAINER
        grant.save(update_fields=['role', 'updated_at'])
        maintainer_context = create_push_context(dataset=self.dataset, access_token=token)
        enforce_pre_receive(
            context_id=maintainer_context.id,
            dataset_id=self.dataset.id,
            lines=[f'{commit} {zero} refs/tags/v1\n'],
            repository_path=self.repository,
        )

    def test_context_rechecks_token_state_before_policy(self):
        """Reject a token revoked after receive-pack authorization but before the hook."""

        commit = self._commit('data.txt', 'initial\n', 'Initial')
        self._import(commit)
        context = create_push_context(dataset=self.dataset, access_token=self.access_token)
        self.access_token.revoked_at = timezone.now()
        self.access_token.save(update_fields=['revoked_at'])

        with self.assertRaisesRegex(GitPushDenied, 'no longer permits'):
            enforce_pre_receive(
                context_id=context.id,
                dataset_id=self.dataset.id,
                lines=[f'{"0" * 40} {commit} refs/heads/main\n'],
                repository_path=self.repository,
            )

    def _commit(self, path, content, message):
        """Create and return one local commit."""

        target = self.working / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        self._git('-C', str(self.working), 'add', '--', path)
        self._git('-C', str(self.working), 'commit', '-m', message)
        return self._git('-C', str(self.working), 'rev-parse', 'HEAD').stdout.strip()

    def _import(self, commit):
        """Copy one commit graph into the bare repository without retaining a ref."""

        temporary_ref = f'refs/heads/import-{commit[:12]}'
        self._git('-C', str(self.working), 'push', str(self.repository), f'{commit}:{temporary_ref}')
        self._git('--git-dir', str(self.repository), 'update-ref', '-d', temporary_ref)

    def _git(self, *arguments, check=True):
        """Run one isolated Git fixture operation."""

        return subprocess.run(['git', *arguments], check=check, capture_output=True, text=True)


class LfsPointerParsingTests(TestCase):
    """Recognize only the canonical Git LFS pointer representation."""

    def test_canonical_pointer_and_sorted_extensions_are_recognized(self):
        """Accept standard v1 fields and extension values without parsing formats."""

        oid = 'c' * 64
        pointer = f'version https://git-lfs.github.com/spec/v1\next-0-example value with spaces\noid sha256:{oid}\nsize 123\n'.encode()

        self.assertEqual(parse_lfs_pointer(pointer), (oid, 123))

    def test_noncanonical_pointer_encodings_are_ordinary_blobs(self):
        """Reject missing newlines, uppercase hashes, leading-zero sizes, and unsorted keys."""

        oid = 'd' * 64
        invalid = [
            f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize 12'.encode(),
            f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid.upper()}\nsize 12\n'.encode(),
            f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize 012\n'.encode(),
            f'version https://git-lfs.github.com/spec/v1\nsize 12\noid sha256:{oid}\n'.encode(),
        ]

        self.assertTrue(all(parse_lfs_pointer(pointer) is None for pointer in invalid))
