import subprocess
import sys
import unittest


class PackageBoundaryTests(unittest.TestCase):
    """Verify the unified distribution keeps its runtime boundaries."""

    def test_library_imports_do_not_initialize_cli_dependencies(self):
        """Keep ordinary and filesystem imports free of CLI side effects."""

        program = """
import sys

import niyan
import niyan.filesystem

unexpected = {'keyring', 'niyan.cli', 'niyan.git'}.intersection(sys.modules)
if unexpected:
    raise AssertionError(f'Unexpected modules imported: {sorted(unexpected)}')
"""

        subprocess.run([sys.executable, '-c', program], check=True, capture_output=True, text=True)

    def test_filesystem_metadata_operates_without_git_git_lfs_or_cli(self):
        """Exercise the registered filesystem with repository executables unavailable."""

        program = """
import os
import shutil
import subprocess

import fsspec

os.environ['PATH'] = ''
if shutil.which('git') is not None or shutil.which('git-lfs') is not None:
    raise AssertionError('Repository executable unexpectedly remained available')

def forbidden_subprocess(*args, **kwargs):
    raise AssertionError('The filesystem client invoked a subprocess')

subprocess.run = forbidden_subprocess

class Api:
    def __init__(self, host, *, token=None, timeout=30):
        pass

    def capabilities(self):
        return 200, {'api_versions': ['v1'], 'filesystem': {'protocol_version': 1, 'features': ['dataset-path-resolution', 'exact-revision-resolution', 'repository-metadata', 'git-blob-reads', 'authorized-lfs-download-actions']}}

    def resolve_dataset(self, locator_path):
        return 200, {'id': 'dataset-id', 'path': 'lab/images', 'repository_path': 'sample.csv'}

    def resolve_revision(self, dataset_id, *, revision=None):
        return 200, {'resolved_commit': 'a' * 40}

    def get_repository_blob(self, dataset_id, *, revision, path):
        return 200, {'object_id': 'b' * 40, 'size': 7, 'is_lfs': False, 'lfs_object_id': None, 'lfs_size': None}

filesystem = fsspec.filesystem('niyan', token='test-token', api_factory=Api)
info = filesystem.info('niyan://data.example.test/lab/images/sample.csv?revision=main')
if info['size'] != 7 or info['resolved_commit'] != 'a' * 40:
    raise AssertionError(f'Unexpected metadata: {info!r}')
"""

        subprocess.run([sys.executable, '-c', program], check=True, capture_output=True, text=True)


if __name__ == '__main__':
    unittest.main()
