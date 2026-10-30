from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import gateway
import gateway_render
import scanner
from test_scanner import ROOT

WINDOWS_PYTHON = 'C:\\Python312\\python.exe'


class PythonCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def windows(self):
        return patch('sys.platform', 'win32'), patch('sys.executable', WINDOWS_PYTHON)

    def scan_hints(self):
        home = self.base / 'home'
        (home / '.gemini').mkdir(parents=True, exist_ok=True)
        hints = [scanner.Inventory(home, (), 'linux', {}, lambda command: '', ROOT).discover()['not_scanned']['next_step'],
                 scanner.next_step('schedule is kept by the Claude Desktop app')]
        for changed in (True, False):
            item = {'kind': 'rule', 'agent': 'codex', 'name': 'x', 'source': '~/.codex/AGENTS.md'}
            scanner.conflict(item, 'reason', changed)
            hints.append(item['next_step'])
        return hints

    def gateway_errors(self, *calls):
        errors = []
        for call in calls:
            with patch.object(gateway, 'ROOT', self.base), self.assertRaises(gateway.SetupError) as raised:
                call()
            errors.append(str(raised.exception))
        return errors

    def test_scan_next_steps_use_the_running_interpreter_on_windows(self):
        platform, executable = self.windows()
        with platform, executable:
            hints = self.scan_hints()
        self.assertEqual(len(hints), 4)
        for hint in hints:
            self.assertNotIn('python3', hint)
            self.assertIn(WINDOWS_PYTHON + ' hub.py import-', hint)

    def test_scan_next_steps_say_python3_elsewhere(self):
        with patch('sys.platform', 'linux'):
            hints = self.scan_hints()
        for hint in hints:
            self.assertIn('python3 hub.py import-', hint)

    def test_gateway_error_text_uses_the_running_interpreter_on_windows(self):
        platform, executable = self.windows()
        with platform, executable:
            errors = self.gateway_errors(gateway.require_supported, gateway.check_runtime)
        self.assertEqual(len(errors), 2)
        for error in errors:
            self.assertNotIn('python3', error)
            self.assertIn(WINDOWS_PYTHON + ' ', error)

    def test_gateway_error_text_says_python3_elsewhere(self):
        with patch('sys.platform', 'linux'):
            errors = self.gateway_errors(gateway.check_runtime)
        self.assertIn('python3 gateway.py install', errors[0])

    def test_example_server_starts_with_the_running_interpreter_on_windows(self):
        root = self.base / 'locker'
        root.mkdir()
        shutil.copyfile(ROOT / 'manifest.json', root / 'manifest.json')
        for name in ('mcp', 'examples'):
            shutil.copytree(ROOT / name, root / name)
        platform, executable = self.windows()
        with platform, executable:
            self.assertEqual(gateway_render.render(root)['mcpServers']['example-server']['command'], WINDOWS_PYTHON)
        with patch('sys.platform', 'linux'):
            self.assertEqual(gateway_render.render(root)['mcpServers']['example-server']['command'], 'python3')


if __name__ == '__main__':
    unittest.main()
