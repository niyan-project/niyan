"""Benchmark Git and Niyān repository browsing at large path counts."""

import argparse
import json
import os
import platform
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace


def timed(label, operation):
    started = time.perf_counter()
    result = operation()
    elapsed = time.perf_counter() - started
    print(f'{label}: {elapsed:.3f}s', flush=True)
    return elapsed, result


def run(arguments, *, cwd=None, input_bytes=None, stdout=subprocess.PIPE, timeout=1800, environment=None):
    command = [str(argument) for argument in arguments]
    result = subprocess.run(command, cwd=cwd, env=environment, input=input_bytes, check=False, stdout=stdout, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        detail = result.stderr.decode(errors='replace').strip()
        raise RuntimeError(f'{command[0]} exited with status {result.returncode}: {detail}')
    return result


def initialize_repository(repository_path, path_count):
    run(['git', 'init', '--bare', '--initial-branch=main', repository_path])
    process = subprocess.Popen(['git', '--git-dir', str(repository_path), 'fast-import', '--quiet'], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        process.stdin.write(b'blob\nmark :1\ndata 2\nx\n\ncommit refs/heads/main\nmark :2\nauthor Niyan benchmark <benchmark@example.invalid> 1700000000 +0000\ncommitter Niyan benchmark <benchmark@example.invalid> 1700000000 +0000\ndata 17\nBenchmark fixture\n')
        for index in range(path_count):
            shard = index // 1000
            process.stdin.write(f'M 100644 :1 shard-{shard:06d}/file-{index:09d}.dat\n'.encode())
        process.stdin.write(b'\ndone\n')
        process.stdin.close()
        stderr = process.stderr.read()
        return_code = process.wait(timeout=1800)
    except Exception:
        process.kill()
        raise
    if return_code:
        raise RuntimeError(f'git fast-import failed: {stderr.decode(errors="replace")}')
    run(['git', '--git-dir', repository_path, 'repack', '-ad'])


def count_tree(repository_path):
    process = subprocess.Popen(['git', '--git-dir', str(repository_path), 'ls-tree', '-r', '--name-only', 'main'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    count = sum(1 for _ in process.stdout)
    stderr = process.stderr.read()
    if process.wait(timeout=1800):
        raise RuntimeError(f'git ls-tree failed: {stderr.decode(errors="replace")}')
    return count


def browse_with_niyan(source_root, repository_root, dataset_id, path_count):
    sys.path.insert(0, str(source_root / 'apps' / 'server'))
    from datasets.repositories import GitRepositoryStore
    from datasets.repository_browser import RepositoryBrowser

    dataset = SimpleNamespace(id=dataset_id)
    browser = RepositoryBrowser(dataset, repository_store=GitRepositoryStore(repository_root))
    root_seconds, root_page = timed('Niyān root tree page', lambda: browser.list_tree(revision='main', path='', limit=100, offset=0))
    last_root_offset = max(0, ((path_count + 999) // 1000) - 100)
    last_root_seconds, last_root_page = timed('Niyān last root tree page', lambda: browser.list_tree(revision='main', path='', limit=100, offset=last_root_offset))
    shard_seconds, shard_page = timed('Niyān 100-entry shard page', lambda: browser.list_tree(revision='main', path='shard-000000', limit=100, offset=900))
    return {
        'root_page_seconds': root_seconds,
        'root_page_items': len(root_page[1]),
        'last_root_page_seconds': last_root_seconds,
        'last_root_page_items': len(last_root_page[1]),
        'shard_page_seconds': shard_seconds,
        'shard_page_items': len(shard_page[1]),
    }


def benchmark(path_count, work_root, source_root):
    case_root = work_root / str(path_count)
    repository_root = case_root / 'repositories'
    dataset_id = uuid.uuid4()
    repository_path = repository_root / f'{dataset_id}.git'
    checkout = case_root / 'checkout'
    repository_root.mkdir(parents=True)

    create_seconds, _ = timed(f'create {path_count:,}-path repository', lambda: initialize_repository(repository_path, path_count))
    browse_seconds, tree_count = timed('Git recursive tree browse', lambda: count_tree(repository_path))
    if tree_count != path_count:
        raise RuntimeError(f'Expected {path_count} paths but Git returned {tree_count}.')
    niyan_results = browse_with_niyan(source_root, repository_root, dataset_id, path_count)

    clone_seconds, _ = timed(
        'Git shallow clone and checkout',
        lambda: run(['git', 'clone', '--depth=1', '--single-branch', '--branch=main', '--no-tags', f'file://{repository_path}', checkout], timeout=1800),
    )
    run(['git', 'config', 'user.name', 'Niyan benchmark'], cwd=checkout)
    run(['git', 'config', 'user.email', 'benchmark@example.invalid'], cwd=checkout)
    status_seconds, status = timed('Git status', lambda: run(['git', 'status', '--porcelain'], cwd=checkout))
    if status.stdout:
        raise RuntimeError('The benchmark checkout is unexpectedly dirty.')

    original_commit = run(['git', '--git-dir', repository_path, 'rev-parse', 'main']).stdout.strip().decode()
    update_stream = (
        'blob\nmark :3\ndata 8\nchanged\ncommit refs/heads/main\nmark :4\n'
        'author Niyan benchmark <benchmark@example.invalid> 1700000001 +0000\n'
        'committer Niyan benchmark <benchmark@example.invalid> 1700000001 +0000\n'
        'data 16\nBenchmark update\n'
        f'from {original_commit}\nM 100644 :3 shard-000000/file-000000000.dat\n\ndone\n'
    ).encode()
    run(['git', '--git-dir', repository_path, 'fast-import', '--quiet'], input_bytes=update_stream)
    fetch_seconds, _ = timed('Git fetch one changed blob', lambda: run(['git', 'fetch', 'origin', 'main'], cwd=checkout))
    run(['git', 'reset', '--hard', 'origin/main'], cwd=checkout)
    push_commit_seconds, _ = timed('Git create empty commit', lambda: run(['git', 'commit', '--allow-empty', '-m', 'Benchmark push'], cwd=checkout))
    push_seconds, _ = timed('Git push one commit', lambda: run(['git', 'push', 'origin', 'HEAD:main'], cwd=checkout))

    repository_bytes = int(run(['du', '-sk', repository_path]).stdout.split()[0]) * 1024
    checkout_bytes = int(run(['du', '-sk', checkout]).stdout.split()[0]) * 1024
    return {
        'path_count': path_count,
        'create_seconds': create_seconds,
        'recursive_tree_browse_seconds': browse_seconds,
        'shallow_clone_checkout_seconds': clone_seconds,
        'status_seconds': status_seconds,
        'fetch_seconds': fetch_seconds,
        'push_commit_seconds': push_commit_seconds,
        'push_seconds': push_seconds,
        'bare_repository_bytes': repository_bytes,
        'checkout_bytes': checkout_bytes,
        'niyan_repository_browser': niyan_results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--paths', type=int, nargs='+', default=[100_000, 1_000_000])
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--work-root', type=Path)
    parser.add_argument('--keep', action='store_true')
    arguments = parser.parse_args()
    if any(path_count < 1 or path_count % 1000 for path_count in arguments.paths):
        raise SystemExit('Every path count must be a positive multiple of 1,000.')

    temporary = None
    if arguments.work_root is None:
        temporary = tempfile.TemporaryDirectory(prefix='niyan-scale-')
        work_root = Path(temporary.name)
    else:
        work_root = arguments.work_root.resolve()
        work_root.mkdir(parents=True, exist_ok=False)

    try:
        results = {
            'system': {
                'platform': platform.platform(),
                'python': platform.python_version(),
                'git': run(['git', '--version']).stdout.decode().strip(),
                'cpu_count': os.cpu_count(),
                'peak_rss_bytes': None,
            },
            'cases': [benchmark(path_count, work_root, arguments.source_root.resolve()) for path_count in arguments.paths],
        }
        native_peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        results['system']['peak_rss_bytes'] = native_peak_rss if sys.platform == 'darwin' else native_peak_rss * 1024
        encoded = json.dumps(results, indent=2) + '\n'
        if arguments.output:
            arguments.output.write_text(encoded)
        print(encoded)
    finally:
        if temporary is not None and not arguments.keep:
            temporary.cleanup()
        elif arguments.work_root is not None and not arguments.keep:
            shutil.rmtree(work_root)


if __name__ == '__main__':
    main()
