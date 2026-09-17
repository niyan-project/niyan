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


if __name__ == '__main__':
    unittest.main()
