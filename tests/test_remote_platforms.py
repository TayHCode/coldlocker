from contextlib import redirect_stdout
import io
import json
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import connect
import hub
import routine_enable
import remote_connect


ROOT = Path(__file__).resolve().parents[1]
URL = 'https://catalog.example.org/mcp'
OVERRIDE = 'https://library.example.net:8443/mcp'


class RemotePlatformsTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.scratch'
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        shutil.copytree(ROOT / 'routines', self.root / 'routines')
        (self.root / 'manifest.json').write_bytes((ROOT / 'manifest.json').read_bytes())
        self.config = self.root / '.hub-store/remote.json'
        self.config.parent.mkdir()
        self.config.write_text(json.dumps({'public_url': URL, 'account_id': 'fixture-account'}))

    def connect(self, *extra):
        args = hub.parser().parse_args(['connect', '--remote', '--home', str(self.home), *extra])
        output = io.StringIO()
        with patch.object(connect, 'detect', side_effect=AssertionError('local config inspected')):
            code = connect.run(args, self.root, output=output)
        self.assertEqual(code, 0)
        self.assertEqual(list(self.home.iterdir()), [])
        return json.loads(output.getvalue())

    def test_remote_lists_hosted_clients_without_local_detection(self):
        result = self.connect()
        self.assertTrue(result['print_only'])
        self.assertEqual(result['url'], URL)
        self.assertTrue({'grok', 'dots', 'muse', 'mausbot', 'claude_ai', 'claude_desktop', 'chatgpt', 'codex', 't3'} <= set(result['clients']))
        for value in result['clients'].values():
            self.assertIn(value['status'], ('verified-from-docs', 'unconfirmed'))
            self.assertTrue(value['sources'])
        for name in ('dots', 'muse', 'mausbot', 't3'):
            self.assertEqual(result['clients'][name]['status'], 'unconfirmed')
        self.assertIn('hub_list_rules', result['verify_prompt'])

    def test_remote_client_filter_and_codex_oauth(self):
        clients = self.connect('--agent', 'codex')['clients']
        self.assertEqual(set(clients), {'codex'})
        self.assertIn('codex mcp login agentlocker', clients['codex']['steps'])

    def test_remote_prints_catalog_scope_without_accessing_secrets(self):
        with patch('store.FileStore', side_effect=AssertionError('store accessed')):
            result = self.connect('--agent', 'codex')
        self.assertEqual(result['scope'], 'catalog:read')

    def test_remote_rejects_local_only_client(self):
        with self.assertRaisesRegex(ValueError, 'no researched instructions'):
            self.connect('--agent', 'cursor')

    def test_remote_deduplicates_requested_clients(self):
        self.assertEqual(list(self.connect('--agent', 'codex', 'codex')['clients']), ['codex'])

    def test_remote_routine_guards_all_approved_controls(self):
        value, _ = routine_enable.load(self.root, 'inventory-digest')
        plan = routine_enable.remote_plan('codex', value, self.root)
        expected = {key: value[key] for key in ('id', 'owner', 'runner', 'schedule', 'timezone', 'overlap', 'timeout_seconds', 'prompt')}
        self.assertIn(json.dumps(expected, sort_keys=True), plan['prompt'])
        self.assertEqual(plan['scope'], 'catalog:read')

    def test_direct_remote_run_uses_fixture_root(self):
        args = hub.parser().parse_args(['connect', '--remote', '--agent', 'codex'])
        output = io.StringIO()
        with patch('store.FileStore', side_effect=AssertionError('store opened')):
            self.assertEqual(remote_connect.run(args, output, self.root), 0)
        self.assertEqual(json.loads(output.getvalue())['url'], URL)

    def test_remote_routine_unconfirmed_connections_and_no_scheduler_calls(self):
        for runner in ('t3-scheduler', 'dots', 'muse', 'mausbot'):
            with self.subTest(runner=runner):
                args = hub.parser().parse_args(['routine', 'enable', 'inventory-digest', '--runner', runner, '--remote'])
                output = io.StringIO()
                with patch.object(routine_enable, 'call', side_effect=AssertionError('scheduler called')), patch.object(routine_enable, 'reconcile', side_effect=AssertionError('write attempted')), redirect_stdout(output):
                    self.assertEqual(routine_enable.execute(args, self.root, self.home), 0)
                plan = json.loads(output.getvalue())['plan']
                self.assertEqual(plan['connection']['status'], 'unconfirmed')
                self.assertEqual(list(self.home.iterdir()), [])

    def test_remote_write_refused_before_detection(self):
        with self.assertRaisesRegex(ValueError, 'print-only'):
            self.connect('--write', '--yes')

    def test_hosted_client_requires_remote_mode(self):
        args = hub.parser().parse_args(['connect', '--agent', 'dots', '--home', str(self.home)])
        with self.assertRaisesRegex(ValueError, '--remote'):
            connect.run(args, self.root)

    def test_remote_routine_handoffs_fetch_and_guard_current_catalog(self):
        for runner in ('t3-scheduler', 'grok-bot', 'muse', 'dots', 'codex', 'claude-code', 'mausbot'):
            with self.subTest(runner=runner):
                args = hub.parser().parse_args(['routine', 'enable', 'inventory-digest', '--runner', runner, '--remote'])
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(routine_enable.execute(args, self.root, self.home), 0)
                result = json.loads(output.getvalue())
                self.assertFalse(result['write'])
                self.assertIn(URL, result['plan']['activation_prompt'])
                self.assertNotIn('agentlocker.dev', output.getvalue())
                plan = result['plan']
                value, _ = routine_enable.load(self.root, 'inventory-digest')
                self.assertIn(URL, routine_enable.remote_plan(runner, value, self.root)['activation_prompt'])
                self.assertNotIn('files', plan)
                self.assertIn('hub_get_item', plan['prompt'])
                self.assertIn('"kind": "routine"', plan['prompt'])
                self.assertIn('"name": "inventory-digest"', plan['prompt'])
                self.assertIn('owner', plan['prompt'])
                self.assertIn('enabled=false', plan['prompt'])
                self.assertIn('stop', plan['prompt'])
                self.assertIn('single runner', plan['activation_prompt'])
                if runner == 't3-scheduler':
                    self.assertEqual(plan['payload']['prompt'], plan['prompt'])
                self.assertEqual(list(self.home.iterdir()), [])

    def test_remote_routine_refuses_write_disable_and_local_runners(self):
        for extra in (['--runner', 'dots', '--write', '--yes'], ['--runner', 'cron']):
            args = hub.parser().parse_args(['routine', 'enable', 'inventory-digest', '--remote', *extra])
            with self.assertRaisesRegex(ValueError, 'remote'):
                routine_enable.execute(args, self.root, self.home)
        args = hub.parser().parse_args(['routine', 'disable', 'inventory-digest', '--runner', 'dots', '--remote'])
        with self.assertRaisesRegex(ValueError, 'remote'):
            routine_enable.execute(args, self.root, self.home)

    def test_configured_url_in_every_generated_client(self):
        result = self.connect()
        self.assertNotIn('agentlocker.dev', json.dumps(result))
        for name, client in result['clients'].items():
            self.assertIn(URL, client['steps'], name)
        self.assertEqual(remote_connect.clients(self.root), result['clients'])

    def test_connect_override_precedes_invalid_config(self):
        self.config.write_text('not json')
        result = self.connect('--url', OVERRIDE)
        self.assertEqual(result['url'], OVERRIDE)
        self.assertIn(OVERRIDE, result['clients']['codex']['steps'])
        self.assertNotIn(URL, json.dumps(result))

    def test_missing_url_is_actionable_and_has_no_owner_default(self):
        self.config.unlink()
        with self.assertRaisesRegex(ValueError, 'public_url.*remote.json') as raised:
            self.connect()
        self.assertNotIn('agentlocker.dev', str(raised.exception))
        value, _ = routine_enable.load(self.root, 'inventory-digest')
        with self.assertRaisesRegex(ValueError, 'public_url'):
            routine_enable.remote_plan('muse', value, self.root)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_remote_routine_override_and_no_store_access(self):
        self.config.write_text('invalid')
        for runner in ('t3-scheduler', 'grok-bot', 'muse', 'dots', 'codex', 'claude-code', 'mausbot', 'n8n'):
            args = hub.parser().parse_args(['routine', 'enable', 'inventory-digest', '--runner', runner, '--remote', '--url', OVERRIDE])
            output = io.StringIO()
            with patch.object(routine_enable, 'FileStore', side_effect=AssertionError('store opened')), redirect_stdout(output):
                self.assertEqual(routine_enable.execute(args, self.root, self.home), 0)
            plan = json.loads(output.getvalue())['plan']
            self.assertEqual(plan['catalog_url'], OVERRIDE)
            self.assertIn(OVERRIDE, plan['prompt'])
            self.assertIn(OVERRIDE, plan['activation_prompt'])
            self.assertNotIn('agentlocker.dev', output.getvalue())

    def test_url_requires_remote_mode_before_detection_or_store_access(self):
        args = hub.parser().parse_args(['connect', '--url', URL])
        with patch.object(connect, 'detect', side_effect=AssertionError), self.assertRaisesRegex(ValueError, '--url.*--remote'):
            connect.run(args, self.root)
        for flags in (['enable', '--runner', 'codex'], ['disable', '--runner', 'codex']):
            args = hub.parser().parse_args(['routine', flags[0], 'inventory-digest', *flags[1:], '--url', URL])
            with patch.object(routine_enable, 'FileStore', side_effect=AssertionError), self.assertRaisesRegex(ValueError, '--url.*--remote'):
                routine_enable.execute(args, self.root, self.home)

    def test_invalid_overrides_fail_before_local_access(self):
        for url in ('http://catalog.example.org/mcp', URL + '?token=fixture', URL + '#', 'https://user:pass@example.org/mcp'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.connect('--url', url)
            value, _ = routine_enable.load(self.root, 'inventory-digest')
            with self.assertRaises(ValueError):
                routine_enable.remote_plan('codex', value, self.root, url)

    def test_import_and_parser_discovery_need_no_remote_configuration(self):
        import importlib
        with patch('remote_settings.catalog_url', side_effect=AssertionError('configuration read')):
            importlib.reload(remote_connect)
            self.assertIn('grok', remote_connect.AGENTS)
            hub.parser()

    def test_local_muse_handoff_has_no_owner_url(self):
        value, prompt = routine_enable.load(self.root, 'inventory-digest')
        plan = routine_enable.ADAPTERS['muse'].plan('muse', value, prompt)
        self.assertNotIn('agentlocker.dev', json.dumps(plan))
