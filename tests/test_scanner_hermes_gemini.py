import json
import shutil
from pathlib import Path
import tempfile
import unittest

import scanner

ROOT = Path(__file__).resolve().parents[1]


class ScannerHermesGeminiTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.project = self.base / 'project'
        self.project.mkdir()
        self.root = self.base / 'locker'
        self.root.mkdir()
        (self.root / 'manifest.json').write_text(json.dumps({
            'schema_version': 1, 'name': 'agentlocker', 'license': 'MIT',
            'skills': [], 'rules': [], 'mcp_servers': [], 'routines': [], 'plugins': [], 'connections': []
        }))

    def scan(self, platform='linux', env=None):
        return scanner.Inventory(self.home, (self.project,), platform, env or {}, lambda command: '', self.root,
                                 live=False).discover()

    def test_discovers_hermes_gemini_and_redacts_mcp_secrets(self):
        (self.home / '.hermes/skills/research').mkdir(parents=True)
        (self.home / '.hermes/skills/research/SKILL.md').write_text('Research workflow')
        (self.home / '.hermes/SOUL.md').write_text('Use a concise voice')
        (self.home / '.hermes/config.yaml').write_text('''mcp_servers:
  search:
    command: npx
    args:
      - -y
      - search-mcp
    env:
      API_TOKEN: hermes-private-token-123
''')
        (self.home / '.hermes/cron').mkdir(parents=True)
        (self.home / '.hermes/cron/jobs.json').write_text(json.dumps([
            {'name': 'Daily research', 'schedule': '0 9 * * *', 'prompt': 'Review current research'}
        ]))
        gemini = self.home / '.gemini'
        (gemini / 'skills/summary').mkdir(parents=True)
        (gemini / 'skills/summary/SKILL.md').write_text('Summarize documents')
        (gemini / 'GEMINI.md').write_text('Follow these instructions')
        (gemini / 'settings.json').write_text(json.dumps({'mcpServers': {
            'calendar': {'command': 'node', 'args': ['server.js'], 'env': {'apiToken': 'gemini-private-token-456'}}
        }}))
        (gemini / 'commands').mkdir()
        (gemini / 'commands/review.toml').write_text('prompt = "Review this change"')
        extension = gemini / 'extensions/research'
        (extension / 'skills/literature').mkdir(parents=True)
        (extension / 'skills/literature/SKILL.md').write_text('Review sources')
        (extension / 'GEMINI.md').write_text('Extension instructions')
        (extension / 'gemini-extension.json').write_text(json.dumps({'name': 'research', 'mcpServers': {
            'research': {'command': 'python', 'args': ['server.py'], 'env': {'TOKEN': 'extension-private-token-789'}}
        }}))
        (extension / 'commands').mkdir()
        (extension / 'commands/find.toml').write_text('prompt = "Find sources"')
        inventory = scanner.Inventory(self.home, (self.project,), 'linux', {}, lambda command: '', self.root, live=False)
        report = inventory.discover()
        agents = {item['agent'] for item in report['items']}
        self.assertIn('hermes', agents)
        self.assertIn('gemini', agents)
        self.assertEqual([], [item for item in report['not_scanned']['agents'] if item['name'] in ('hermes', 'gemini')])
        self.assertTrue(any(item['agent'] == 'hermes' and item['kind'] == 'routine' and
                            item['schedule'] == {'type': 'cron', 'expression': '0 9 * * *'}
                            for item in report['items']))
        servers = [item for item in report['items'] if item['kind'] == 'mcp_server']
        self.assertEqual(3, len(servers))
        serialized = json.dumps(report)
        for secret in ('hermes-private-token-123', 'gemini-private-token-456', 'extension-private-token-789'):
            self.assertNotIn(secret, serialized)
        for server in servers:
            definition, secrets = inventory.payloads[server['id']]
            self.assertTrue(secrets)
            self.assertIn('secret://', json.dumps(definition))

    def test_missing_dirs_and_malformed_configs_are_reported_without_crashing(self):
        empty = self.scan()
        self.assertFalse(any(item['agent'] in ('hermes', 'gemini') for item in empty['items']))
        (self.home / '.hermes').mkdir()
        (self.home / '.hermes/config.yaml').write_text('mcp_servers:\n  search: [unterminated')
        (self.home / '.hermes/cron').mkdir()
        (self.home / '.hermes/cron/jobs.json').write_text('{broken')
        (self.home / '.gemini').mkdir()
        (self.home / '.gemini/settings.json').write_text('{broken')
        report = self.scan()
        failed = [item for item in report['items'] if item['agent'] in ('hermes', 'gemini')]
        self.assertTrue(failed)
        self.assertTrue(all(item['status'] == 'skipped' for item in failed))

    def test_windows_paths_use_userprofile_and_hermes_home_override(self):
        appdata = self.base / 'AppData' / 'Roaming'
        (appdata / 'gemini-cli').mkdir(parents=True)
        gemini_home = self.home / '.gemini'
        gemini_home.mkdir()
        (gemini_home / 'settings.json').write_text(json.dumps({'mcpServers': {
            'win-server': {'command': 'node', 'args': ['server.js']}
        }}))
        hermes_home = self.home / 'HermesHome'
        (hermes_home / 'skills/windows').mkdir(parents=True)
        (hermes_home / 'skills/windows/SKILL.md').write_text('Windows skill')
        report = self.scan('win32', {'APPDATA': str(appdata), 'USERPROFILE': str(self.home),
                                     'HERMES_HOME': str(hermes_home)})
        self.assertTrue(any(item['agent'] == 'gemini' and item['kind'] == 'mcp_server' for item in report['items']))
        self.assertTrue(any(item['agent'] == 'hermes' and item['kind'] == 'skill' for item in report['items']))


    def test_native_hermes_schedules(self):
        root = self.home / '.hermes/cron'
        root.mkdir(parents=True)
        (root / 'jobs.json').write_text(json.dumps({'jobs': [
            {'id': 'cron', 'schedule': {'kind': 'cron', 'expr': '0 9 * * *'}, 'prompt': 'Review'},
            {'id': 'interval', 'schedule': {'kind': 'interval', 'minutes': 30}, 'prompt': 'Summarize'},
            {'id': 'once', 'schedule': {'kind': 'once', 'run_at': '2030-01-01T00:00:00Z'}, 'prompt': 'Once'},
        ]}))
        items = [i for i in self.scan()['items'] if i['agent'] == 'hermes']
        self.assertEqual({'type': 'cron', 'expression': '0 9 * * *'}, next(i for i in items if i['name'] == 'cron')['schedule'])
        self.assertEqual({'type': 'interval', 'every_seconds': 1800}, next(i for i in items if i['name'] == 'interval')['schedule'])
        self.assertEqual('skipped', next(i for i in items if i['name'] == 'once')['status'])

    def test_yaml_unrelated_block_and_documented_flow_args(self):
        root = self.home / '.hermes'
        root.mkdir()
        (root / 'config.yaml').write_text('''personality: |
  Be helpful.
mcp_servers:
  search:
    command: npx # executable
    args: [-y, search-mcp]
    env:
      API_TOKEN: "fixture-hermes-token"
model:
  provider: auto
''')
        report = self.scan()
        servers = [i for i in report['items'] if i['agent'] == 'hermes' and i['kind'] == 'mcp_server']
        self.assertEqual(1, len(servers))
        self.assertNotEqual('skipped', servers[0]['status'])

    def test_extension_custom_context(self):
        extension = self.home / '.gemini/extensions/research'
        extension.mkdir(parents=True)
        (extension / 'gemini-extension.json').write_text(json.dumps({'name': 'research', 'contextFileName': 'CONTEXT.md'}))
        (extension / 'CONTEXT.md').write_text('Review sources carefully')
        items = self.scan()['items']
        self.assertTrue(any(i['agent'] == 'gemini' and i['source'].endswith('/CONTEXT.md') for i in items))

    def test_http_headers_and_env_are_only_references_and_scan_is_read_only(self):
        for agent in ('hermes', 'gemini'):
            (self.home / ('.' + agent)).mkdir()
        (self.home / '.hermes/config.yaml').write_text('''mcp_servers:
  remote:
    url: https://example.com/mcp
    headers:
      Authorization: fixture-header-hermes
''')
        (self.home / '.gemini/settings.json').write_text(json.dumps({'mcpServers': {
            'remote': {'httpUrl': 'https://example.com/mcp', 'headers': {'Authorization': 'fixture-header-gemini'}},
            'local': {'command': 'node', 'env': {'NORMAL_VALUE': 'fixture-env-gemini', 'EMPTY': ''}},
        }}))
        def snapshot():
            return {p.relative_to(self.base).as_posix(): p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
        before = snapshot()
        inv = scanner.Inventory(self.home, (self.project,), 'linux', {}, lambda command: '', self.root, live=False)
        report = inv.discover()
        self.assertEqual(before, snapshot())
        self.assertFalse((self.root / '.hub-store').exists())
        servers = [i for i in report['items'] if i['agent'] in ('hermes', 'gemini') and i['kind'] == 'mcp_server']
        self.assertEqual(3, len(servers))
        for item in servers:
            self.assertNotEqual('skipped', item['status'])
            definition, secrets = inv.payloads[item['id']]
            for field in ('headers', 'env'):
                for value in definition.get(field, {}).values():
                    self.assertTrue(value == '' or value.startswith('secret://'))
            for secret in secrets.values():
                self.assertNotIn(secret, json.dumps(report))
                self.assertNotIn(secret, json.dumps(definition))

    def test_selected_project_sources_on_all_platforms(self):
        for relative in ('.gemini/skills/summary/SKILL.md', '.hermes/skills/research/SKILL.md',
                         'GEMINI.md', '.hermes.md', 'HERMES.md', '.gemini/commands/review.toml'):
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('Review carefully')
        for platform in ('linux', 'darwin', 'win32'):
            with self.subTest(platform=platform):
                items = [i for i in self.scan(platform)['items'] if i['agent'] in ('hermes', 'gemini')]
                self.assertEqual(6, len(items))
        inv = scanner.Inventory(self.home, (), 'linux', {}, lambda command: '', self.root, live=False)
        self.assertFalse(any(i['agent'] in ('hermes', 'gemini') for i in inv.discover()['items']))

    def test_malformed_inputs_are_individually_skipped(self):
        files = {
            '.gemini/settings.json': ['{broken', '[]', '{"mcpServers": []}'],
            '.hermes/config.yaml': [
                'mcp_servers:\n  search: [unterminated',
                'mcp_servers: []',
                'mcp_servers:\n  search:\n    command: node\n    command: python',
                'mcp_servers:\n  search:\n    command: node\n    args: [one,,two]',
                'mcp_servers:\n  search:\n    command: node\n    env:\n      API_TOKEN: !unsafe value',
            ],
            '.hermes/cron/jobs.json': ['{broken', '42', '{"jobs": {}}'],
        }
        for relative, values in files.items():
            path = self.home / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            for value in values:
                with self.subTest(path=relative, value=value):
                    path.write_text(value)
                    items = [i for i in self.scan()['items'] if i['agent'] in ('hermes', 'gemini')]
                    self.assertEqual(1, len(items))
                    self.assertEqual('skipped', items[0]['status'])
            path.unlink()

    def test_yaml_quoted_commas_and_hashes_remain_literal(self):
        from checks.scanners.hermes import yaml_mapping
        value = yaml_mapping('''mcp_servers:\n  search:\n    args: ["one,two", 'three,four', -y]\n    env:\n      TOKEN: "value # suffix" # comment''')
        self.assertEqual(['one,two', 'three,four', '-y'], value['mcp_servers']['search']['args'])
        self.assertEqual('value # suffix', value['mcp_servers']['search']['env']['TOKEN'])

    def test_extension_context_cannot_escape_its_directory(self):
        extension = self.home / '.gemini/extensions/research'
        extension.mkdir(parents=True)
        (extension.parent / 'outside.md').write_text('Outside context')
        (extension / 'gemini-extension.json').write_text(json.dumps({'contextFileName': '../outside.md'}))
        items = [i for i in self.scan()['items'] if i['agent'] == 'gemini']
        self.assertEqual(1, len(items))
        self.assertEqual('skipped', items[0]['status'])

    def test_extension_without_optional_context_has_no_failed_rule(self):
        extension = self.home / '.gemini/extensions/research'
        extension.mkdir(parents=True)
        (extension / 'gemini-extension.json').write_text(json.dumps({'name': 'research'}))
        self.assertFalse(any(i['agent'] == 'gemini' for i in self.scan()['items']))
