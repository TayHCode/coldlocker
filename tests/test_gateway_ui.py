import json
import socket
import time
import unittest
from unittest.mock import patch

import gateway
import modes
import portable
from gateway_adapter import LOOPBACK, environment, prepare, save
from gateway import settings
from gateway_render import placeholder
from hub_ui import gateway_connection
import test_ui_auth
from ui_auth import entry_prefix


class GatewayUITests(unittest.TestCase):
    setUp = test_ui_auth.AuthenticationTests.setUp
    stop = test_ui_auth.AuthenticationTests.stop
    request = test_ui_auth.AuthenticationTests.request
    post = test_ui_auth.AuthenticationTests.post
    oauth = test_ui_auth.AuthenticationTests.oauth
    connected = test_ui_auth.AuthenticationTests.connected

    def prepared(self):
        return settings(prepare(self.root, self.root))['mcpServers']['example-tools']

    def test_start_route_runs_the_cli_start_path(self):
        with patch.object(gateway, 'launch') as launch:
            result = self.post('gateway/start', {})
        launch.assert_called_once_with()
        self.assertEqual(result['gateway']['status'], 'stopped')

    def test_start_route_names_each_skipped_server_and_its_missing_reference(self):
        running = {'port': 43871, 'pid': 2, 'identity': 'x'}
        with patch.object(gateway, 'launch'), patch('hub_ui.state', return_value=running):
            result = self.post('gateway/start', {})
        self.assertEqual(result['gateway']['skipped'], [
            {'name': 'example-tools', 'missing': ['example-auth'], 'next_step': 'set it on the Sign in screen'}])

    @patch('sys.platform', modes.GATEWAY_PLATFORM)
    def test_start_route_names_the_busy_port_and_the_command_for_another(self):
        with socket.socket() as occupied:
            occupied.bind((LOOPBACK, 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            with patch.object(gateway, 'ROOT', self.root), patch.object(gateway, 'PORT', port), \
                    patch.object(gateway, 'check_runtime'), patch.object(gateway, 'prepare') as prepare, \
                    patch.object(gateway.subprocess, 'Popen') as popen:
                status, body = self.request('/api/gateway/start', {})
        self.assertEqual(status, 400)
        error = json.loads(body)['error']
        self.assertIn(f'{port} is busy', error)
        self.assertIn(f'{portable.python_command()} gateway.py start --port <port>', error)
        prepare.assert_not_called()
        popen.assert_not_called()

    @patch('sys.platform', modes.GATEWAY_PLATFORM)
    def test_start_route_reports_a_gateway_already_running_on_another_port(self):
        with socket.socket() as occupied:
            occupied.bind((LOOPBACK, 0))
            occupied.listen()
            busy = occupied.getsockname()[1]
            running = {'port': busy + 1, 'pid': 2, 'identity': 'x'}
            with patch.object(gateway, 'ROOT', self.root), patch.object(gateway, 'PORT', busy), \
                    patch.object(gateway, 'check_runtime'), patch.object(gateway, 'state', return_value=running), \
                    patch('hub_ui.state', return_value=running), patch.object(gateway.subprocess, 'Popen') as popen:
                status, body = self.request('/api/gateway/start', {})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['gateway']['url'], f'http://{LOOPBACK}:{busy + 1}/mcp')
        popen.assert_not_called()

    @patch('sys.platform', modes.GATEWAY_PLATFORM)
    def test_start_route_says_the_one_install_command_when_not_installed(self):
        with patch.object(gateway, 'ROOT', self.root):
            status, body = self.request('/api/gateway/start', {})
        self.assertEqual(status, 400)
        self.assertIn(f'{portable.python_command()} gateway.py install', json.loads(body)['error'])
        self.assertFalse((self.root / '.hub-store/gateway').exists())

    @patch('sys.platform', modes.GATEWAY_PLATFORM)
    def test_launch_prepares_then_starts_the_gateway(self):
        order = []
        with patch.object(gateway, 'ROOT', self.root), patch.object(gateway, 'check_runtime'), \
                patch.object(gateway, 'prepare', side_effect=lambda *a: order.append('prepare')), \
                patch.object(gateway, 'start', side_effect=lambda *a, **k: order.append('start')):
            gateway.launch()
        self.assertEqual(order, ['prepare', 'start'])

    def test_status_offers_start_only_when_installed(self):
        with patch.object(gateway, 'check_runtime'):
            offered = self.post('connect', {})['gateway']
        self.assertTrue(offered['startable'])
        with patch.object(gateway, 'check_runtime', side_effect=gateway.SetupError('runtime missing')):
            missing = self.post('connect', {})['gateway']
        self.assertFalse(missing['startable'])
        self.assertEqual(missing['install'], f'{portable.python_command()} gateway.py install')

    def test_oauth_header_and_hashes_survive_prepare(self):
        self.connected()
        before = (self.root / 'manifest.json').read_bytes()
        server = self.prepared()
        self.assertEqual(server['headers']['Authorization'], placeholder('secret://example-auth'))
        self.assertNotIn('oauth', server)
        self.assertIn('Bearer ' + self.auth.read('example-tools', 'access'), environment(server, self.server.store).values())
        self.assertEqual((self.root / 'manifest.json').read_bytes(), before)
        self.assertEqual(server, self.prepared())

    def test_headerless_bearer_and_raw_api_key(self):
        self.server.stage_import('mcp', {'name': 'headerless', 'description': 'Example', 'transport': 'http', 'url': self.provider.origin + '/mcp'})
        value = self.provider.mint()
        self.post('auth/bearer', {'name': 'headerless', 'header': 'Authorization', 'scheme': 'bearer', 'value': value})
        runtime = prepare(self.root, self.root)
        server = settings(runtime)['mcpServers']['headerless']
        self.assertEqual(server['headers']['Authorization'], placeholder('secret://' + entry_prefix('headerless') + '-header'))
        self.assertNotIn(value, json.dumps(server))
        self.server.stage_import('mcp', {'name': 'api-key', 'description': 'Example', 'transport': 'http', 'url': self.provider.origin + '/mcp', 'headers': {'X-Api-Key': 'secret://api-key'}})
        self.post('auth/bearer', {'name': 'api-key', 'header': 'X-Api-Key', 'scheme': 'raw', 'value': value})
        runtime = prepare(self.root, self.root)
        self.assertIn(value, environment(settings(runtime)['mcpServers']['api-key'], self.server.store).values())

    def test_expiring_token_refreshes_and_rotates_in_store(self):
        self.connected()
        old = self.auth.read('example-tools', 'access'), self.auth.read('example-tools', 'refresh')
        binding = self.auth.metadata('example-tools')
        binding['expires_at'] = time.time() + 30
        self.auth.write_metadata('example-tools', binding)
        self.prepared()
        self.assertEqual(self.provider.last_grant, 'refresh_token')
        self.assertNotEqual(old[0], self.auth.read('example-tools', 'access'))
        self.assertNotEqual(old[1], self.auth.read('example-tools', 'refresh'))
        self.assertEqual(self.server.store.resolve('secret://example-auth'), 'Bearer ' + self.auth.read('example-tools', 'access'))
        self.provider.last_grant = None
        self.prepared()
        self.assertIsNone(self.provider.last_grant)

    def test_failed_refresh_preserves_store_and_runtime(self):
        self.connected()
        runtime = prepare(self.root, self.root)
        before = (runtime / 'mcp_settings.json').read_bytes()
        binding = self.auth.metadata('example-tools')
        binding['expires_at'] = 1
        self.auth.write_metadata('example-tools', binding)
        old = self.auth.read('example-tools', 'refresh')
        self.provider.fail_refresh = True
        with self.assertRaises(ValueError):
            self.prepared()
        self.assertEqual(old, self.auth.read('example-tools', 'refresh'))
        self.assertEqual(before, (runtime / 'mcp_settings.json').read_bytes())

    def test_changed_resource_and_missing_header_fail_closed(self):
        self.connected()
        binding = self.auth.metadata('example-tools')
        binding['resource'] += '/changed'
        self.auth.write_metadata('example-tools', binding)
        with self.assertRaises(ValueError):
            self.prepared()
        binding['resource'] = self.provider.origin + '/mcp'
        self.auth.write_metadata('example-tools', binding)
        self.server.store.delete('example-auth')
        with self.assertRaises(OSError):
            self.prepared()

    def test_ui_binding_overrides_old_gateway_oauth(self):
        self.connected()
        runtime = prepare(self.root, self.root)
        value = settings(runtime)
        value['mcpServers']['example-tools']['oauth'] = {'accessToken': self.provider.mint()}
        save(runtime / 'mcp_settings.json', value)
        self.assertNotIn('oauth', self.prepared())

    def test_gateway_status_snippets_are_url_only(self):
        self.assertEqual(gateway_connection(self.root)['status'], 'stopped')
        with patch('hub_ui.state', return_value={'port': 43210}):
            result = gateway_connection(self.root)
        self.assertEqual(result['status'], 'running')
        for client in ('Claude', 'Cursor'):
            entry = json.loads(result['snippets'][client])['mcpServers']['agentlocker-gateway']
            self.assertEqual(entry['url'], result['url'])
            self.assertFalse(set(entry) - {'type', 'url'})
        self.assertIn('url = ', result['snippets']['Codex'])
        with patch('hub_ui.state', return_value={'port': 'invalid'}):
            self.assertEqual(gateway_connection(self.root)['snippets'], {})
