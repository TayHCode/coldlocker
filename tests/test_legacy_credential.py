import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from checks import validators
import gateway_adapter
import gateway_render
from hub import read_json, record, write_json
import hub_ui
import mcp_endpoint
import portable
import scanner
from store import FileStore
import verify

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'sk_' + 'live_' + 'A1b2C3d4E5f6G7h8I9j0K'
REASON = 'replaces the stored copy that had a credential in its args'


class LegacyCredentialTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.root = self.base / 'locker'
        self.root.mkdir()
        self.local = self.root / '.hub-store/catalog'
        for name in ('manifest.json', 'LICENSE', '.gitignore'):
            shutil.copyfile(ROOT / name, self.root / name)
        for folder in ('skills', 'rules', 'routines', 'mcp', 'config'):
            shutil.copytree(ROOT / folder, self.root / folder)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.store = FileStore(self.root)
        servers = {'argsrv': {'command': 'npx', 'args': ['-y', 'some-mcp@1.2.3', '--api-key', TOKEN]},
                   'plainsrv': {'command': 'uvx', 'args': ['other-mcp==1.0.0']}}
        (self.home / '.claude.json').write_text(json.dumps({'mcpServers': servers}), encoding='utf-8')
        manifest = validators.empty_manifest()
        for name, source in servers.items():
            destination = self.local / 'mcp/servers' / (name + '.json')
            destination.parent.mkdir(parents=True, exist_ok=True)
            write_json(destination, {'name': name, 'description': name, 'transport': 'stdio', **source})
            with contextlib.redirect_stdout(io.StringIO()):
                record(manifest, 'mcp_servers', name, destination, 'agentlocker', 'scan-claude', self.local)

    def scan(self):
        return scanner.Inventory(self.home, (), 'linux', {}, lambda command: '', self.root).discover()

    def listed(self):
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'hub_list_mcp_servers', 'arguments': {}}}
        return mcp_endpoint.dispatch(request, self.root)['result']['content'][0]['text']

    def test_sign_in_list_marks_only_the_legacy_server(self):
        rows = {row['name']: row for row in hub_ui.auth_status(self.root, self.store)}
        self.assertLessEqual({'argsrv', 'plainsrv'}, set(rows))
        self.assertEqual(rows['plainsrv']['state'], 'no_credentials')
        self.assertIs(rows['argsrv'].get('legacy_credential'), True)
        self.assertNotIn('legacy_credential', rows['plainsrv'])
        self.assertIn('credential in its args', rows['argsrv']['error'])
        self.assertIn(f'{portable.python_command()} hub.py scan --import', rows['argsrv']['next_step'])
        self.assertNotIn(TOKEN, json.dumps(rows))

    def test_verify_and_auth_definition_name_the_reason(self):
        result = verify.check(self.root, 'argsrv')
        self.assertEqual((result['ok'], result['status']), (False, 'check_failed'))
        self.assertIn('credential in its args', result['error'])
        self.assertIn(f'{portable.python_command()} hub.py scan --import', result['next_step'])
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_prepare_and_listing_never_emit_the_token(self):
        rendered = gateway_render.render(self.root)
        self.assertIn('plainsrv', rendered['mcpServers'])
        self.assertNotIn('argsrv', rendered['mcpServers'])
        runtime = gateway_adapter.prepare(self.root, self.root)
        self.assertNotIn(TOKEN, (runtime / 'mcp_settings.json').read_text(encoding='utf-8'))
        listed = self.listed()
        self.assertNotIn(TOKEN, listed)
        entries = {e['name']: e for e in json.loads(listed)}
        self.assertIn('server', entries['plainsrv'])
        self.assertNotIn('server', entries['argsrv'])
        self.assertIn('credential in its args', entries['argsrv']['reason'])
        self.assertIn(f'{portable.python_command()} hub.py scan --import', entries['argsrv']['next_step'])

    def test_rescan_offers_a_replacement_and_import_overwrites(self):
        item = next(i for i in self.scan()['items'] if i['kind'] == 'mcp_server' and i['name'] == 'argsrv')
        self.assertEqual((item['status'], item['importable'], item['reason']), ('new', True, REASON))
        report = self.scan()
        result = scanner.apply(self.root, report, [item['id']], self.store, self.home, platform='linux', env={}, runner=lambda command: '')
        self.assertEqual(result['counts'], {'imported': 1, 'rejected': 0})
        catalog = ''.join(p.read_text(encoding='utf-8') for p in self.local.rglob('*.json'))
        self.assertNotIn(TOKEN, catalog)
        definition = read_json(self.local / 'mcp/servers/argsrv.json')
        reference = definition['args'][-1]
        self.assertTrue(reference.startswith('secret://'))
        self.assertEqual(self.store.resolve(reference), TOKEN)
        validators.mcp(definition)
        self.assertLessEqual({'argsrv', 'plainsrv'}, {r['name'] for r in hub_ui.auth_status(self.root, self.store)})
        again = next(i for i in self.scan()['items'] if i['name'] == 'argsrv')
        self.assertEqual(again['status'], 'already_in_locker')


if __name__ == '__main__':
    unittest.main()
