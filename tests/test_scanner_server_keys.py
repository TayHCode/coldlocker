import json
from pathlib import Path
import tempfile
import unittest

import scanner

ROOT = Path(__file__).resolve().parents[1]
SERVER = {'command': 'uvx', 'args': ['tool==1.0.0']}


class ServerKeyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / 'home'
        self.project = Path(temporary.name) / 'proj'
        (self.home / '.codex').mkdir(parents=True)
        self.project.mkdir()
        (self.home / '.codex/config.toml').write_text(
            '[mcp_servers.node_repl]\ncommand = "node_repl"\n\n'
            '[mcp_servers.Docs_Server]\nurl = "https://docs.example.test/mcp"\n')
        (self.home / '.claude.json').write_text(json.dumps({
            'mcpServers': {'my_server': SERVER},
            'projects': {str(self.project): {'mcpServers': {'proj_srv': SERVER}}}}))
        (self.project / '.mcp.json').write_text(json.dumps({'mcpServers': {'project_json': SERVER}}))

    def test_underscore_and_uppercase_keys_import_under_normalised_names(self):
        report = scanner.Inventory(self.home, (self.project,), 'linux', {}, lambda command: '', ROOT).discover()
        servers = {i['name']: i for i in report['items'] if i['kind'] == 'mcp_server'}
        for name in ('node-repl', 'docs-server', 'my-server', 'proj-srv', 'project-json'):
            with self.subTest(name=name):
                self.assertIn(name, servers)
                self.assertNotEqual(servers[name]['status'], 'skipped', servers[name].get('reason'))
        self.assertIn('docs-server', {i['name'] for i in report['items'] if i['kind'] == 'connection'})


if __name__ == '__main__':
    unittest.main()
