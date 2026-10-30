import contextlib
import io
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import hub
from test_scanner import FIXTURES


class ScanIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.home = base / 'home'
        shutil.copytree(FIXTURES / 'linux', self.home)
        self.log = base / 'crontab-calls.log'
        stub = base / 'bin'
        stub.mkdir()
        script = stub / 'crontab'
        script.write_text(f'#!/bin/sh\necho called >> "{self.log}"\necho "no crontab for user" >&2\nexit 1\n', encoding='utf-8')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        self.path = f'{stub}{os.pathsep}{os.environ["PATH"]}'

    def scan(self, *argv, home=None):
        args = hub.parser().parse_args(['scan', '--home', str(home or self.home), '--json', *argv])
        session = MagicMock()
        session.__enter__.return_value = session
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {'PATH': self.path}), \
                patch('executor_connector.Session', return_value=session) as constructor, \
                patch('executor_sources.invoke', return_value={'integrations': []}), \
                patch.object(sys.stdin, 'isatty', return_value=False), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = args.action(args)
        calls = len(self.log.read_text(encoding='utf-8').splitlines()) if self.log.exists() else 0
        return code, calls, constructor.call_count, stderr.getvalue()

    def test_alternate_home_never_calls_crontab_or_executor(self):
        code, crontab_calls, sessions, error = self.scan()
        self.assertEqual((code, crontab_calls, sessions), (0, 0, 0))
        self.assertEqual(len(error.strip().splitlines()), 1)
        self.assertIn('--live', error)

    @unittest.skipIf(os.name == 'nt', 'the crontab stub is a POSIX shell script and Windows scans never read crontab')
    def test_live_flag_enables_crontab_and_executor(self):
        code, crontab_calls, sessions, error = self.scan('--live')
        self.assertEqual(code, 0)
        self.assertGreaterEqual(crontab_calls, 1)
        self.assertEqual(sessions, 1)
        self.assertEqual(error, '')

    @unittest.skipIf(os.name == 'nt', 'the crontab stub is a POSIX shell script and Windows scans never read crontab')
    def test_real_home_stays_live(self):
        with patch('pathlib.Path.home', return_value=self.home):
            code, crontab_calls, sessions, error = self.scan(home=self.home)
        self.assertEqual(code, 0)
        self.assertGreaterEqual(crontab_calls, 1)
        self.assertEqual(sessions, 1)
        self.assertEqual(error, '')


if __name__ == '__main__':
    unittest.main()
