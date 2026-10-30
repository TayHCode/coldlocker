from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULES = ('agents_sync', 'calllog', 'checks.scan', 'composio_connector', 'connect', 'executor_connector',
           'executor_sources', 'gateway', 'gateway_adapter', 'gateway_render', 'hub', 'hub_ui', 'importers',
           'lifecycle', 'mcp_endpoint', 'remote_publisher', 'safety', 'scanner', 'store', 'ui_auth', 'verify')

PRELUDE = '''
import getpass, os, subprocess, sys, tempfile, types, urllib.request
sys.path.insert(0, {root!r})
sys.modules['pwd'] = None
sys.modules['fcntl'] = None
for name in ('fchmod', 'getuid'):
    if hasattr(os, name):
        delattr(os, name)
calls = []
fake = types.ModuleType('msvcrt')
fake.LK_LOCK, fake.LK_NBLCK, fake.LK_UNLCK = 1, 2, 0
fake.locking = lambda fd, mode, size: calls.append((mode, size))
sys.modules['msvcrt'] = fake
'''


def run(code, *args):
    return subprocess.run([sys.executable, '-I', '-c', PRELUDE.format(root=str(ROOT)) + code, *args],
                          capture_output=True, text=True, cwd=ROOT, encoding='utf-8')


class WindowsCompatTests(unittest.TestCase):
    def test_every_module_imports_without_posix_only_features(self):
        for module in MODULES:
            with self.subTest(module=module):
                result = run(f'import {module}')
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_hub_help_runs_without_posix_only_features(self):
        result = run("import hub; sys.argv = ['hub.py', '--help']; sys.exit(hub.main())")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('usage', result.stdout)

    def test_store_write_and_call_log_run_without_posix_only_features(self):
        with tempfile.TemporaryDirectory() as directory:
            code = '''
from pathlib import Path
import calllog, store
root = Path(sys.argv[1])
store.FileStore(root).put('sample-token', 'value')
assert store.FileStore(root).has('sample-token')
calllog.append(root, 'client', 'tools/list', None, 1.0)
assert calllog.read(root)['calls']
assert calls
'''
            result = run(code, directory)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_windows_lock_reports_contention_as_blocking_error(self):
        code = '''
import errno, portable
def busy(fd, mode, size):
    raise PermissionError(errno.EACCES, 'locked')
sys.modules['msvcrt'].locking = busy
with open(sys.argv[1], 'a') as stream:
    try:
        portable.lock(stream, wait=False)
    except BlockingIOError:
        sys.exit(0)
sys.exit(1)
'''
        with tempfile.TemporaryDirectory() as directory:
            result = run(code, str(Path(directory) / 'sample.lock'))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_project_protection_uses_home_without_pwd(self):
        code = '''
import agents_sync
from pathlib import Path
from unittest.mock import patch
home = Path(os.path.abspath('/nonexistent-fixture-home'))
with patch('pathlib.Path.home', return_value=home):
    try:
        agents_sync.project(home / '.codex/a')
    except ValueError:
        sys.exit(0)
sys.exit(1)
'''
        result = run(code)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_project_protection_covers_the_real_home_without_pwd(self):
        code = '''
import agents_sync
from pathlib import Path
for name in ('.codex/a', '.claude', '.config/opencode'):
    try:
        agents_sync.project(Path.home() / name)
    except ValueError:
        continue
    sys.exit(1)
agents_sync.project(Path.home() / 'agentlocker-fixture-project')
sys.exit(0)
'''
        result = run(code)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
