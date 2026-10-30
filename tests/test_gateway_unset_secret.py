import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import gateway
import gateway_adapter
import hub
import modes
from store import FileStore
from test_gateway import cli
import test_verify

NEXT_STEP = 'set it on the Sign in screen'


class UnsetSecretTests(unittest.TestCase):
    setUp = test_verify.VerifyTests.setUp

    def add_server(self, name, **fields):
        path = self.root / f'mcp/servers/{name}.json'
        path.write_text(json.dumps({**self.definition, 'name': name, **fields}), encoding='utf-8')
        manifest = json.loads((self.root / 'manifest.json').read_text(encoding='utf-8'))
        hub.record(manifest, 'mcp_servers', name, path, 'agentlocker', 'authored', root=self.root)
        (self.root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    def mixed(self):
        (self.root / 'config.json').unlink()
        self.add_server('clean')
        self.add_server('needs-env', env={'API_TOKEN': 'secret://unset-env'})
        self.add_server('needs-arg', args=['--api-key', 'secret://unset-arg'])
        FileStore(self.root).put('set-token', 'a-value')
        self.add_server('has-env', env={'API_TOKEN': 'secret://set-token'})

    def test_prepare_omits_unset_servers_and_environment_resolves(self):
        self.mixed()
        runtime = gateway_adapter.prepare(self.root, self.root)
        prepared = gateway.settings(runtime)
        self.assertEqual(sorted(prepared['mcpServers']), ['clean', 'demo', 'has-env'])
        environment = gateway_adapter.environment(prepared, FileStore(self.root))
        self.assertIn('a-value', environment.values())

    def test_supervisor_launches_with_an_unset_secret_server_stored(self):
        self.mixed()
        runtime = gateway_adapter.prepare(self.root, self.root)
        for platform in ('linux', 'darwin', 'win32'):
            with self.subTest(platform=platform):
                child = MagicMock(poll=MagicMock(return_value=0), pid=1)
                child.stdout.__iter__.return_value = iter(())
                with patch('sys.platform', platform), patch.object(gateway, 'ROOT', self.root), \
                        patch.object(gateway, 'check_node'), patch.object(gateway, 'check_runtime'), \
                        patch.object(gateway, 'identity', return_value='fixed-identity'), \
                        patch.object(gateway.signal, 'signal'), \
                        patch.object(gateway.subprocess, 'Popen', return_value=child) as popen:
                    gateway.run_supervisor(runtime, 43871, False)
                self.assertEqual(popen.call_count, 1)
                self.assertNotIn('FileNotFoundError', repr(popen.call_args))

    def test_start_refreshes_settings_after_a_secret_is_deleted(self):
        self.mixed()
        runtime = gateway_adapter.prepare(self.root, self.root)
        FileStore(self.root).delete('set-token')
        with patch('sys.platform', modes.GATEWAY_PLATFORM), patch.object(gateway, 'ROOT', self.root), \
                patch.object(gateway, 'check_node'), patch.object(gateway, 'prerequisite'), \
                patch.object(gateway, 'check_runtime'), patch.object(gateway, 'require_free'), \
                patch.object(gateway.subprocess, 'Popen', side_effect=RuntimeError('stop here')):
            with self.assertRaises(RuntimeError):
                gateway.start(runtime, 43871)
        self.assertNotIn('has-env', gateway.settings(runtime)['mcpServers'])
        self.assertIn('clean', gateway.settings(runtime)['mcpServers'])

    def test_skipped_servers_are_named_with_the_reference_and_never_a_value(self):
        self.mixed()
        found = gateway_adapter.skipped(self.root, FileStore(self.root))
        self.assertEqual(found, {'needs-env': ['unset-env'], 'needs-arg': ['unset-arg']})

    def running(self):
        runtime = self.root / '.hub-store/gateway'
        runtime.mkdir(parents=True)
        (runtime / 'pid.json').write_text('{}', encoding='utf-8')
        (runtime / 'mcp_settings.json').write_text('{}', encoding='utf-8')
        self.http.tools = [{'name': 'clean-run'}]
        for name, value in (('sys.platform', modes.GATEWAY_PLATFORM),
                            ('gateway.state', lambda path: {'pid': 4242, 'port': self.http.server_port}),
                            ('gateway.start', lambda *arguments: None)):
            patcher = patch(name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_cli_start_and_status_name_each_skipped_server(self):
        self.mixed()
        self.running()
        for command in ('start', 'status'):
            code, output, errors = cli(self.root, command)
            self.assertEqual(code, 0)
            lines = output.splitlines()
            self.assertIn(f'skipped: needs-env needs secret://unset-env; {NEXT_STEP}', lines)
            self.assertIn(f'skipped: needs-arg needs secret://unset-arg; {NEXT_STEP}', lines)
            self.assertNotIn('a-value', output)
            self.assertNotIn('inspect protected diagnostics', errors)

    def test_skipped_servers_are_not_counted_or_listed_as_failed(self):
        self.mixed()
        self.running()
        for command in ('start', 'status'):
            code, output, _ = cli(self.root, command)
            self.assertEqual(code, 0)
            self.assertIn('servers: 1/3 connected; failed: demo, has-env; skipped: 2', output.splitlines())
        import hub_ui
        result = hub_ui.gateway_connection(self.root)
        self.assertEqual(result['total'], 3)
        self.assertEqual([item['name'] for item in result['down']], ['demo', 'has-env'])

    def test_connection_lists_skipped_servers(self):
        self.mixed()
        self.running()
        import hub_ui
        result = hub_ui.gateway_connection(self.root)
        self.assertEqual(result['skipped'], [
            {'name': 'needs-arg', 'missing': ['unset-arg'], 'next_step': NEXT_STEP},
            {'name': 'needs-env', 'missing': ['unset-env'], 'next_step': NEXT_STEP}])


if __name__ == '__main__':
    unittest.main()
