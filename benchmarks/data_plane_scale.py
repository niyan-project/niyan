"""Benchmark Git LFS negotiation and real multi-gigabyte ranged reads."""

import argparse
import base64
import hashlib
import json
import os
import platform
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen
from wsgiref.simple_server import WSGIRequestHandler, make_server

import environ


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SERVER_ROOT = REPOSITORY_ROOT / 'apps' / 'server'
CLIENT_ROOT = REPOSITORY_ROOT / 'clients' / 'python' / 'src'
sys.path.insert(0, str(REPOSITORY_ROOT))

from deploy.rehearse_recovery import build_environment, delete_prefix, django_command, docker_database, s3_client


PART_SIZE = 64 * 1024 * 1024
PART_COUNT = 33
OBJECT_SIZE = PART_SIZE * PART_COUNT
READ_SIZE = 1024 * 1024


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *arguments):
        pass


def timed(operation):
    started = time.perf_counter()
    result = operation()
    return time.perf_counter() - started, result


def log(message):
    print(f'[data-plane benchmark] {message}', flush=True)


def run_git(*arguments, cwd=None):
    result = subprocess.run(['git', *arguments], cwd=cwd, check=False, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Git command failed.')
    return result.stdout.strip()


def create_pointer_commit(dataset, oid, size, workspace):
    from datasets.repositories import GitRepositoryStore

    run_git('init', '--initial-branch=main', str(workspace))
    run_git('config', 'user.name', 'Niyan scale benchmark', cwd=workspace)
    run_git('config', 'user.email', 'benchmark@example.invalid', cwd=workspace)
    (workspace / '.gitattributes').write_text('large.bin filter=lfs diff=lfs merge=lfs -text\n')
    (workspace / 'large.bin').write_text(f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {size}\n')
    run_git('add', '.gitattributes', 'large.bin', cwd=workspace)
    run_git('commit', '-m', 'Add multi-gigabyte benchmark object', cwd=workspace)
    run_git('remote', 'add', 'origin', str(GitRepositoryStore().path_for(dataset.id)), cwd=workspace)
    run_git('push', 'origin', 'main', cwd=workspace)


def assemble_object(client, bucket, seed_key, target_key, content):
    client.put_object(Bucket=bucket, Key=seed_key, Body=content, ContentLength=len(content))
    response = client.create_multipart_upload(Bucket=bucket, Key=target_key)
    upload_id = response['UploadId']
    parts = []
    try:
        for part_number in range(1, PART_COUNT + 1):
            copied = client.upload_part_copy(
                Bucket=bucket,
                Key=target_key,
                PartNumber=part_number,
                UploadId=upload_id,
                CopySource={'Bucket': bucket, 'Key': seed_key},
                CopySourceRange=f'bytes=0-{PART_SIZE - 1}',
            )
            parts.append({'PartNumber': part_number, 'ETag': copied['CopyPartResult']['ETag']})
        client.complete_multipart_upload(Bucket=bucket, Key=target_key, UploadId=upload_id, MultipartUpload={'Parts': parts})
    except Exception:
        client.abort_multipart_upload(Bucket=bucket, Key=target_key, UploadId=upload_id)
        raise


def negotiate_batch(origin, dataset_id, username, token, objects):
    credentials = base64.b64encode(f'{username}:{token}'.encode()).decode()
    request = Request(
        f'{origin}/git/{dataset_id}.git/info/lfs/objects/batch',
        data=json.dumps({'operation': 'upload', 'transfers': ['basic'], 'hash_algo': 'sha256', 'objects': objects}).encode(),
        method='POST',
        headers={
            'Authorization': f'Basic {credentials}',
            'Content-Type': 'application/vnd.git-lfs+json',
            'Accept': 'application/vnd.git-lfs+json',
        },
    )
    with urlopen(request, timeout=300) as response:
        body = json.load(response)
    if response.status != 200 or len(body.get('objects', [])) != len(objects):
        raise RuntimeError('The Git LFS batch response was incomplete.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=SERVER_ROOT / '.env')
    parser.add_argument('--output', type=Path)
    arguments = parser.parse_args()
    if os.environ.get('NIYAN_RUN_DATA_PLANE_BENCHMARK') != '1':
        raise SystemExit('Refusing to run without NIYAN_RUN_DATA_PLANE_BENCHMARK=1.')
    environ.Env.read_env(arguments.env_file, overwrite=False)
    required_s3 = ['NIYAN_S3_BUCKET', 'NIYAN_S3_ACCESS_KEY_ID', 'NIYAN_S3_SECRET_ACCESS_KEY']
    missing = [name for name in required_s3 if not os.environ.get(name)]
    if missing:
        raise SystemExit(f'Missing required S3 configuration: {", ".join(missing)}')

    run_id = uuid.uuid4().hex
    key_prefix = f'niyan-scale-benchmark/{run_id}'
    container_name = f'niyan-scale-{run_id[:12]}'
    database_password = secrets.token_urlsafe(24)
    bucket = os.environ['NIYAN_S3_BUCKET']
    client = s3_client()
    application_server = None
    container_started = False

    with tempfile.TemporaryDirectory(prefix='niyan-data-scale-') as temporary_directory:
        temporary_root = Path(temporary_directory)
        git_root = temporary_root / 'git'
        git_root.mkdir()
        try:
            database_port = docker_database(container_name, database_password)
            container_started = True
            environment = build_environment(database_port, database_password, git_root, key_prefix)
            environment['NIYAN_ALLOWED_HOSTS'] = '127.0.0.1,localhost'
            os.environ.update(environment)
            django_command(REPOSITORY_ROOT, environment, 'migrate', '--noinput')

            sys.path.insert(0, str(SERVER_ROOT))
            sys.path.insert(0, str(CLIENT_ROOT))
            os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings')
            import django

            django.setup()

            from django.conf import settings
            from django.core.wsgi import get_wsgi_application

            from accounts.models import AccessToken, User
            from accounts.tokens import create_access_token
            from datasets.lfs_transfers import finalize_lfs_upload
            from datasets.models import LfsObject
            from datasets.object_storage import S3ObjectStore
            from datasets.services import create_dataset
            from niyan.filesystem import NiyanFileSystem

            user = User.objects.create_superuser(username='scale-owner', email='scale@example.invalid', password='Benchmark-only-password-43!')
            dataset = create_dataset(namespace=user.personal_namespace, slug='scale-data', name='Scale data', created_by=user)
            _, raw_token = create_access_token(
                user=user,
                name='Scale benchmark',
                scopes=['api', 'read_repository', 'write_repository'],
                origin=AccessToken.Origin.CLI,
            )

            application_server = make_server('127.0.0.1', 0, get_wsgi_application(), handler_class=QuietHandler)
            server_thread = threading.Thread(target=application_server.serve_forever, daemon=True)
            server_thread.start()
            origin = f'http://127.0.0.1:{application_server.server_port}'

            batch_objects = [{'oid': hashlib.sha256(f'negotiation-{index}'.encode()).hexdigest(), 'size': 1} for index in range(100)]
            log('warming the 100-object Git LFS Batch negotiation')
            negotiate_batch(origin, dataset.id, user.username, raw_token, batch_objects)
            log('measuring five 100-object Git LFS Batch negotiations')
            negotiation_samples = [timed(lambda: negotiate_batch(origin, dataset.id, user.username, raw_token, batch_objects))[0] for _ in range(5)]

            part = b'\0' * PART_SIZE
            digest = hashlib.sha256()
            for _ in range(PART_COUNT):
                digest.update(part)
            oid = digest.hexdigest()
            lfs_object = LfsObject.objects.create(dataset=dataset, oid=oid, size=OBJECT_SIZE)
            object_store = S3ObjectStore(settings.NIYAN_S3_CONFIGURATION)
            seed_key = f'{key_prefix}/benchmark-seed'
            target_key = object_store._key(lfs_object.storage_key)
            log('assembling the 2.0625 GiB object through server-side multipart copies')
            assembly_seconds, _ = timed(lambda: assemble_object(client, bucket, seed_key, target_key, part))
            finalize_lfs_upload(lfs_object=lfs_object, object_store=object_store, verify_provider_sha256=False)
            create_pointer_commit(dataset, oid, OBJECT_SIZE, temporary_root / 'worktree')

            filesystem = NiyanFileSystem(host=origin, dataset=dataset.path, revision='main', token=raw_token, block_size=5 * 1024 * 1024)
            log('reading the first and last 1 MiB through the public fsspec client')
            with filesystem.open('large.bin', 'rb') as remote_file:
                first_seconds, first = timed(lambda: remote_file.read(READ_SIZE))
                remote_file.seek(OBJECT_SIZE - READ_SIZE)
                last_seconds, last = timed(lambda: remote_file.read(READ_SIZE))
            if first != b'\0' * READ_SIZE or last != b'\0' * READ_SIZE:
                raise RuntimeError('Ranged reads returned unexpected multi-gigabyte object content.')

            results = {
                'system': {'platform': platform.platform(), 'python': platform.python_version()},
                'object': {'size_bytes': OBJECT_SIZE, 'part_size_bytes': PART_SIZE, 'part_count': PART_COUNT, 'server_side_multipart_copy_seconds': assembly_seconds},
                'lfs_batch_100_seconds': negotiation_samples,
                'lfs_batch_100_median_seconds': sorted(negotiation_samples)[len(negotiation_samples) // 2],
                'fsspec_reads': {
                    'read_size_bytes': READ_SIZE,
                    'first_range_seconds': first_seconds,
                    'last_range_offset': OBJECT_SIZE - READ_SIZE,
                    'last_range_seconds': last_seconds,
                    'full_object_downloaded': False,
                },
            }
            encoded = json.dumps(results, indent=2) + '\n'
            if arguments.output:
                arguments.output.write_text(encoded)
            print(encoded)
        finally:
            if application_server is not None:
                application_server.shutdown()
                application_server.server_close()
            delete_prefix(client, bucket, f'{key_prefix}/')
            if container_started:
                subprocess.run(['docker', 'stop', '--time', '5', container_name], capture_output=True)


if __name__ == '__main__':
    main()
