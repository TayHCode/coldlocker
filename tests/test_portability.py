import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import connect
import hub_ui


ROOT = Path(__file__).resolve().parents[1]


class PortabilityTests(unittest.TestCase):
    def test_modules_import_without_fcntl(self):
        for module in ('hub_ui', 'verify', 'connect'):
            code = f"import sys; sys.path.insert(0, {str(ROOT)!r}); sys.modules['fcntl'] = None; import {module}"
            result = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_entries_use_the_running_interpreter(self):
        for agent in connect.AGENTS:
            rendered = connect.render(ROOT, agent)
            if agent == 'hermes':
                self.assertIn('mcp_servers:', rendered)
                continue
            value = connect.toml(rendered) if agent == 'codex' else json.loads(rendered)
            key = 'mcp_servers' if agent == 'codex' else 'mcp' if agent == 'opencode' else 'servers' if agent == 'vscode' else 'mcpServers'
            entry = value[key]['agentlocker']
            command = entry['command'][0] if agent == 'opencode' else entry['command']
            self.assertEqual(command, connect.interpreter())

    def test_connect_route_honours_scan_platform(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / 'home'
            home.mkdir()
            paths = {platform: tuple(platform + '/' + part for part in connect.PATHS[platform]) for platform in connect.PATHS}
            for platform in paths:
                with patch.dict(connect.PATHS, paths), patch('hub_ui.gateway_connection', return_value={'status': 'stopped', 'snippets': {}}):
                    server = hub_ui.SetupServer(root, scan_home=home, scan_platform=platform)
                    self.addCleanup(server.server_close)
                    result = server.api('/api/connect', {})
                expected = {str(home / part) for part in paths[platform]}
                self.assertEqual({agent['path'] for agent in result['agents']}, expected)


if __name__ == '__main__':
    unittest.main()
