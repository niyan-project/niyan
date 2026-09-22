"""Exercise a many-pointer chunked push through production Caddy and Gunicorn."""

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = ROOT / 'deploy' / 'compose.yml'
POINTER_COUNT = 19000


def run(*arguments, cwd=None, environment=None, check=True):
    """Run one bounded command and retain diagnostics for assertions."""

    return subprocess.run(arguments, cwd=cwd, env=environment, check=check, capture_output=True, text=True, timeout=180)


def server_shell(source):
    """Run trusted fixture setup or inspection inside the deployed server."""

    completed = run('docker', 'compose', '--file', str(COMPOSE_FILE), 'exec', '-T', 'server', 'python', 'manage.py', 'shell', '-c', source, cwd=ROOT)
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith('{'):
            return json.loads(line)
    raise AssertionError(f'Deployment shell returned no JSON object: {completed.stdout}')


def oid_for(index):
    """Derive the deterministic fixture object identifier."""

    return hashlib.sha256(f'production-pipeline-{index}'.encode()).hexdigest()


def main():
    """Push 19,000 pointers through an HTTP/1.1 chunked request and verify state."""

    fixture = server_shell(
        "import hashlib,json; from django.utils import timezone; from accounts.models import AccessToken,User; from accounts.tokens import create_access_token; from datasets.models import LfsObject; from datasets.services import create_dataset; "
        "user=User.objects.create_superuser(username='pipeline-check',password='Pipeline-check-only-47!'); dataset=create_dataset(namespace=user.personal_namespace,slug='pipeline-check',name='Pipeline check',created_by=user); token,raw=create_access_token(user=user,name='Production pipeline check',scopes=['write_repository'],origin=AccessToken.Origin.CLI,dataset=dataset); now=timezone.now(); "
        f"LfsObject.objects.bulk_create([LfsObject(dataset=dataset,oid=hashlib.sha256(f'production-pipeline-{{index}}'.encode()).hexdigest(),size=index+1,state=LfsObject.State.AVAILABLE,verification_method=LfsObject.VerificationMethod.SIZE,available_at=now) for index in range({POINTER_COUNT})],batch_size=1000); "
        "print(json.dumps({'dataset_id':str(dataset.id),'username':user.username,'token':raw}))"
    )
    https_port = os.environ.get('NIYAN_HTTPS_PORT', '443')
    origin = f'https://localhost:{https_port}'
    remote = f"{origin}/git/{fixture['dataset_id']}.git"
    authorization = base64.b64encode(f"{fixture['username']}:{fixture['token']}".encode()).decode()

    with tempfile.TemporaryDirectory(prefix='niyan-production-pipeline-') as temporary_directory:
        repository = Path(temporary_directory)
        run('git', 'init', '--initial-branch=main', str(repository))
        run('git', 'config', 'user.name', 'Niyan deployment test', cwd=repository)
        run('git', 'config', 'user.email', 'deployment-test@example.invalid', cwd=repository)
        pointers = repository / 'pointers'
        pointers.mkdir()
        for index in range(POINTER_COUNT):
            (pointers / f'{index:05d}.bin').write_text(f'version https://git-lfs.github.com/spec/v1\noid sha256:{oid_for(index)}\nsize {index + 1}\n')
        run('git', 'add', '--all', cwd=repository)
        run('git', 'commit', '-m', 'Add production pipeline fixture', cwd=repository)
        run('git', 'remote', 'add', 'origin', remote, cwd=repository)
        run('git', 'config', 'http.sslVerify', 'false', cwd=repository)
        run('git', 'config', 'http.version', 'HTTP/1.1', cwd=repository)
        run('git', 'config', 'http.extraHeader', f'Authorization: Basic {authorization}', cwd=repository)

        environment = os.environ | {'GIT_TRACE_CURL': '1'}
        started = time.perf_counter()
        pushed = run('git', 'push', '--set-upstream', 'origin', 'main', cwd=repository, environment=environment)
        elapsed = time.perf_counter() - started
        trace = pushed.stderr.lower()
        if 'transfer-encoding: chunked' not in trace:
            raise AssertionError('The production push did not exercise HTTP/1.1 chunked request framing.')
        if elapsed >= 100:
            raise AssertionError(f'The production many-pointer push exceeded its 100-second budget: {elapsed:.2f}s')
        local_head = run('git', 'rev-parse', 'HEAD', cwd=repository).stdout.strip()
        remote_head = run('git', 'ls-remote', 'origin', 'refs/heads/main', cwd=repository).stdout.split()[0]
        if remote_head != local_head:
            raise AssertionError('The production push did not advance the authoritative ref.')

    state = server_shell(
        "import json; from datasets.models import Dataset,GitPushContext,LfsObject; "
        f"dataset=Dataset.objects.get(pk='{fixture['dataset_id']}'); context=GitPushContext.objects.filter(dataset=dataset).latest('created_at'); "
        "print(json.dumps({'referenced':LfsObject.objects.filter(dataset=dataset,state=LfsObject.State.REFERENCED).count(),'validated':context.validated_at is not None,'completed':context.completed_at is not None}))"
    )
    if state != {'referenced': POINTER_COUNT, 'validated': True, 'completed': True}:
        raise AssertionError(f'Production bookkeeping is incomplete: {state}')
    print(json.dumps({'chunked_push_seconds': elapsed, 'pointer_count': POINTER_COUNT, **state}, sort_keys=True))


if __name__ == '__main__':
    main()
