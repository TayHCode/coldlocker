import json
from pathlib import Path
import shutil
import tempfile
import unittest

import scanner
from test_scanner import ROOT

SERVER = {'command': 'uvx', 'args': ['tool==1.0.0']}


class DisabledServerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.home = base / 'home'
        self.project = base / 'proj'
        self.root = base / 'locker'
        self.root.mkdir()
        for name in ('manifest.json', 'LICENSE', '.gitignore'):
            shutil.copyfile(ROOT / name, self.root / name)
        for folder in ('skills', 'rules', 'routines', 'mcp'):
            shutil.copytree(ROOT / folder, self.root / folder)
        self.write(self.home / '.codex/config.toml', '[mcp_servers.switched-off]\ncommand = "uvx"\nargs = ["tool==1.0.0"]\nenabled = false\n\n'
                   '[mcp_servers.plain-ok]\ncommand = "uvx"\nargs = ["tool==1.0.0"]\nenabled = true\n')
        self.write(self.home / '.cursor/mcp.json', json.dumps({'mcpServers': {'cursor-off': dict(SERVER, enabled=False), 'cursor-on': SERVER}}))
        self.write(self.home / '.claude.json', json.dumps({
            'mcpServers': {'plain-claude': SERVER, 'claude-off': dict(SERVER, enabled=False)},
            'projects': {str(self.project): {'mcpServers': {'proj-srv': SERVER, 'off-in-claude': SERVER}, 'disabledMcpServers': ['off-in-claude']}}}))
        self.write(self.project / '.mcp.json', json.dumps({'mcpServers': {'json-on': SERVER, 'json-off': SERVER}}))
        self.write(self.project / '.claude/settings.json', json.dumps({'disabledMcpjsonServers': ['json-off']}))
        self.report = scanner.Inventory(self.home, (self.project,), 'linux', {}, lambda command: '', self.root).discover()

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def rows(self, name):
        return [i for i in self.report['items'] if i['kind'] == 'mcp_server' and i['name'] == name]

    def test_disabled_servers_are_skipped(self):
        for name in ('switched-off', 'cursor-off', 'claude-off', 'off-in-claude', 'json-off'):
            rows = self.rows(name)
            self.assertEqual([r['status'] for r in rows], ['skipped'], name)
            self.assertEqual(rows[0]['reason'], 'disabled server', name)

    def test_enabled_servers_stay_importable(self):
        for name in ('plain-ok', 'cursor-on', 'plain-claude', 'proj-srv', 'json-on'):
            self.assertEqual([r['status'] for r in self.rows(name)], ['new'], name)


if __name__ == '__main__':
    unittest.main()
