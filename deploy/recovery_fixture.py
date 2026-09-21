"""Seed and validate the disposable state used by the recovery rehearsal.

This helper is intentionally separate from Django management commands so the
orchestrator can run it against both a tagged source tree and the current one.
It is not an operator-facing backup tool.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def _configure_django():
    source_root = Path(os.environ['NIYAN_REHEARSAL_SOURCE_ROOT']).resolve()
    sys.path.insert(0, str(source_root))
    os.chdir(source_root)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings')

    import django

    django.setup()


def _run_git(*arguments, cwd=None):
    return subprocess.run(['git', *arguments], cwd=cwd, check=True, capture_output=True, text=True, timeout=60).stdout.strip()


def seed(manifest_path):
    from django.conf import settings

    from accounts.models import AccessToken, User
    from accounts.tokens import create_access_token
    from datasets.lfs_transfers import finalize_lfs_upload
    from datasets.models import LfsObject
    from datasets.object_storage import S3ObjectStore
    from datasets.repositories import GitRepositoryStore
    from datasets.services import create_dataset

    user = User.objects.create_superuser(username='recovery-owner', email='recovery@example.invalid', password='Recovery-only-password-43!')
    dataset = create_dataset(
        namespace=user.personal_namespace,
        slug='recovery-fixture',
        name='Recovery fixture',
        description='Disposable coordinated recovery rehearsal data.',
        created_by=user,
    )
    _, raw_token = create_access_token(
        user=user,
        name='Recovery rehearsal',
        scopes=['api', 'read_repository', 'write_repository'],
        origin=AccessToken.Origin.CLI,
    )

    lfs_content = b'Niyan coordinated recovery rehearsal LFS payload\n'
    oid = hashlib.sha256(lfs_content).hexdigest()
    lfs_object = LfsObject.objects.create(dataset=dataset, oid=oid, size=len(lfs_content))
    object_store = S3ObjectStore(settings.NIYAN_S3_CONFIGURATION)
    object_store.client.put_object(
        Bucket=object_store.configuration.bucket,
        Key=object_store._key(lfs_object.storage_key),
        Body=lfs_content,
        ContentLength=len(lfs_content),
    )
    finalize_lfs_upload(lfs_object=lfs_object, object_store=object_store, verify_provider_sha256=False)

    workspace = manifest_path.parent / 'seed-worktree'
    workspace.mkdir()
    _run_git('init', '--initial-branch=main', str(workspace))
    _run_git('config', 'user.name', 'Niyan recovery rehearsal', cwd=workspace)
    _run_git('config', 'user.email', 'recovery@example.invalid', cwd=workspace)
    (workspace / '.gitattributes').write_text('payload.bin filter=lfs diff=lfs merge=lfs -text\n')
    (workspace / 'README.md').write_text('# Recovery fixture\n\nThis dataset exists only during a recovery rehearsal.\n')
    (workspace / 'payload.bin').write_text(f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(lfs_content)}\n')
    _run_git('add', '.gitattributes', 'README.md', 'payload.bin', cwd=workspace)
    _run_git('commit', '-m', 'Add recovery fixture', cwd=workspace)
    repository_path = GitRepositoryStore().path_for(dataset.id)
    _run_git('remote', 'add', 'origin', str(repository_path), cwd=workspace)
    _run_git('push', 'origin', 'main', cwd=workspace)
    commit = _run_git('rev-parse', 'HEAD', cwd=workspace)

    manifest_path.write_text(
        json.dumps(
            {
                'username': user.username,
                'token': raw_token,
                'dataset_id': str(dataset.id),
                'dataset_path': dataset.path,
                'commit': commit,
                'lfs_oid': oid,
                'lfs_size': len(lfs_content),
                'lfs_sha256': oid,
            }
        )
    )
    manifest_path.chmod(0o600)


def validate(manifest_path):
    from django.conf import settings

    from accounts.tokens import authenticate_access_token
    from datasets.models import Dataset, LfsObject
    from datasets.object_storage import S3ObjectStore
    from datasets.policies import can_read_dataset, can_write_repository
    from datasets.repositories import GitRepositoryStore

    manifest = json.loads(manifest_path.read_text())
    token = authenticate_access_token(manifest['token'])
    dataset = Dataset.objects.select_related('namespace__parent').get(pk=manifest['dataset_id'])
    if token.user.username != manifest['username'] or dataset.path != manifest['dataset_path']:
        raise RuntimeError('The restored identity or dataset path does not match the recovery point.')
    if not can_read_dataset(user=token.user, dataset=dataset) or not can_write_repository(user=token.user, dataset=dataset):
        raise RuntimeError('The restored owner no longer has the expected dataset access.')

    repository_path = GitRepositoryStore().existing_path(dataset.id)
    _run_git('--git-dir', str(repository_path), 'fsck', '--full')
    commit = _run_git('--git-dir', str(repository_path), 'rev-parse', 'refs/heads/main')
    if commit != manifest['commit']:
        raise RuntimeError('The restored Git ref does not match the recovery point.')

    lfs_object = LfsObject.objects.get(dataset=dataset, oid=manifest['lfs_oid'])
    if lfs_object.state not in {LfsObject.State.AVAILABLE, LfsObject.State.REFERENCED}:
        raise RuntimeError('The restored Git LFS object is not available.')
    object_store = S3ObjectStore(settings.NIYAN_S3_CONFIGURATION)
    response = object_store.client.get_object(Bucket=object_store.configuration.bucket, Key=object_store._key(lfs_object.storage_key))
    content = response['Body'].read()
    if len(content) != manifest['lfs_size'] or hashlib.sha256(content).hexdigest() != manifest['lfs_sha256']:
        raise RuntimeError('The restored Git LFS bytes do not match the recovery point.')


def reconcile_interrupted_deletion():
    from django.conf import settings
    from django.utils import timezone

    from accounts.models import User
    from datasets.models import Dataset
    from datasets.object_storage import S3ObjectStore
    from datasets.repositories import GitRepositoryStore
    from datasets.services import create_dataset, delete_dataset

    user = User.objects.get(username='recovery-owner')
    dataset = create_dataset(namespace=user.personal_namespace, slug='interrupted-deletion', name='Interrupted deletion', created_by=user)
    store = S3ObjectStore(settings.NIYAN_S3_CONFIGURATION)
    store.client.put_object(Bucket=store.configuration.bucket, Key=store._key(f'datasets/{dataset.id}/rehearsal-marker'), Body=b'delete me')

    # This is the durable state left when a process exits after deletion is
    # hidden but before either backing store has been cleaned.
    Dataset.objects.filter(pk=dataset.pk).update(deletion_started_at=timezone.now())
    delete_dataset(dataset=dataset, deleted_by=user)

    if Dataset.objects.filter(pk=dataset.pk).exists():
        raise RuntimeError('Interrupted dataset deletion did not remove database state.')
    if GitRepositoryStore().path_for(dataset.id).exists():
        raise RuntimeError('Interrupted dataset deletion did not remove Git state.')
    response = store.client.list_objects_v2(Bucket=store.configuration.bucket, Prefix=store._key(f'datasets/{dataset.id}') + '/')
    if response.get('KeyCount', 0):
        raise RuntimeError('Interrupted dataset deletion did not remove object-storage state.')


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in {'seed', 'validate', 'reconcile-deletion'}:
        raise SystemExit('usage: recovery_fixture.py {seed|validate|reconcile-deletion} MANIFEST')
    _configure_django()
    command = sys.argv[1]
    manifest_path = Path(sys.argv[2]).resolve()
    if command == 'seed':
        seed(manifest_path)
    elif command == 'validate':
        validate(manifest_path)
    else:
        reconcile_interrupted_deletion()


if __name__ == '__main__':
    main()
