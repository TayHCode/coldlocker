import contextlib
import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from checks import checker, validators
import gateway_render
import mcp_endpoint
import portable
import scanner
from store import FileStore
from windows_text import windows_text_writes

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/scan'
CANARY = 'canary-secret-value-0001'


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.home = self.base / 'home'
        shutil.copytree(FIXTURES / 'linux', self.home)
        self.root = self.base / 'locker'
        self.root.mkdir()
        self.local = self.root / '.hub-store/catalog'
        for name in ('manifest.json', 'LICENSE', '.gitignore'):
            shutil.copyfile(ROOT / name, self.root / name)
        for folder in ('skills', 'rules', 'routines', 'mcp', 'config'):
            shutil.copytree(ROOT / folder, self.root / folder)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.runner = lambda command: ''
        self.store = FileStore(self.root)

    def scan(self, platform='linux', **kwargs):
        return scanner.Inventory(self.home, (), platform, {}, self.runner, self.root, **kwargs).discover()

    def apply(self, report, selected=None):
        selected = selected if selected is not None else [i['id'] for i in report['items'] if i['importable']]
        return scanner.apply(self.root, report, selected, self.store, self.home, platform='linux', env={}, runner=self.runner)

    def test_per_os_fixtures_are_write_free_and_secret_free(self):
        for folder, platform in [('linux', 'linux'), ('macos', 'darwin'), ('windows', 'win32')]:
            with self.subTest(platform=platform):
                home = self.base / folder
                shutil.copytree(FIXTURES / folder, home)
                before, hub_before = snapshot(home), snapshot(self.root)
                with patch.object(scanner, 'ROOT', self.root):
                    report = scanner.scan(home, platform=platform, env={}, runner=lambda command: '')
                self.assertEqual(before, snapshot(home))
                self.assertEqual(hub_before, snapshot(self.root))
                self.assertEqual(report['platform'], platform)
                for kind in scanner.KINDS.values():
                    self.assertGreater(report['totals'][kind], 0, kind)
                self.assertNotIn(CANARY, json.dumps(report))
                self.assertNotIn(str(home), json.dumps(report))
                items = report['items']
                self.assertTrue(any(i['agent'] == 'opencode' and i['kind'] == 'mcp_server' and i['status'] == 'new' for i in items))
                self.assertEqual(any(i['agent'] == 'system' and i['kind'] == 'routine' for i in items), platform != 'win32')

    def test_crlf_dedupe_includes_supporting_files(self):
        report = self.scan()
        items = [i for i in report['items'] if i['kind'] == 'skill' and i['name'] == 'sample']
        self.assertEqual([i['status'] for i in items], ['new', 'duplicate'])
        self.assertEqual(items[1]['duplicate_of'], items[0]['id'])
        self.assertEqual(items[0]['content_hash'], items[1]['content_hash'])
        expected = 'sc-' + scanner.hashlib.sha256('|'.join(str(items[0][k]) for k in ('agent', 'kind', 'source', 'content_hash')).encode()).hexdigest()[:12]
        self.assertEqual(items[0]['id'], expected)

    def test_vendor_and_plugin_cache_never_claimed(self):
        report = self.scan()
        vendor = [i for i in report['items'] if i['ownership'] != 'user']
        self.assertEqual({i['ownership'] for i in vendor}, {'vendor', 'plugin_cache'})
        self.assertTrue(all(i['status'] == 'vendor' and not i['importable'] for i in vendor))
        self.apply(report)
        self.assertFalse((self.local / 'skills/vendor').exists())
        self.assertFalse((self.local / 'skills/bundled').exists())
        self.assertFalse((self.root / '.claude').exists())

    def test_plugin_cache_item_names_its_plugin(self):
        items = {i['name']: i for i in self.scan()['items'] if i['ownership'] != 'user'}
        self.assertEqual(items['bundled']['plugin'], 'bundle')
        self.assertNotIn('plugin', items['vendor'])

    def test_conflict_and_already_in_locker(self):
        report = self.scan()
        self.apply(report)
        imported = self.scan()
        self.assertTrue(any(i['name'] == 'sample' and i['status'] == 'already_in_locker' for i in imported['items']))
        path = self.home / '.claude/skills/sample/support/data.txt'
        path.write_text('Changed content', encoding='utf-8')
        changed = self.scan()
        self.assertTrue(any(i['name'] == 'sample' and i['status'] == 'conflict' for i in changed['items']))
        self.assertEqual(self.apply(changed)['counts']['imported'], 0)

    def test_same_name_different_content_conflicts_both(self):
        (self.home / '.codex/skills/sample/support/data.txt').write_text('Different', encoding='utf-8')
        items = [i for i in self.scan()['items'] if i['kind'] == 'skill' and i['name'] == 'sample']
        self.assertEqual([i['status'] for i in items], ['conflict', 'conflict'])

    def test_connect_first_registers_http_without_backend_change(self):
        report = self.scan()
        items = [i for i in report['items'] if i['kind'] == 'mcp_server' and i['upstream']]
        self.assertEqual({i['upstream']['kind'] for i in items}, {'executor', 'mcphub', 'composio'})
        self.assertTrue(all(i['status'] == 'connect' for i in items))
        result = self.apply(report)
        self.assertEqual(result['rejected'], [])
        for item in items:
            value = json.loads((self.local / 'mcp/servers' / (item['name'] + '.json')).read_text(encoding='utf-8'))
            self.assertEqual(value['transport'], 'http')
            self.assertEqual(value['url'], item['upstream']['endpoint'])
        self.assertFalse((self.root / 'config.json').exists())

    def test_import_idempotent_check_and_canary_private_only(self):
        report = self.scan()
        before = snapshot(self.home)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = self.apply(report)
            subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
            code = checker.run(self.root)
        self.assertEqual(code, 0, stdout.getvalue())
        self.assertEqual(result['rejected'], [])
        self.assertGreater(result['counts']['imported'], 10)
        self.assertEqual(snapshot(self.home), before)
        after = snapshot(self.root)
        self.assertEqual(self.apply(report)['counts']['imported'], 0)
        self.assertEqual(snapshot(self.root), after)
        public = json.dumps(report) + stdout.getvalue() + stderr.getvalue() + json.dumps(self.store.names())
        for p in self.root.rglob('*'):
            if p.is_file() and ('.hub-store' not in p.parts or 'catalog' in p.parts) and '.git' not in p.parts:
                public += p.read_text(encoding='utf-8')
        self.assertNotIn(CANARY, public)
        self.assertTrue(any(self.store.resolve('secret://' + name) == CANARY for name in self.store.names()))
        manifest = json.loads((self.local / 'manifest.json').read_text(encoding='utf-8'))
        self.assertTrue(all(i['owner'] == 'agentlocker' for c in scanner.KINDS.values() for i in manifest[c]))
        for entry in manifest['routines']:
            routine = json.loads((self.local / entry['path']).read_text(encoding='utf-8'))
            self.assertIs(routine['enabled'], False)
            self.assertEqual(routine['owner'], 'agentlocker')
        for collection in ('plugins', 'connections'):
            inventory = mcp_endpoint.inventory(self.root, collection)
            self.assertTrue(inventory)
            self.assertTrue(all('metadata' in i for i in inventory))

    def test_apply_rescans_ignores_forged_payload_and_changed_sources(self):
        report = self.scan()
        item = next(i for i in report['items'] if i['kind'] == 'skill' and i['status'] == 'new')
        item['source'] = '/not-a-trusted-path'
        (self.home / '.claude/skills/sample/support/data.txt').write_text('Changed after preview', encoding='utf-8')
        self.assertEqual(self.apply(report, [item['id']])['counts']['imported'], 0)

    def relocate_below_symlinked_ancestor(self):
        real = self.base / 'real'
        real.mkdir()
        link = self.base / 'link'
        link.symlink_to(real, target_is_directory=True)
        patcher = patch.object(tempfile, 'tempdir', str(link))
        patcher.start()
        self.addCleanup(patcher.stop)
        shutil.move(str(self.root), str(real / 'locker'))
        self.root = link / 'locker'
        self.local = self.root / '.hub-store/catalog'
        self.store = FileStore(self.root)

    def test_import_below_symlinked_ancestor(self):
        self.relocate_below_symlinked_ancestor()
        result = self.apply(self.scan())
        self.assertEqual(result['rejected'], [])
        self.assertGreater(result['counts']['imported'], 10)

    def test_symlink_inside_store_rejected_with_reason(self):
        self.relocate_below_symlinked_ancestor()
        elsewhere = self.base / 'elsewhere'
        elsewhere.mkdir()
        report = self.scan()
        item = next(i for i in report['items'] if i['kind'] == 'rule' and i['status'] == 'new')
        self.local.mkdir(parents=True)
        (self.local / 'rules').symlink_to(elsewhere, target_is_directory=True)
        result = self.apply(report, [item['id']])
        self.assertEqual(result['rejected'], [item['id']])
        self.assertIn('symlink', result['reasons'][item['id']])
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_only_selection_and_unindexed_destination_preserved(self):
        report = self.scan()
        item = next(i for i in report['items'] if i['kind'] == 'rule' and i['status'] == 'new')
        target = self.local / 'rules' / (item['name'] + '.md')
        target.parent.mkdir(parents=True)
        target.write_text('Unfamiliar content', encoding='utf-8')
        result = self.apply(report, [item['id']])
        self.assertEqual(result['rejected'], [item['id']])
        self.assertEqual(target.read_text(encoding='utf-8'), 'Unfamiliar content')
        target.unlink()
        result = self.apply(report, [item['id']])
        self.assertEqual(result['imported'], [item['id']])

    def test_malformed_neighbors_indirection_symlinks_limits(self):
        (self.home / '.cursor/mcp.json').write_text('{broken', encoding='utf-8')
        (self.home / '.claude/skills/escape').symlink_to(self.home / '.claude/skills/sample', target_is_directory=True)
        large = self.home / '.claude/skills/oversize'
        large.mkdir()
        (large / 'SKILL.md').write_bytes(b'x' * (scanner.LIMIT + 1))
        report = self.scan()
        self.assertTrue(any(i['name'] == 'oversize' and i['status'] == 'skipped' for i in report['items']))
        self.assertFalse(any(i['name'] == 'escape' for i in report['items']))
        statuses = {i['name']: i['status'] for i in report['items'] if i['kind'] == 'mcp_server'}
        self.assertEqual(statuses['broken'], 'skipped')
        self.assertEqual(statuses['indirect'], 'skipped')
        self.assertEqual(statuses['tool'], 'new')

    def test_managed_agents_keeps_sync_ownership(self):
        path = self.home / '.agents/agents.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text('{"schemaVersion":4}', encoding='utf-8')
        items = self.scan()['items']
        managed = [i for i in items if 'managed_by agents' in i['flags']]
        self.assertTrue(managed)
        self.assertTrue(all(not i['importable'] for i in managed))

    def test_t3_real_schema_readonly_and_disabled(self):
        path = self.home / '.t3/userdata/statev2.sqlite'
        path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(path)) as c:
            c.executescript((self.home / '.t3/userdata/schema.sql').read_text(encoding='utf-8'))
            c.execute('INSERT INTO scheduled_tasks VALUES (?,?,?,?,?,?,?)', ('one', 'fixture-schedule', 'Summarize inventory', 1, json.dumps({'type':'interval','everyMs':60000}), CANARY, CANARY))
            c.commit()
        before = snapshot(self.home)
        report = self.scan()
        self.assertEqual(before, snapshot(self.home))
        items = [i for i in report['items'] if i['agent'] == 't3']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['status'], 'new')
        self.assertNotIn(CANARY, json.dumps(report))
        self.apply(report)
        value = json.loads((self.local / 'routines/fixture-schedule/routine.json').read_text(encoding='utf-8'))
        self.assertFalse(value['enabled'])
        self.assertEqual(value['schedule']['every_seconds'], 60)

    def test_t3_webhook_delivery_options_are_not_imported(self):
        path = self.home / '.t3/userdata/statev2.sqlite'
        path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(path)) as connection:
            connection.executescript((path.parent / 'schema.sql').read_text(encoding='utf-8'))
            for name, age in [('webhook-expiring', 720), ('webhook-unlimited', None)]:
                schedule = {'type': 'webhook', 'signature': None, 'maxDeliveryAgeMinutes': age}
                connection.execute('INSERT INTO scheduled_tasks VALUES (?,?,?,?,?,?,?)',
                                   (name, name, 'Summarize inventory', 1, json.dumps(schedule), CANARY, CANARY))
            connection.commit()
        before = snapshot(self.home)
        report = self.scan()
        items = [i for i in report['items'] if i['agent'] == 't3']
        self.assertEqual(len(items), 2)
        self.assertTrue(all(i['status'] == 'new' for i in items), items)
        self.assertNotIn(CANARY, json.dumps(report))
        self.assertEqual(self.apply(report, [i['id'] for i in items])['counts']['imported'], 2)
        for item in items:
            value = json.loads((self.local / 'routines' / item['name'] / 'routine.json').read_text(encoding='utf-8'))
            self.assertEqual(value['schedule'], {'type': 'webhook'})
            self.assertFalse(value['enabled'])
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(self.store.names(), [])

    def test_grok_webhook_trigger_options_and_signature_are_not_imported(self):
        path = self.home / '.grok/routines/webhook.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'name': 'grok-webhook', 'prompt': 'Summarize inventory',
                                   'trigger': {'type': 'webhook', 'signature': {'secret': CANARY},
                                               'maxDeliveryAgeMinutes': 720}}), encoding='utf-8')
        before = snapshot(self.home)
        report = self.scan()
        items = [i for i in report['items'] if i['source'].endswith('/webhook.json')]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['status'], 'new')
        self.assertNotIn(CANARY, json.dumps(report))
        self.assertEqual(self.apply(report, [items[0]['id']])['counts']['imported'], 1)
        value = json.loads((self.local / 'routines/grok-webhook/routine.json').read_text(encoding='utf-8'))
        self.assertEqual(value['schedule'], {'type': 'webhook'})
        self.assertFalse(value['enabled'])
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(self.store.names(), [])

    def test_project_configs_and_bounded_exclusions(self):
        project = self.base / 'project'
        shutil.copytree(self.home, project)
        (project / '.agents').mkdir(exist_ok=True)
        (project / 'AGENTS.md').write_text('Project policy', encoding='utf-8')
        inventory = scanner.Inventory(self.home, [project], 'linux', {}, self.runner, self.root).discover()
        self.assertTrue(any(i['source'].startswith('<project>/') for i in inventory['items']))
        for excluded in scanner.EXCLUDE:
            path = project / '.claude/skills' / excluded / 'SKILL.md'
            path.parent.mkdir(exist_ok=True)
            path.write_text('ignored', encoding='utf-8')
        self.assertFalse(any(i['name'] in scanner.EXCLUDE for i in scanner.Inventory(self.home, [project], 'linux', {}, self.runner, self.root).discover()['items']))

    def test_exported_grok_skill_keeps_supporting_files(self):
        report = self.scan()
        item = next(i for i in report['items'] if i['name'] == 'exported-skill')
        self.assertEqual(item['status'], 'new')
        self.assertEqual(self.apply(report, [item['id']])['imported'], [item['id']])
        self.assertEqual((self.local / 'skills/exported-skill/support.txt').read_text(encoding='utf-8'), 'Keep supporting files.\n')

    def box_export(self, entries=None):
        export = self.base / 'box-export'
        for name in ('alpha', 'beta'):
            package = export / 'workflows' / name
            package.mkdir(parents=True)
            (package / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: Box workflow {name}.\n---\nDo {name}.\n', encoding='utf-8')
        (export / 'workflows/alpha/notes.txt').write_text('Keep with alpha.\n', encoding='utf-8')
        entries = entries if entries is not None else [
            {'name': 'hub-t3-done', 'owner_bot': 'fixture-bot', 'trigger': {'type': 'webhook'}, 'prompt': 'Report the finished task.'}]
        (export / 'routines.json').write_text(json.dumps(entries), encoding='utf-8')
        return export

    def box_scan(self, export):
        home = self.base / 'empty-home'
        home.mkdir(exist_ok=True)
        inventory = scanner.Inventory(home, (), 'linux', {'GROK_EXPORT_DIR': str(export)}, self.runner, self.root)
        return inventory.discover()

    def test_grok_box_export_workflows_and_routines_are_found(self):
        report = self.box_scan(self.box_export())
        grok = {(i['kind'], i['name']): i for i in report['items'] if i['agent'] == 'grok'}
        self.assertEqual(set(grok), {('skill', 'alpha'), ('skill', 'beta'), ('routine', 'hub-t3-done')})
        self.assertEqual({i['status'] for i in grok.values()}, {'new'})

    def test_grok_routine_trigger_alias_imports_disabled_on_grok_bot_runner(self):
        report = self.box_scan(self.box_export())
        item = next(i for i in report['items'] if i['kind'] == 'routine')
        result = scanner.apply(self.root, report, [item['id']], self.store, self.base / 'empty-home', platform='linux',
                               env={'GROK_EXPORT_DIR': str(self.base / 'box-export')}, runner=self.runner)
        self.assertEqual(result['imported'], [item['id']])
        value = json.loads((self.local / 'routines/hub-t3-done/routine.json').read_text(encoding='utf-8'))
        self.assertEqual(value['schedule'], {'type': 'webhook'})
        self.assertEqual(value['runner'], 'grok-bot')
        self.assertEqual(value['owner'], 'fixture-bot')
        self.assertIs(value['enabled'], False)

    def test_grok_routine_failure_is_named_after_the_entry(self):
        home = self.base / 'empty-home'
        (home / '.grok/routines').mkdir(parents=True)
        (home / '.grok/routines/entries.json').write_text(json.dumps([
            {'name': 'good-one', 'owner_bot': 'fixture-bot', 'trigger': {'type': 'webhook'}, 'prompt': 'Fine.'},
            {'name': 'broken-one', 'owner_bot': 'fixture-bot', 'trigger': {'type': 'unknown'}, 'prompt': 'Bad trigger.'}]), encoding='utf-8')
        report = scanner.Inventory(home, (), 'linux', {}, self.runner, self.root).discover()
        routines = {i['name']: i for i in report['items'] if i['kind'] == 'routine'}
        self.assertEqual(set(routines), {'good-one', 'broken-one'})
        self.assertEqual(routines['good-one']['status'], 'new')
        self.assertEqual(routines['broken-one']['status'], 'skipped')
        self.assertIn('needs-manual', routines['broken-one']['flags'])

    def test_grok_routines_directory_under_export_base(self):
        export = self.box_export([])
        (export / 'routines').mkdir()
        (export / 'routines/nightly.json').write_text(json.dumps(
            {'name': 'nightly-check', 'trigger': {'type': 'interval', 'every_seconds': 3600}, 'prompt': 'Check.'}), encoding='utf-8')
        routine = next(i for i in self.box_scan(export)['items'] if i['kind'] == 'routine')
        self.assertEqual((routine['name'], routine['status']), ('nightly-check', 'new'))

    def test_grok_bot_runner_is_valid_and_still_disabled_only(self):
        value = dict(id='x', description='d', owner='o', runner='grok-bot', schedule={'type': 'webhook'}, timezone='UTC',
                     prompt='prompt.md', overlap='skip', timeout_seconds=60, enabled=False)
        self.assertEqual(validators.routine(dict(value)), value)
        with self.assertRaises(ValueError):
            validators.routine(dict(value, enabled=True))

    def test_auth_cache_marks_connections_without_copying_auth(self):
        report = self.scan()
        item = next(i for i in report['items'] if i['kind'] == 'connection' and i['name'] == 'open-remote')
        server = next(i for i in report['items'] if i['kind'] == 'mcp_server' and i['name'] == 'open-remote')
        self.assertEqual(self.apply(report, [item['id']])['imported'], [])
        scanner.apply(self.root, report, [server['id']], self.store, self.home, platform='linux', env={}, runner=self.runner, approved=[server['id']])
        data = json.loads((self.local / 'connections/open-remote.json').read_text(encoding='utf-8'))
        self.assertEqual(data['auth_status'], 'needs-auth')
        self.assertEqual(set(data), {'name', 'provider', 'endpoint', 'auth_status'})

    def test_executor_live_listing_uses_existing_connector_without_approval(self):
        from unittest.mock import MagicMock
        session = MagicMock()
        session.__enter__.return_value = session
        with patch('executor_connector.Session', return_value=session) as constructor, patch('executor_sources.invoke', return_value={'integrations': [{'slug': 'fixture-source'}]}) as invoke:
            inventory = scanner.Inventory(self.home, (), 'linux', {}, scanner.default_runner, self.root)
            inventory.runner = scanner.default_runner
            inventory.executor_sources(self.home / '.mcp.json', {'url': 'http://127.0.0.1:4788/mcp'}, {})
            self.assertEqual(invoke.call_args.args[1:], ('executor.coreTools.integrations.list', {}))
            self.assertNotIn('approve', invoke.call_args.kwargs)
            self.assertEqual(inventory.items[0]['kind'], 'connection')
            self.assertEqual(inventory.items[0]['upstream']['kind'], 'executor')
            inventory.executor_sources(self.home / '.mcp.json', {'url': 'http://127.0.0.1:4788/mcp'}, {})
            self.assertEqual(constructor.call_count, 1)

    def test_t3_wal_snapshot_does_not_touch_database_or_sidecars(self):
        path = self.home / '.t3/userdata/statev2.sqlite'
        with contextlib.closing(sqlite3.connect(path)) as connection:
            connection.executescript((self.home / '.t3/userdata/schema.sql').read_text(encoding='utf-8'))
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('INSERT INTO scheduled_tasks VALUES (?,?,?,?,?,?,?)', ('one', 'pending-task', 'Summary', 1, json.dumps({'type': 'webhook'}), CANARY, CANARY))
        connection.commit()
        before = snapshot(self.home)
        report = self.scan()
        self.assertEqual(before, snapshot(self.home))
        self.assertNotIn(CANARY, json.dumps(report))

    def test_failed_server_names_and_cross_file_content_cannot_echo_credentials(self):
        path = self.home / '.mcp.json'
        value = json.loads(path.read_text(encoding='utf-8'))
        value['mcpServers'][CANARY] = {'command': None, 'env': {'TOKEN': CANARY}}
        path.write_text(json.dumps(value), encoding='utf-8')
        rule = self.home / '.codex/AGENTS.override.md'
        rule.write_text('Do not copy ' + CANARY, encoding='utf-8')
        report = self.scan()
        self.assertNotIn(CANARY, json.dumps(report))
        self.assertTrue(any(i['name'] == 'redacted-item' and i['status'] == 'skipped' for i in report['items']))
        item = next(i for i in report['items'] if i['kind'] == 'rule' and i['name'] == 'codex-agents-override')
        self.assertEqual(item['status'], 'skipped')
        self.apply(report)
        self.assertFalse((self.local / 'rules/codex-agents-override.md').exists())

    def write_server_env(self, env, key='tool'):
        path = self.home / '.mcp.json'
        path.write_text(json.dumps({'mcpServers': {key: {'command': 'example', 'env': env}}}), encoding='utf-8')

    def write_skill(self, name, text):
        folder = self.home / '.claude/skills' / name
        folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: Example\n---\n{text}\n', encoding='utf-8')

    def test_plain_env_values_do_not_redact_unrelated_items(self):
        self.write_server_env({'DEBUG': '1', 'NODE_ENV': 'prod', 'FLAG': 'true', 'PORT': '1000', 'OFF': '0'})
        self.write_skill('plain', 'Run in prod with DEBUG 1 and port 1000, true or 0.')
        report = self.scan()
        self.assertFalse([i for i in report['items'] if i['name'] == 'redacted-item'])
        skill = next(i for i in report['items'] if i['kind'] == 'skill' and i['name'] == 'plain')
        self.assertEqual(skill['status'], 'new')
        self.assertTrue(skill['importable'])

    def test_secret_env_value_is_redacted_from_other_items_with_reason(self):
        token = 'tok-' + 'e' * 40
        path = self.home / '.mcp.json'
        path.write_text(json.dumps({'mcpServers': {'api': {'command': 'example', 'env': {'SERVICE_TOKEN': token}}, token: {'command': None}}}), encoding='utf-8')
        self.write_skill('leaky', 'Use ' + token + ' directly.')
        report = self.scan()
        self.assertNotIn(token, json.dumps(report))
        reason = 'contains a credential value from api env SERVICE_TOKEN'
        redacted = next(i for i in report['items'] if i['name'] == 'redacted-item')
        self.assertEqual((redacted['kind'], redacted['agent'], redacted['status'], redacted['importable']), ('mcp_server', 'claude', 'skipped', False))
        self.assertEqual([f['reason'] for f in redacted['review']], [reason])
        leaky = next(i for i in report['items'] if i['name'] == 'leaky')
        self.assertEqual((leaky['status'], leaky['importable']), ('skipped', False))
        self.assertEqual([f['reason'] for f in leaky['review']], [reason])

    def test_item_keeps_its_name_and_source_when_only_another_field_repeats_a_credential(self):
        token = 'tok-' + 'f' * 40
        path = self.home / '.mcp.json'
        path.write_text(json.dumps({'mcpServers': {
            'api': {'command': 'example', 'env': {'SERVICE_TOKEN': token}},
            'hubby': {'url': 'http://localhost:43871/' + token}}}))
        report = self.scan()
        self.assertNotIn(token, json.dumps(report))
        self.assertFalse([i for i in report['items'] if i['name'] == 'redacted-item'])
        hubby = next(i for i in report['items'] if i['kind'] == 'mcp_server' and i['name'] == 'hubby')
        self.assertEqual((hubby['status'], hubby['importable'], hubby['source']), ('skipped', False, '~/.mcp.json'))
        self.assertIsNone(hubby['upstream'])
        self.assertEqual([f['reason'] for f in hubby['review']], ['contains a credential value from api env SERVICE_TOKEN'])

    def test_secret_shaped_value_under_plain_key_is_a_credential(self):
        token = 'ghp_' + 'a' * 36
        self.write_server_env({'LABEL': token, 'MODE': 'plain'})
        inventory = scanner.Inventory(self.home, (), 'linux', {}, self.runner, self.root)
        inventory.discover()
        self.assertEqual(inventory.credential_values[token], 'tool env LABEL')
        self.assertNotIn('plain', inventory.credential_values)

    def endpoint_servers(self):
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'hub_list_mcp_servers', 'arguments': {}}}
        return mcp_endpoint.dispatch(request, self.root)['result']['content'][0]['text']

    def catalog_text(self):
        return ''.join(p.read_text(encoding='utf-8') for p in self.local.rglob('*.json'))

    def test_secret_in_args_is_moved_to_store(self):
        flagged, inline, shaped, auth = ('arg-tok-' + c * 24 for c in 'abcd')
        shaped = 'glpat' + '-' + 'A1b2C3d4E5f6G7h8I9j0'
        servers = {'flagged': ['some-mcp==1.2.3', '--api-key', flagged],
                   'inline': ['some-mcp==1.2.3', '--password=' + inline],
                   'shaped': ['some-mcp==1.2.3', shaped],
                   'auth': ['some-mcp==1.2.3', '--auth', auth]}
        values = {'flagged': flagged, 'inline': inline, 'shaped': shaped, 'auth': auth}
        (self.home / '.mcp.json').write_text(json.dumps({'mcpServers': {n: {'command': 'uvx', 'args': a} for n, a in servers.items()}}), encoding='utf-8')
        self.write_skill('leaky', 'Use ' + flagged + ' directly.')
        report = self.scan()
        for token in values.values():
            self.assertNotIn(token, json.dumps(report))
        items = {i['name']: i for i in report['items'] if i['kind'] == 'mcp_server' and i['name'] in servers}
        self.assertEqual({(i['status'], i['importable'], i['secrets']) for i in items.values()}, {('new', True, 1)})
        self.assertEqual(next(i for i in report['items'] if i['name'] == 'leaky')['status'], 'skipped')
        result = self.apply(report, [i['id'] for i in items.values()])
        self.assertEqual(result['counts'], {'imported': 4, 'rejected': 0})
        listed, catalog = self.endpoint_servers(), self.catalog_text()
        rendered = json.dumps(gateway_render.render(self.root))
        for name, token in values.items():
            for text in (listed, catalog, rendered):
                self.assertNotIn(token, text)
            definition = json.loads((self.local / 'mcp/servers' / (name + '.json')).read_text(encoding='utf-8'))
            reference = next(a for a in definition['args'] if 'secret://' in a)
            self.assertEqual(self.store.resolve(reference[reference.index('secret://'):]), token if name != 'inline' else inline)
        self.assertIn('--password=secret://', listed)
        self.assertIn('--password=${AGENTLOCKER_SECRET_', rendered)

    def test_placeholder_args_stay_literal(self):
        args = ['some-mcp==1.2.3', '--token', '$SERVICE_TOKEN', '--api-key', 'secret://existing', '--token-file', 'token.txt',
                '--password=<your-password>', '--no-auth', '--auth-mode', 'oauth']
        (self.home / '.mcp.json').write_text(json.dumps({'mcpServers': {'plain': {'command': 'uvx', 'args': args}}}), encoding='utf-8')
        report = self.scan()
        item = next(i for i in report['items'] if i['kind'] == 'mcp_server' and i['name'] == 'plain')
        self.assertEqual((item['status'], item['secrets']), ('new', 0))
        self.assertEqual(self.apply(report, [item['id']])['counts'], {'imported': 1, 'rejected': 0})
        definition = json.loads((self.local / 'mcp/servers/plain.json').read_text(encoding='utf-8'))
        self.assertEqual(definition['args'], args)

    def test_literal_credential_args_are_refused_in_definitions(self):
        token = 'arg-tok-' + 'z' * 24
        for args in (['--api-key', token], ['--token=' + token], ['x', '--auth', token]):
            with self.subTest(args=args):
                definition = {'name': 'direct', 'description': 'Direct', 'transport': 'stdio', 'command': 'uvx', 'args': args}
                with self.assertRaisesRegex(ValueError, 'credential in command-line args'):
                    validators.mcp(definition)
        ok = {'name': 'direct', 'description': 'Direct', 'transport': 'stdio', 'command': 'uvx',
              'args': ['--api-key', 'secret://direct-key', '--token=secret://direct-token', '--no-auth']}
        self.assertEqual(validators.mcp(ok), ok)

    def test_short_secret_key_value_is_not_matched_inside_other_words(self):
        self.write_server_env({'API_KEY': 'abc'})
        self.write_skill('words', 'The alphabet and abcdef are fine.')
        report = self.scan()
        self.assertEqual(next(i for i in report['items'] if i['name'] == 'words')['status'], 'new')

    def test_redacted_items_surface_in_safety_report(self):
        token = 'tok-' + 'e' * 40
        self.write_server_env({'SERVICE_TOKEN': token})
        self.write_skill('leaky', 'Use ' + token + ' directly.')
        report = self.scan()
        flagged = [i['name'] for items in scanner.safety_report(report)['agents'].values() for i in items]
        self.assertIn('leaky', flagged)

    def test_routine_name_does_not_carry_long_digit_runs(self):
        path = self.home / '.t3/userdata/state.sqlite'
        with contextlib.closing(sqlite3.connect(path)) as connection:
            connection.executescript((self.home / '.t3/userdata/schema.sql').read_text(encoding='utf-8'))
            connection.execute('INSERT INTO scheduled_tasks VALUES (?,?,?,?,?,?,?)',
                               ('one', 'Report for advertiser 2025550100', 'Summarize', 1, json.dumps({'type': 'interval', 'everyMs': 60000}), '', ''))
            connection.commit()
        report = self.scan()
        routines = [i for i in report['items'] if i['agent'] == 't3' and i['kind'] == 'routine']
        self.assertEqual(len(routines), 1)
        self.assertNotRegex(routines[0]['name'], r'\d{6}')
        self.assertTrue(routines[0]['importable'])

    def t3_routines(self, *titles, prompt='Summarize'):
        path = self.home / '.t3/userdata/state.sqlite'
        with contextlib.closing(sqlite3.connect(path)) as connection:
            connection.executescript((self.home / '.t3/userdata/schema.sql').read_text(encoding='utf-8'))
            for index, title in enumerate(titles):
                connection.execute('INSERT INTO scheduled_tasks VALUES (?,?,?,?,?,?,?)',
                                   (f'task-{index}', title, prompt, 1, json.dumps({'type': 'interval', 'everyMs': 60000}), '', ''))
            connection.commit()
        return [i for i in self.scan()['items'] if i['agent'] == 't3' and i['kind'] == 'routine']

    def test_long_routine_title_is_cut_at_a_hyphen_with_a_stable_unique_id(self):
        base = 'Recheck the example reporting workspace overview every three days and summarize the '
        base_slug = 'recheck-the-example-reporting-workspace-overview-every-three-days-and-summarize-the-'
        first, second = self.t3_routines(base + 'alpha results', base + 'bravo results')
        for item in (first, second):
            self.assertLessEqual(len(item['name']), 63)
            self.assertRegex(item['name'], r'-[0-9a-f]{6}$')
            self.assertTrue(base_slug.startswith(item['name'][:-6]))
            self.assertEqual(item['name'][:-7], 'recheck-the-example-reporting-workspace-overview-every')
            self.assertEqual(item['status'], 'new')
        self.assertNotEqual(first['name'], second['name'])
        again = [i['name'] for i in self.scan()['items'] if i['agent'] == 't3' and i['kind'] == 'routine']
        self.assertEqual(again, [first['name'], second['name']])

    def test_hidden_routine_titles_with_matching_content_keep_distinct_ids(self):
        titles = ('Report for advertiser 2025550100', 'Report for advertiser 2025550101',
                  'Recheck account 202-555-0100 weekly', 'Recheck account 202-555-0101 weekly')
        items = self.t3_routines(*titles)
        names = [item['name'] for item in items]
        self.assertEqual(len(set(names)), len(titles))
        self.assertTrue(all(name.startswith('routine-') for name in names))
        again = [i['name'] for i in self.scan()['items'] if i['agent'] == 't3' and i['kind'] == 'routine']
        self.assertEqual(names, again)
        report = self.scan()
        result = scanner.apply(self.root, report, [i['id'] for i in items], self.store, self.home,
                               platform='linux', env={}, runner=self.runner, approved=[i['id'] for i in items])
        self.assertEqual(result['counts']['imported'], len(titles))
        self.assertEqual({p.name for p in (self.local / 'routines').iterdir()}, set(names))

    def test_long_routine_title_with_numeric_hash_keeps_title_id(self):
        title = 'Review the sample team dashboard numbers every second week and post the south region summary'
        item, = self.t3_routines(title)
        self.assertEqual(item['name'], scanner.routine_id(title))
        self.assertTrue(item['name'].endswith('-850061'))
        self.assertEqual(item['status'], 'new')
        self.assertEqual(self.apply(self.scan(), [item['id']])['counts']['imported'], 1)

    def test_short_routine_title_has_no_hash(self):
        self.assertEqual(self.t3_routines('Weekly digest')[0]['name'], 'weekly-digest')

    def test_routine_title_with_account_number_shape_needs_review(self):
        item, = self.t3_routines('Recheck account 202-555-0100 weekly')
        self.assertEqual(item['status'], 'needs_review')
        self.assertFalse(item['importable'])
        self.assertNotIn('202-555-0100', item['name'])
        self.assertTrue(any('account' in f['reason'] for f in item['review']), item['review'])
        self.assertEqual(self.apply(self.scan(), [item['id']])['counts']['imported'], 0)

    def test_routine_prompt_with_account_number_shape_needs_review(self):
        for title in ('Recheck example workspace', 'Recheck account 202-555-0100 weekly'):
            with self.subTest(title=title):
                (self.home / '.t3/userdata/state.sqlite').unlink(missing_ok=True)
                item, = self.t3_routines(title, prompt='Open the example report for account 202-555-0100 and summarize it')
                self.assertEqual(item['status'], 'needs_review')
                self.assertFalse(item['importable'])
                self.assertNotIn('202-555-0100', item['name'])
                self.assertTrue(any('prompt' in f['reason'] for f in item['review']), item['review'])
                self.assertEqual(self.apply(self.scan(), [item['id']])['counts']['imported'], 0)

    def test_routine_description_is_the_title_unless_it_holds_an_account_number(self):
        item, = self.t3_routines('Weekly   digest of  the example workspace')
        scanner.apply(self.root, self.scan(), [item['id']], self.store, self.home, platform='linux', env={}, runner=self.runner)
        value = json.loads((self.local / 'routines' / item['name'] / 'routine.json').read_text(encoding='utf-8'))
        self.assertEqual(value['description'], 'Weekly digest of the example workspace')

    def test_routine_digits_never_reach_the_locker_or_the_scan_report(self):
        for title, risky, digits in [('Recheck account 202-555-0100 weekly', True, '202-555-0100'), ('Report for advertiser 2025550100', False, '2025550100')]:
            with self.subTest(title=title):
                (self.home / '.t3/userdata/state.sqlite').unlink(missing_ok=True)
                item, = self.t3_routines(title, prompt=title)
                report = self.scan()
                self.assertEqual(item['status'], 'needs_review' if risky else 'new')
                self.assertNotIn(digits, json.dumps(report))
                scanner.apply(self.root, report, [item['id']], self.store, self.home, platform='linux', env={}, runner=self.runner, approved=[item['id']])
                text = (self.local / 'routines' / item['name'] / 'routine.json').read_text(encoding='utf-8')
                self.assertNotIn(digits, text)
                self.assertNotIn(digits.replace('-', ''), text)
                self.assertEqual(json.loads(text)['description'], 'Imported disabled schedule')

    def test_very_long_routine_title_is_still_imported(self):
        item, = self.t3_routines('Review ' + 'the example workspace overview ' * 200)
        self.assertEqual(item['status'], 'new')
        self.assertLessEqual(len(item['name']), 63)
        self.assertEqual(self.apply(self.scan(), [item['id']])['counts']['imported'], 1)

    def test_claude_project_groups_keep_separate_sources_and_duplicates(self):
        server = {'command': 'example', 'args': ['project']}
        (self.home / '.claude.json').write_text(json.dumps({'projects': {'/work/one': {'mcpServers': {'project-tool': server}}, '/work/two': {'mcpServers': {'project-tool': server}}}}), encoding='utf-8')
        report = self.scan()
        items = [i for i in report['items'] if i['name'] == 'project-tool']
        self.assertEqual([i['status'] for i in items], ['new', 'duplicate'])
        self.assertNotEqual(items[0]['source'], items[1]['source'])
        self.assertEqual(items[1]['duplicate_of'], items[0]['id'])

    def test_cron_alias_and_command_are_inert(self):
        self.runner = lambda command: '@daily example digest\n0 9 * * 1 example weekly\n'
        report = self.scan()
        cron = [i for i in report['items'] if i['source'] == '<system:crontab>']
        self.assertEqual(len(cron), 2)
        self.apply(report, [i['id'] for i in cron])
        daily = json.loads((self.local / 'routines/cron-1/routine.json').read_text(encoding='utf-8'))
        self.assertFalse(daily['enabled'])
        self.assertEqual(daily['schedule'], {'type': 'cron', 'expression': '0 0 * * *'})

    def test_windows_env_directories_are_independent_of_host(self):
        app = self.base / 'custom-appdata'
        source = FIXTURES / 'windows/AppData/Roaming/opencode'
        shutil.copytree(source, app / 'opencode')
        report = scanner.Inventory(self.home, (), 'win32', {'APPDATA': str(app), 'LOCALAPPDATA': str(self.base / 'local')}, lambda command: '', self.root).discover()
        self.assertTrue(any(i['agent'] == 'opencode' and i['name'] == 'open-local' for i in report['items']))

    def test_own_home_path_is_rewritten_to_tilde_before_validation(self):
        (self.home / '.claude/CLAUDE.md').write_text(f'Keep notes in {self.home}/notes and {self.home}/bin/tool.\n', encoding='utf-8')
        (self.home / '.config/systemd/user/homejob.timer').write_text('[Timer]\nOnCalendar=daily\n', encoding='utf-8')
        (self.home / '.config/systemd/user/homejob.service').write_text(f'[Service]\nExecStart={self.home}/bin/run --data {self.home}/data\n', encoding='utf-8')
        (self.home / '.mcp.json').write_text(json.dumps({'mcpServers': {'homebound': {'command': f'{self.home}/bin/server', 'args': [f'{self.home}/data', '--flag']}}}), encoding='utf-8')
        report = self.scan()
        wanted = {('rule', 'claude'), ('routine', 'homejob'), ('mcp_server', 'homebound')}
        items = [i for i in report['items'] if (i['kind'], i['name']) in wanted and i['agent'] in ('claude', 'system')]
        self.assertEqual({(i['kind'], i['name']) for i in items}, wanted)
        self.assertEqual({i['status'] for i in items}, {'new'})
        result = self.apply(report, [i['id'] for i in items])
        self.assertEqual(result['counts'], {'imported': 3, 'rejected': 0})
        rule = (self.local / 'rules/claude.md').read_text(encoding='utf-8')
        self.assertEqual(rule, 'Keep notes in ~/notes and ~/bin/tool.\n')
        prompt = (self.local / 'routines/homejob/prompt.md').read_text(encoding='utf-8')
        self.assertIn('ExecStart=~/bin/run --data ~/data', prompt)
        server = json.loads((self.local / 'mcp/servers/homebound.json').read_text(encoding='utf-8'))
        self.assertEqual((server['command'], server['args']), ('~/bin/server', ['~/data', '--flag']))
        self.assertNotIn(str(self.home), json.dumps([rule, prompt, server]))

    def test_import_writes_files_without_translating_newlines(self):
        (self.home / '.claude/CLAUDE.md').write_bytes(b'Keep notes short.\n')
        (self.home / '.config/systemd/user/homejob.timer').write_bytes(b'[Timer]\nOnCalendar=daily\n')
        (self.home / '.config/systemd/user/homejob.service').write_bytes(b'[Service]\nExecStart=/bin/true\n')
        report = self.scan()
        items = [i for i in report['items'] if (i['kind'], i['name']) in {('rule', 'claude'), ('routine', 'homejob')} and i['agent'] in ('claude', 'system')]
        self.assertEqual(len(items), 2)
        with windows_text_writes():
            result = self.apply(report, [i['id'] for i in items])
        self.assertEqual(result['counts'], {'imported': 2, 'rejected': 0})
        written = [p for p in self.local.rglob('*') if p.is_file()]
        self.assertTrue(written)
        self.assertEqual([p.relative_to(self.local).as_posix() for p in written if b'\r' in p.read_bytes()], [])
        self.assertEqual((self.local / 'routines/homejob/prompt.md').read_bytes(), b'[Service]\nExecStart=/bin/true\n')

    def test_home_path_does_not_hide_other_private_findings(self):
        address = str(ipaddress.IPv4Address((100 << 24) + (64 << 16) + 258))
        (self.home / '.claude/CLAUDE.md').write_text(f'Use {self.home}/bin/tool on {address}.\n', encoding='utf-8')
        (self.home / '.config/systemd/user/homejob.timer').write_text('[Timer]\nOnCalendar=daily\n', encoding='utf-8')
        (self.home / '.config/systemd/user/homejob.service').write_text(f'[Service]\nExecStart={self.home}/bin/run {address}\n', encoding='utf-8')
        (self.home / '.mcp.json').write_text(json.dumps({'mcpServers': {'homebound': {'command': f'{self.home}/bin/server', 'args': [address]}}}), encoding='utf-8')
        report = self.scan()
        items = [i for i in report['items'] if (i['kind'], i['name']) in {('rule', 'claude'), ('routine', 'homejob'), ('mcp_server', 'homebound')} and i['agent'] in ('claude', 'system')]
        self.assertEqual(len(items), 3)
        self.assertEqual({i['status'] for i in items}, {'skipped'})

    def test_sibling_directory_sharing_home_prefix_is_not_rewritten(self):
        home = self.base / 'home/user'
        shutil.copytree(FIXTURES / 'linux', home)
        text = f'Run {home.as_posix()}2/bin and {home.as_posix()}/bin\n'
        (home / '.claude/CLAUDE.md').write_text(text, encoding='utf-8')
        inventory = scanner.Inventory(home, (), 'linux', {}, self.runner, self.root)
        self.assertEqual(inventory.tilde(text), f'Run {home.as_posix()}2/bin and ~/bin\n')
        report = inventory.discover()
        rule = [i for i in report['items'] if i['agent'] == 'claude' and i['kind'] == 'rule' and i['name'] == 'claude']
        self.assertEqual([i['status'] for i in rule], ['skipped'])

    def test_windows_home_prefix_ignores_siblings_and_matches_case_and_separators(self):
        home = PureWindowsPath('D:\\Profiles\\me')
        pattern = scanner.home_pattern(home, 'win32')
        text = 'D:\\Profiles\\me2\\bin D:\\Profiles\\me\\bin d:/profiles/ME/bin D:/Profiles/me-old/x D:/Profiles/me/.cfg'
        self.assertEqual(pattern.sub('~', text), 'D:\\Profiles\\me2\\bin ~\\bin ~/bin D:/Profiles/me-old/x ~/.cfg')
        posix = scanner.home_pattern(PurePosixPath('/srv/me'), 'linux')
        self.assertEqual(posix.sub('~', '/srv/me2/x /srv/me/x /SRV/me/x'), '/srv/me2/x ~/x /SRV/me/x')

    def test_metadata_rejects_credentials_and_unknown_fields(self):
        for value in ({'name':'connection','provider':'fixture','endpoint':'https://example.invalid/mcp?key=private','auth_status':'unknown'},
                      {'name':'connection','provider':'fixture','endpoint':'https://example.invalid/mcp','auth_status':'unknown','token':CANARY}):
            with self.assertRaises(ValueError):
                validators.metadata(value, 'connection')


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


class InstructionRuleNameTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.root = self.base / 'locker'
        self.root.mkdir()
        for name in ('manifest.json', 'LICENSE', '.gitignore'):
            shutil.copyfile(ROOT / name, self.root / name)
        for folder in ('skills', 'rules', 'routines', 'mcp', 'config'):
            shutil.copytree(ROOT / folder, self.root / folder)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.store = FileStore(self.root)

    def scan(self, projects=()):
        return scanner.Inventory(self.home, projects, 'linux', {}, lambda command: '', self.root).discover()

    def rules(self, report):
        return {i['source']: i for i in report['items'] if i['kind'] == 'rule'}

    def test_codex_and_opencode_agents_md_both_importable(self):
        write_text(self.home / '.codex/AGENTS.md', 'Codex instructions\n')
        write_text(self.home / '.config/opencode/AGENTS.md', 'OpenCode instructions\n')
        report = self.scan()
        rules = self.rules(report)
        self.assertEqual({k: (v['name'], v['status']) for k, v in rules.items()},
                         {'~/.codex/AGENTS.md': ('codex-agents', 'new'), '~/.config/opencode/AGENTS.md': ('opencode-agents', 'new')})
        selected = [i['id'] for i in rules.values()]
        result = scanner.apply(self.root, report, selected, self.store, self.home, platform='linux', env={}, runner=lambda command: '')
        self.assertEqual(result['counts'], {'imported': 2, 'rejected': 0})

    def test_project_agents_md_is_named_for_the_project(self):
        project = self.base / 'widgets'
        write_text(project / 'AGENTS.md', 'Project instructions\n')
        write_text(project / 'CLAUDE.md', 'Project Claude instructions\n')
        write_text(self.home / '.codex/AGENTS.md', 'Codex instructions\n')
        rules = self.rules(self.scan((project,)))
        self.assertEqual({k: (v['name'], v['status']) for k, v in rules.items()},
                         {'<project>/AGENTS.md': ('widgets-agents', 'new'),
                          '<project>/CLAUDE.md': ('widgets-claude', 'new'),
                          '~/.codex/AGENTS.md': ('codex-agents', 'new')})

    def test_claude_instruction_file_keeps_its_name(self):
        write_text(self.home / '.claude/CLAUDE.md', 'Claude instructions\n')
        self.assertEqual([i['name'] for i in self.rules(self.scan()).values()], ['claude'])

    def test_remaining_name_conflict_names_the_other_item_and_a_next_step(self):
        write_text(self.home / '.opencode/AGENTS.md', 'First\n')
        write_text(self.home / '.config/opencode/AGENTS.md', 'Second\n')
        report = self.scan()
        first, second = self.rules(report)['~/.opencode/AGENTS.md'], self.rules(report)['~/.config/opencode/AGENTS.md']
        for item, other in ((first, second), (second, first)):
            self.assertEqual(item['status'], 'conflict')
            self.assertIn(other['id'], item['reason'])
            self.assertIn('opencode', item['reason'])
            self.assertIn(f"{portable.python_command()} hub.py import-rule {item['source']} --name <new>", item['next_step'])
            self.assertNotIn('<path>', item['next_step'])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            scanner.human(report)
        self.assertIn(second['id'], out.getvalue())
        self.assertIn('next: ' + first['next_step'], out.getvalue())

    def imported_then_changed(self, text='Changed instructions\n'):
        write_text(self.home / '.codex/AGENTS.md', 'Codex instructions\n')
        report = self.scan()
        scanner.apply(self.root, report, [i['id'] for i in report['items']], self.store, self.home, platform='linux', env={}, runner=lambda command: '')
        write_text(self.home / '.codex/AGENTS.md', text)
        return next(i for i in self.scan()['items'] if i['kind'] == 'rule')

    def test_edited_source_conflict_says_the_source_changed_and_names_the_path(self):
        item = self.imported_then_changed()
        self.assertEqual(item['status'], 'conflict')
        self.assertNotIn('a different rule', item['reason'])
        self.assertIn('codex-agents', item['reason'])
        self.assertIn('changed since it was imported', item['reason'])
        self.assertEqual(item['next_step'], f'Re-import it with {portable.python_command()} hub.py import-rule ~/.codex/AGENTS.md --name codex-agents --origin scan-codex')

    def test_conflict_with_an_unrelated_locker_entry_keeps_the_different_wording(self):
        write_text(self.home / '.codex/AGENTS.md', 'Codex instructions\n')
        report = self.scan()
        scanner.apply(self.root, report, [i['id'] for i in report['items']], self.store, self.home, platform='linux', env={}, runner=lambda command: '')
        catalog = self.root / '.hub-store/catalog/manifest.json'
        manifest = json.loads(catalog.read_text(encoding='utf-8'))
        for entry in manifest['rules']:
            if entry['name'] == 'codex-agents':
                entry['provenance']['origin'] = 'local-import'
        catalog.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        write_text(self.home / '.codex/AGENTS.md', 'Changed instructions\n')
        item = next(i for i in self.scan()['items'] if i['kind'] == 'rule')
        self.assertEqual(item['status'], 'conflict')
        self.assertEqual(item['reason'], 'a different rule named codex-agents is already in the locker')
        self.assertEqual(item['next_step'], f'Import one with {portable.python_command()} hub.py import-rule ~/.codex/AGENTS.md --name <new>')

    def test_conflict_without_a_usable_source_path_keeps_the_placeholder(self):
        item = {'kind': 'rule', 'agent': 'codex', 'name': 'x', 'source': '<project>/AGENTS.md'}
        scanner.conflict(item, 'reason')
        self.assertEqual(item['next_step'], f'Import one with {portable.python_command()} hub.py import-rule <path> --name <new>')


class LocalZoneTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.home = self.base / 'home'
        units = self.home / '.config/systemd/user'
        write_text(units / 'backup.service', '[Service]\nExecStart=example job\n')
        write_text(units / 'backup.timer', '[Timer]\nOnCalendar=*-*-* 09:00:00\n')
        write_text(units / 'zoned.service', '[Service]\nExecStart=example job\n')
        write_text(units / 'zoned.timer', '[Timer]\nOnCalendar=*-*-* 09:00:00 Europe/Paris\n')
        self.root = self.base / 'locker'
        self.root.mkdir()
        for name in ('manifest.json', 'LICENSE', '.gitignore'):
            shutil.copyfile(ROOT / name, self.root / name)
        for folder in ('skills', 'rules', 'routines', 'mcp', 'config'):
            shutil.copytree(ROOT / folder, self.root / folder)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.store = FileStore(self.root)
        self.runner = lambda command: '0 9 * * * example job\n' if command == ['crontab', '-l'] else ''

    def routines(self, env, platform='linux'):
        inventory = scanner.Inventory(self.home, (), platform, env, self.runner, self.root)
        report = inventory.discover()
        return {i['name']: inventory.payloads[i['id']][0][0] for i in report['items'] if i['kind'] == 'routine'}

    def test_crontab_and_timer_use_local_timezone(self):
        routines = self.routines({'TZ': 'America/New_York'})
        self.assertEqual(routines['cron-1']['timezone'], 'America/New_York')
        self.assertEqual(routines['cron-1']['runner'], 'cron')
        self.assertEqual(routines['backup']['timezone'], 'America/New_York')
        self.assertEqual(routines['backup']['runner'], 'systemd-timer')
        self.assertEqual(routines['zoned']['timezone'], 'Europe/Paris')

    def test_timezone_falls_back_to_utc_without_a_usable_zone(self):
        for env in ({}, {'TZ': ''}, {'TZ': 'not a zone'}):
            with self.subTest(env=env):
                self.assertEqual({r['timezone'] for r in self.routines(env).values()} - {'Europe/Paris'}, {'UTC'})

    def test_localtime_link_names_the_zone_when_tz_is_unset(self):
        link = self.base / 'etc/localtime'
        link.parent.mkdir()
        link.symlink_to('/usr/share/zoneinfo/Asia/Tokyo')
        self.assertEqual(scanner.local_timezone({}, link), 'Asia/Tokyo')
        self.assertEqual(scanner.local_timezone({'TZ': ':America/Chicago'}, link), 'America/Chicago')
        self.assertEqual(scanner.local_timezone({}, self.base / 'missing'), 'UTC')
        self.assertEqual(scanner.local_timezone({}, None), 'UTC')

    def test_launchd_routines_are_labelled_launchd_in_the_local_zone(self):
        agents = self.home / 'Library/LaunchAgents'
        agents.mkdir(parents=True)
        (agents / 'job.plist').write_bytes(scanner.plistlib.dumps({'Label': 'job', 'StartInterval': 600, 'ProgramArguments': ['example']}))
        job = self.routines({'TZ': 'America/Denver'}, 'darwin')['job']
        self.assertEqual((job['runner'], job['timezone']), ('launchd', 'America/Denver'))

    def test_win32_scan_lists_no_task_scheduler_agent(self):
        report = scanner.Inventory(self.home, (), 'win32', {}, scanner.default_runner, self.root).discover()
        self.assertNotIn('windows-task-scheduler', [a['name'] for a in report['not_scanned']['agents']])
        self.assertEqual(report['not_scanned']['agents'], [])


if __name__ == '__main__':
    unittest.main()
