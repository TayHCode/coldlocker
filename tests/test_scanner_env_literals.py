import json
from pathlib import Path
import shutil
import tempfile
import unittest

import scanner
from store import FileStore
from test_scanner import ROOT

CONFIG = (
    '[mcp_servers.node_repl]\ncommand = "node"\nargs = ["repl.js"]\n'
    '[mcp_servers.node_repl.env]\nUSE_CASE_BROWSER = ""\n\n'
    '[mcp_servers.plain_repl]\ncommand = "node"\nargs = ["repl.js"]\n')


class EmptyEnvValueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.home = base / 'home'
        self.root = base / 'locker'
        self.root.mkdir()
        for name in ('manifest.json', 'LICENSE', '.gitignore'):
            shutil.copyfile(ROOT / name, self.root / name)
        for folder in ('skills', 'rules', 'routines', 'mcp'):
            shutil.copytree(ROOT / folder, self.root / folder)
        (self.home / '.codex').mkdir(parents=True)
        (self.home / '.codex/config.toml').write_text(CONFIG, encoding='utf-8')
        self.report = scanner.Inventory(self.home, (), 'linux', {}, lambda command: '', self.root).discover()
        self.store = FileStore(self.root)

    def test_server_with_an_empty_env_value_scans_as_new(self):
        servers = {i['name']: i for i in self.report['items'] if i['kind'] == 'mcp_server'}
        for name in ('node-repl', 'plain-repl'):
            with self.subTest(name=name):
                self.assertEqual(servers[name]['status'], 'new', servers[name].get('reason'))
                self.assertTrue(servers[name]['importable'])

    def test_import_keeps_the_empty_value_as_a_literal(self):
        ids = [i['id'] for i in self.report['items'] if i['importable']]
        result = scanner.apply(self.root, self.report, ids, self.store, self.home, platform='linux', env={},
                               runner=lambda command: '')
        self.assertEqual(result['rejected'], [])
        path = self.root / '.hub-store/catalog/mcp/servers/node-repl.json'
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['env'], {'USE_CASE_BROWSER': ''})
        self.assertEqual(self.store.names(), [])


if __name__ == '__main__':
    unittest.main()
