import json
from unittest.mock import patch
import unittest
from urllib.parse import parse_qs, urlencode

import gateway
import test_ui
import test_ui_auth


class DesignAssetsTests(unittest.TestCase):
    setUp = test_ui.UITests.setUp
    stop = test_ui.UITests.stop
    request = test_ui.UITests.request

    def test_plex_fonts_are_whitelisted_without_opening_the_directory(self):
        for font in ('sans-latin-400', 'sans-latin-500', 'sans-latin-600', 'mono-latin-400'):
            status, body = self.request('/fonts/ibm-plex-' + font + '-normal.woff2')
            self.assertEqual(status, 200)
            self.assertEqual(body[:4], b'wOF2')
        for path in ('/fonts/ibm-plex-OFL.txt', '/fonts/../manifest.json', '/fonts/ibm-plex-sans-latin-400-normal.woff2/extra'):
            self.assertEqual(self.request(path)[0], 404)

    def test_stop_refuses_other_roots_and_unmanaged_executor(self):
        with patch.object(gateway, 'stop') as stop:
            self.assertEqual(self.request('/api/gateway/stop', {})[0], 400)
            stop.assert_not_called()
        with patch.object(gateway, 'ROOT', self.root), patch('executor_connector.configuration', return_value={'backend': 'executor'}), patch.object(gateway, 'stop') as stop:
            self.assertEqual(self.request('/api/gateway/stop', {})[0], 400)
            stop.assert_not_called()

    def test_stop_uses_existing_identity_guard_and_only_fixture_runtime(self):
        with patch.object(gateway, 'ROOT', self.root), patch.object(gateway, 'stop') as stop:
            status, body = self.request('/api/gateway/stop', {})
        self.assertEqual(status, 200, body)
        stop.assert_called_once_with(self.root / '.hub-store/gateway')
        self.assertEqual(json.loads(body)['gateway']['status'], 'stopped')


class OAuthReturnTests(unittest.TestCase):
    setUp = test_ui_auth.AuthenticationTests.setUp
    stop = test_ui.UITests.stop
    request = test_ui.UITests.request
    post = test_ui.UITests.post
    oauth = test_ui_auth.AuthenticationTests.oauth

    def test_denial_is_sanitized_visible_in_setup_and_cannot_replay(self):
        flow = parse_qs(self.oauth().query)['state'][0]
        route = '/oauth/callback?' + urlencode({'state': flow, 'error': 'access_denied', 'error_description': 'private-provider-detail'})
        self.assertEqual(self.request(route)[0], 400)
        result = json.loads(self.request('/api/status')[1])
        item = result['auth'][0]
        self.assertEqual(item['oauth_result']['status'], 'denied')
        self.assertGreater(item['oauth_result']['at'], 0)
        self.assertEqual(item['state'], 'needs_signin')
        self.assertNotIn('private-provider-detail', json.dumps(result))
        self.assertFalse(self.server.store.has('example-auth'))
        self.assertEqual(self.request(route)[0], 400)
        self.post('auth/oauth', {'name': 'example-tools'})
        self.assertNotIn('oauth_result', json.loads(self.request('/api/status')[1])['auth'][0])

    def test_denial_preserves_existing_credential_and_token_save_clears_outcome(self):
        self.post('auth/bearer', {'name': 'example-tools', 'header': 'Authorization', 'scheme': 'bearer', 'value': 'fixture-only-value'})
        before = self.server.store.resolve('secret://example-auth')
        flow = parse_qs(self.oauth().query)['state'][0]
        self.assertEqual(self.request('/oauth/callback?' + urlencode({'state': flow, 'error': 'access_denied'}))[0], 400)
        item = json.loads(self.request('/api/status')[1])['auth'][0]
        self.assertEqual(item['state'], 'saved')
        self.assertEqual(item['oauth_result']['status'], 'denied')
        self.assertEqual(self.server.store.resolve('secret://example-auth'), before)
        self.post('auth/bearer', {'name': 'example-tools', 'header': 'Authorization', 'scheme': 'bearer', 'value': 'fixture-replacement-value'})
        self.assertNotIn('oauth_result', json.loads(self.request('/api/status')[1])['auth'][0])



