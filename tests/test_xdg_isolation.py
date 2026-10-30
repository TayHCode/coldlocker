import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import connect
import hub
from test_scanner import FIXTURES, ROOT, snapshot


class XdgIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.home = base / 'home'
        shutil.copytree(FIXTURES / 'linux', self.home)
        self.xdg = base / 'xdg'
        skill = self.xdg / 'opencode/skills/xdgleak'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: xdgleak\ndescription: outside the scanned home\n---\nbody\n', encoding='utf-8')
        (self.xdg / 'opencode/opencode.json').write_text('{"mcp": {"xdgleak": {"type": "local", "command": ["true"]}}}\n', encoding='utf-8')
        self.before = snapshot(self.xdg)

    def test_scan_ignores_xdg_config_home_with_alternate_home(self):
        args = hub.parser().parse_args(['scan', '--home', str(self.home), '--json'])
        stdout = io.StringIO()
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.xdg)}), \
                patch.object(sys.stdin, 'isatty', return_value=False), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(args.action(args), 0)
        self.assertTrue(json.loads(stdout.getvalue())['items'])
        self.assertNotIn('xdgleak', stdout.getvalue())
        self.assertEqual(snapshot(self.xdg), self.before)

    def test_scan_still_honours_xdg_config_home_for_real_home(self):
        args = hub.parser().parse_args(['scan', '--home', str(self.home), '--json'])
        stdout = io.StringIO()
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.xdg)}), \
                patch('pathlib.Path.home', return_value=self.home), \
                patch('scanner.default_runner', return_value=''), \
                patch('executor_connector.Session', side_effect=OSError), \
                patch.object(sys.stdin, 'isatty', return_value=False), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(args.action(args), 0)
        self.assertIn('xdgleak', stdout.getvalue())

    def test_connect_ignores_xdg_config_home_with_alternate_home(self):
        args = SimpleNamespace(home=str(self.home), platform='linux', agent=None, write=True, yes=True)
        output = io.StringIO()
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.xdg)}), \
                patch('connect.gateway_connection', return_value={'status': 'stopped', 'snippets': {}}):
            self.assertEqual(connect.run(args, ROOT, stdin=io.StringIO(), output=output), 0)
        self.assertNotIn(str(self.xdg), output.getvalue())
        self.assertEqual(snapshot(self.xdg), self.before)
        self.assertIn('agentlocker', (self.home / '.claude.json').read_text(encoding='utf-8'))

    def test_connect_cli_write_ignores_xdg_config_home(self):
        result = subprocess.run([sys.executable, 'hub.py', 'connect', '--home', str(self.home), '--write', '--yes'],
                                cwd=ROOT, capture_output=True, text=True,
                                env={**os.environ, 'XDG_CONFIG_HOME': str(self.xdg), 'PYTHONUTF8': '1'}, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(str(self.xdg), result.stdout)
        self.assertEqual(snapshot(self.xdg), self.before)


if __name__ == '__main__':
    unittest.main()
