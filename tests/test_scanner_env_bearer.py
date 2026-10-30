import json
from pathlib import Path
import shutil
import tempfile
import unittest

import hub_ui
import scanner
from store import FileStore
from ui_auth import Authentication
from test_scanner import ROOT

CANARY = 'canary-env-token-value-0003'
CONFIG = (
    '[mcp_servers.bearer-http]\nurl = "https://api.example.test/mcp/"\nbearer_token_env_var = "EXAMPLE_TOKEN"\n\n'
    '[mcp_servers.header-http]\nurl = "https://headers.example.test/mcp"\n'
    'http_headers = { Authorization = "Bearer ${EXAMPLE_TOKEN}" }\n\n'
    '[mcp_servers.keyed-http]\nurl = "https://keyed.example.test/mcp"\n'
    'env_http_headers = { "X-Api-Key" = "EXAMPLE_KEY" }\n\n'
    '[mcp_servers.stdio-env]\ncommand = "tool"\nenv_vars = ["EXAMPLE_TOKEN"]\n')
NAMES = ('bearer-http', 'header-http', 'keyed-http')


class EnvBearerTests(unittest.TestCase):
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
        self.env = {'EXAMPLE_TOKEN': CANARY, 'EXAMPLE_KEY': CANARY}
        self.report = scanner.Inventory(self.home, (), 'linux', self.env, lambda command: '', self.root).discover()
        self.store = FileStore(self.root)

    def items(self, kind):
        return {i['name']: i for i in self.report['items'] if i['kind'] == kind}

    def test_env_indirected_http_servers_are_importable_with_a_connection(self):
        servers, connections = self.items('mcp_server'), self.items('connection')
        for name in NAMES:
            with self.subTest(name=name):
                self.assertEqual(servers[name]['status'], 'needs_review', servers[name].get('reason'))
                self.assertEqual([f['check'] for f in servers[name]['review']], ['unknown-remote'])
                self.assertEqual(connections[name]['status'], 'needs_review')
        self.assertEqual(servers['stdio-env']['status'], 'skipped')
        self.assertNotIn(CANARY, json.dumps(self.report))

    def test_import_stores_a_reference_never_the_value_and_sign_in_lists_it(self):
        servers = [i['id'] for i in self.report['items'] if i['kind'] == 'mcp_server' and i['status'] == 'needs_review']
        ids = [i['id'] for i in self.report['items'] if i['importable']] + servers
        result = scanner.apply(self.root, self.report, ids, self.store, self.home, platform='linux', env=self.env,
                               runner=lambda command: '', approved=servers)
        self.assertEqual(result['rejected'], [])
        rows = {row['name']: row for row in hub_ui.auth_status(self.root, self.store)}
        for name in NAMES:
            with self.subTest(name=name):
                self.assertEqual(rows[name]['status'], 'needs-auth')
        definition = json.loads((self.root / '.hub-store/catalog/mcp/servers/bearer-http.json').read_text(encoding='utf-8'))
        self.assertTrue(definition['headers']['Authorization'].startswith('secret://'))
        self.assertEqual(self.store.names(), [])
        stored = json.loads((self.root / '.hub-store/catalog/mcp/servers/keyed-http.json').read_text(encoding='utf-8'))
        self.assertEqual(list(stored['headers']), ['X-Api-Key'])
        for path in self.root.rglob('*'):
            if path.is_file():
                self.assertNotIn(CANARY, path.read_text(encoding='utf-8', errors='ignore'))
        status = Authentication(self.root, self.store).bearer('bearer-http', 'Authorization', 'typed-fixture-value', 'bearer')
        self.assertEqual(status, {'status': 'connected'})
        connection = json.loads((self.root / '.hub-store/catalog/connections/bearer-http.json').read_text(encoding='utf-8'))
        self.assertEqual(connection['auth_status'], 'needs-auth')


if __name__ == '__main__':
    unittest.main()