class InputStateTests(unittest.TestCase):
    setUp = test_ui.UITests.setUp
    stop = test_ui.UITests.stop
    request = test_ui.UITests.request
    post = test_ui.UITests.post

    def test_enabled_source_is_explained_but_import_is_paused(self):
        path = self.fixtures / 'routines/routine.json'
        before = path.read_bytes()
        value = json.loads(path.read_text())
        value['enabled'] = True
        text = json.dumps(value)
        preview = self.post('routines/preview', {'text': text})
        self.assertTrue(preview['items'][0]['enabled_in_file'])
        self.assertEqual(preview['items'][0]['status'], 'Enabled in file → imports paused')
        self.post('import', {'ticket': preview['ticket']})
        imported = self.root / '.hub-store/catalog/routines' / value['id'] / 'routine.json'
        self.assertFalse(json.loads(imported.read_text())['enabled'])
        self.assertEqual(path.read_bytes(), before)

    def test_manual_missing_and_invalid_config_names_file_without_contents(self):
        status, body = self.request('/api/mcp/preview', {'root': str(self.fixtures), 'path': 'config.tom'})
        self.assertEqual(status, 400)
        self.assertIn('Did you mean config.toml?', body.decode())
        status, body = self.request('/api/mcp/preview', {'root': str(self.fixtures), 'path': 'absent/config.toml'})
        self.assertEqual(status, 400)
        self.assertIn('Could not read config.toml', body.decode())
        path = self.fixtures / 'mcp.json'
        path.write_text('{"private-value": malformed}')
        self.before[str(path)] = path.read_bytes()
        status, body = self.request('/api/mcp/preview', {'root': str(self.fixtures), 'path': 'mcp.json'})
        self.assertEqual(status, 400)
        self.assertIn('Could not parse mcp.json', body.decode())
        self.assertNotIn('private-value', body.decode())
        self.assertNotIn('malformed', body.decode())

    def test_stop_rejects_symlink_runtime_and_wrong_csrf(self):
        store = self.root / '.hub-store'
        store.mkdir()
        (store / 'gateway').symlink_to(self.fixtures, target_is_directory=True)
        with patch.object(gateway, 'ROOT', self.root), patch.object(gateway, 'stop') as stop:
            self.assertEqual(self.request('/api/gateway/stop', {})[0], 400)
            self.assertEqual(self.request('/api/gateway/stop', {}, {'X-CSRF-Token': 'invalid'})[0], 403)
            stop.assert_not_called()


class NavigationTests(unittest.TestCase):
    def test_navigation_and_theme_actions(self):
        import subprocess
        for script in ('ui_design_smoke.cjs', 'ui_connect_keyboard_smoke.cjs'):
            with self.subTest(script=script):
                result = subprocess.run(['node', 'tests/' + script], cwd=test_ui.REPO, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class RoutineScanStateTests(unittest.TestCase):
    setUp = test_ui.UITests.setUp
    stop = test_ui.UITests.stop

    def test_scanner_keeps_source_enabled_metadata_separate_from_payload(self):
        import scanner
        home = self.fixtures / 'scan-home'
        tasks = home / '.codex/automations/fixture'
        tasks.mkdir(parents=True)
        source = tasks / 'automation.toml'
        source.write_text('name = "Fixture routine"\nprompt = "Review fixture inputs"\nenabled = true\n[schedule]\ntype = "interval"\nevery_seconds = 3600\n')
        before = source.read_bytes()
        inventory = scanner.Inventory(home, (), 'linux', {}, lambda command: '', self.root)
        report = inventory.discover()
        item = next(item for item in report['items'] if item['kind'] == 'routine')
        self.assertTrue(item['enabled_in_file'])
        self.assertFalse(inventory.payloads[item['id']][0][0]['enabled'])
        self.assertEqual(source.read_bytes(), before)

    def test_unreadable_config_is_distinct_from_disabled_server(self):
        import scanner
        home = self.fixtures / 'scan-home'
        (home / '.cursor').mkdir(parents=True)
        (home / '.cursor/mcp.json').write_text('{broken-json}')
        (home / '.claude.json').write_text(json.dumps({'mcpServers': {'paused-server': {'command': 'example', 'enabled': False}}}))
        report = scanner.scan(home, (), 'linux', {}, lambda command: '', root=self.root)
        unreadable = next(item for item in report['items'] if item.get('source_error'))
        self.assertEqual(unreadable['source'], '~/.cursor/mcp.json')
        disabled = next(item for item in report['items'] if item['name'] == 'paused-server')
        self.assertFalse(disabled.get('source_error'))
        self.assertFalse(disabled['importable'])
