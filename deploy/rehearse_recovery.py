"""Run a destructive, isolated coordinated backup and recovery rehearsal."""

import argparse
import os
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import boto3
import environ
from botocore.config import Config


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SERVER_ROOT = REPOSITORY_ROOT / 'apps' / 'server'
FIXTURE_SCRIPT = Path(__file__).resolve().parent / 'recovery_fixture.py'


def run(arguments, *, cwd=None, environment=None, input_bytes=None, timeout=300):
    return subprocess.run(
        [str(argument) for argument in arguments],
        cwd=cwd,
        env=environment,
        input=input_bytes,
        check=True,
        capture_output=True,
        timeout=timeout,
    )


def log(message):
    print(f'[recovery rehearsal] {message}', flush=True)


def docker_database(container_name, password):
    run(
        [
            'docker',
            'run',
            '--detach',
            '--rm',
            '--name',
            container_name,
            '--env',
            'POSTGRES_USER=niyan',
            '--env',
            f'POSTGRES_PASSWORD={password}',
            '--env',
            'POSTGRES_DB=niyan',
            '--publish',
            '127.0.0.1::5432',
            'postgres:17-alpine',
        ],
        timeout=180,
    )
    for _ in range(60):
        readiness = subprocess.run(['docker', 'exec', container_name, 'pg_isready', '--username', 'niyan', '--dbname', 'niyan'], capture_output=True)
        if readiness.returncode == 0:
            port_output = run(['docker', 'port', container_name, '5432/tcp']).stdout.decode().strip()
            return int(port_output.rsplit(':', 1)[1])
        time.sleep(1)
    raise RuntimeError('The disposable PostgreSQL container did not become ready.')


def reset_database(container_name):
    run(['docker', 'exec', container_name, 'dropdb', '--force', '--username', 'niyan', 'niyan'])
    run(['docker', 'exec', container_name, 'createdb', '--username', 'niyan', 'niyan'])


def dump_database(container_name, backup_path):
    backup_path.write_bytes(run(['docker', 'exec', container_name, 'pg_dump', '--username', 'niyan', '--dbname', 'niyan', '--format', 'custom']).stdout)


def restore_database(container_name, backup_path):
    run(
        ['docker', 'exec', '--interactive', container_name, 'pg_restore', '--username', 'niyan', '--dbname', 'niyan', '--no-owner', '--no-privileges'],
        input_bytes=backup_path.read_bytes(),
    )


def extract_source(reference, destination):
    archive_path = destination.parent / 'source.tar'
    run(['git', 'archive', '--format=tar', '--output', archive_path, reference], cwd=REPOSITORY_ROOT)
    destination.mkdir()
    with tarfile.open(archive_path) as archive:
        archive.extractall(destination, filter='data')


def s3_client():
    session_arguments = {'region_name': os.environ.get('NIYAN_S3_REGION', 'us-east-1')}
    access_key = os.environ.get('NIYAN_S3_ACCESS_KEY_ID')
    if access_key:
        session_arguments.update(
            aws_access_key_id=access_key,
            aws_secret_access_key=os.environ['NIYAN_S3_SECRET_ACCESS_KEY'],
            aws_session_token=os.environ.get('NIYAN_S3_SESSION_TOKEN'),
        )
    return boto3.session.Session(**session_arguments).client(
        's3',
        endpoint_url=os.environ.get('NIYAN_S3_ENDPOINT_URL'),
        verify=os.environ.get('NIYAN_S3_VERIFY_TLS', 'true').lower() not in {'0', 'false', 'no'},
        config=Config(
            signature_version=os.environ.get('NIYAN_S3_SIGNATURE_VERSION', 's3v4'),
            s3={'addressing_style': os.environ.get('NIYAN_S3_ADDRESSING_STYLE', 'auto')},
            retries={'max_attempts': 3, 'mode': 'standard'},
        ),
    )


def list_keys(client, bucket, prefix):
    keys = []
    paginator = client.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(item['Key'] for item in page.get('Contents', []))
    return keys


def copy_prefix(client, bucket, source_prefix, destination_prefix):
    source_keys = list_keys(client, bucket, source_prefix)
    for source_key in source_keys:
        suffix = source_key[len(source_prefix) :]
        client.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': source_key}, Key=f'{destination_prefix}{suffix}')
    return len(source_keys)


def delete_prefix(client, bucket, prefix):
    uploads = client.list_multipart_uploads(Bucket=bucket, Prefix=prefix)
    for upload in uploads.get('Uploads', []):
        client.abort_multipart_upload(Bucket=bucket, Key=upload['Key'], UploadId=upload['UploadId'])
    keys = list_keys(client, bucket, prefix)
    for offset in range(0, len(keys), 1000):
        client.delete_objects(Bucket=bucket, Delete={'Objects': [{'Key': key} for key in keys[offset : offset + 1000]], 'Quiet': True})


def django_command(source_root, environment, *arguments):
    run([sys.executable, 'manage.py', *arguments], cwd=source_root / 'apps' / 'server', environment=environment, timeout=300)


def fixture_command(source_root, environment, command, manifest_path):
    fixture_environment = environment | {'NIYAN_REHEARSAL_SOURCE_ROOT': str(source_root / 'apps' / 'server')}
    run([sys.executable, FIXTURE_SCRIPT, command, manifest_path], environment=fixture_environment, timeout=300)


def build_environment(database_port, database_password, git_root, key_prefix):
    environment = os.environ.copy()
    environment.update(
        NIYAN_SECRET_KEY=secrets.token_urlsafe(48),
        NIYAN_DEBUG='true',
        NIYAN_ALLOWED_HOSTS='localhost',
        NIYAN_CSRF_TRUSTED_ORIGINS='http://localhost',
        NIYAN_DATABASE_URL=f'postgresql://niyan:{quote(database_password)}@127.0.0.1:{database_port}/niyan',
        NIYAN_GIT_ROOT=str(git_root),
        NIYAN_S3_KEY_PREFIX=key_prefix,
        NIYAN_SECURE_SSL_REDIRECT='false',
        NIYAN_SESSION_COOKIE_SECURE='false',
        NIYAN_CSRF_COOKIE_SECURE='false',
        NIYAN_LOG_LEVEL='WARNING',
    )
    return environment


def restore_git(backup_root, git_root):
    shutil.rmtree(git_root)
    shutil.copytree(backup_root, git_root, copy_function=shutil.copy2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=SERVER_ROOT / '.env', help='Environment file containing disposable S3 credentials.')
    parser.add_argument('--source-ref', default='v0.1.0', help='Tagged source revision that creates the recovery point.')
    arguments = parser.parse_args()

    if os.environ.get('NIYAN_RUN_RECOVERY_REHEARSAL') != '1':
        raise SystemExit('Refusing to run without NIYAN_RUN_RECOVERY_REHEARSAL=1.')
    if not arguments.env_file.is_file():
        raise SystemExit(f'Environment file not found: {arguments.env_file}')
    environ.Env.read_env(arguments.env_file, overwrite=False)
    required_s3 = ['NIYAN_S3_BUCKET', 'NIYAN_S3_ACCESS_KEY_ID', 'NIYAN_S3_SECRET_ACCESS_KEY']
    missing = [name for name in required_s3 if not os.environ.get(name)]
    if missing:
        raise SystemExit(f'Missing required S3 configuration: {", ".join(missing)}')

    run_id = uuid.uuid4().hex
    root_prefix = f'niyan-recovery-rehearsal/{run_id}'
    source_prefix = f'{root_prefix}/source'
    backup_prefix = f'{root_prefix}/backup'
    bucket = os.environ['NIYAN_S3_BUCKET']
    container_name = f'niyan-recovery-{run_id[:12]}'
    database_password = secrets.token_urlsafe(24)
    client = s3_client()
    started = time.monotonic()
    container_started = False

    with tempfile.TemporaryDirectory(prefix='niyan-recovery-') as temporary_directory:
        temporary_root = Path(temporary_directory)
        source_root = temporary_root / 'source'
        git_root = temporary_root / 'git'
        git_backup = temporary_root / 'git-backup'
        database_backup = temporary_root / 'database.dump'
        manifest_path = temporary_root / 'manifest.json'
        git_root.mkdir()

        try:
            if list_keys(client, bucket, root_prefix):
                raise RuntimeError('The unique rehearsal prefix unexpectedly already contains objects.')
            log(f'extracting source release {arguments.source_ref}')
            extract_source(arguments.source_ref, source_root)
            log('starting disposable PostgreSQL 17')
            database_port = docker_database(container_name, database_password)
            container_started = True
            environment = build_environment(database_port, database_password, git_root, source_prefix)

            log('migrating and seeding the source release')
            django_command(source_root, environment, 'migrate', '--noinput')
            fixture_command(source_root, environment, 'seed', manifest_path)
            fixture_command(source_root, environment, 'validate', manifest_path)

            log('creating coordinated PostgreSQL, Git, and S3 recovery artifacts')
            dump_database(container_name, database_backup)
            shutil.copytree(git_root, git_backup, copy_function=shutil.copy2)
            copied_objects = copy_prefix(client, bucket, f'{source_prefix}/', f'{backup_prefix}/')
            if copied_objects < 1:
                raise RuntimeError('The recovery point did not contain an S3 object.')

            log('destroying all source stores before restore')
            reset_database(container_name)
            shutil.rmtree(git_root)
            git_root.mkdir()
            delete_prefix(client, bucket, f'{source_prefix}/')

            log('restoring the coordinated recovery point into a clean installation')
            restore_database(container_name, database_backup)
            restore_git(git_backup, git_root)
            copy_prefix(client, bucket, f'{backup_prefix}/', f'{source_prefix}/')

            log('upgrading the restored release to the current source tree')
            django_command(REPOSITORY_ROOT, environment, 'migrate', '--noinput')
            fixture_command(REPOSITORY_ROOT, environment, 'validate', manifest_path)
            fixture_command(REPOSITORY_ROOT, environment, 'reconcile-deletion', manifest_path)

            log('proving rollback by restoring the original coordinated recovery point')
            reset_database(container_name)
            restore_database(container_name, database_backup)
            restore_git(git_backup, git_root)
            delete_prefix(client, bucket, f'{source_prefix}/')
            copy_prefix(client, bucket, f'{backup_prefix}/', f'{source_prefix}/')
            django_command(source_root, environment, 'migrate', '--check')
            fixture_command(source_root, environment, 'validate', manifest_path)

            elapsed = time.monotonic() - started
            log(f'PASS: coordinated restore, upgrade, deletion reconciliation, and rollback completed in {elapsed:.1f}s')
        finally:
            delete_prefix(client, bucket, f'{root_prefix}/')
            if container_started:
                subprocess.run(['docker', 'stop', '--time', '5', container_name], capture_output=True)


if __name__ == '__main__':
    main()
