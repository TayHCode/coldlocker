import contextlib
import http.client
import io
import json
import threading
import time
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit

import modes
from fake_oauth import Provider
import test_ui
from ui_auth import Authentication, entry_prefix


class AuthenticationTests(unittest.TestCase):
    stop = test_ui.UITests.stop
    request = test_ui.UITests.request
    post = test_ui.UITests.post

    def setUp(self):
        test_ui.UITests.setUp(self)
        self.provider = Provider()
        thread = threading.Thread(target=self.provider.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.provider.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(self.provider.shutdown)
        self.server.stage_import('mcp', {'name': 'example-tools', 'description': 'Example tools', 'transport': 'http',
                                       'url': self.provider.origin + '/mcp', 'headers': {'Authorization': 'secret://example-auth'}})
        self.auth = self.server.authentication
        self.transcripts = []

    def oauth(self):
        result = self.post('auth/oauth', {'name': 'example-tools'})
        self.transcripts.append(json.dumps(result))
        parts = urlsplit(result['url'])
        connection = http.client.HTTPConnection(parts.hostname, parts.port)
        connection.request('GET', '/approve?' + parts.query)
        response = connection.getresponse()
        callback = response.getheader('Location')
        response.read()
        connection.close()
        self.assertEqual(response.status, 302)
        return urlsplit(callback)

    def connected(self):
        callback = self.oauth()
        status, body = self.request(callback.path + '?' + callback.query, headers={'Sec-Fetch-Site': 'cross-site'})
        self.transcripts.append(body.decode())
        self.assertEqual(status, 200)
        return callback

    def test_bearer_masking_and_rejected_inputs(self):
        value = self.provider.mint()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = self.post('auth/bearer', {'name': 'example-tools', 'header': 'Authorization', 'scheme': 'bearer', 'value': value})
            self.assertEqual(result['status'], 'connected')
            self.assertEqual(self.server.store.resolve('secret://example-auth'), 'Bearer ' + value)
            self.assertEqual(self.request('/api/auth/bearer', {'name': 'missing', 'value': value})[0], 400)
        self.assertNotIn(value, output.getvalue())
        for bad in ('', value + '\r\nX: bad'):
            self.assertEqual(self.request('/api/auth/bearer', {'name': 'example-tools', 'header': 'Authorization', 'scheme': 'raw', 'value': bad})[0], 400)
        status, body = self.request('/api/status')
        self.assertNotIn(value.encode(), body)
        self.assertNotIn(value, json.dumps(result))
        self.assertEqual(self.request('/api/auth/bearer', {'name': 'example-tools', 'header': 'Injected', 'scheme': 'raw', 'value': value})[0], 400)
        self.post('auth/bearer', {'name': 'example-tools', 'header': 'Authorization', 'scheme': 'raw', 'value': value})
        self.assertEqual(self.server.store.resolve('secret://example-auth'), value)

    def test_pkce_refresh_rotation_expiry_and_no_leak(self):
        callback = self.connected()
        self.assertEqual(self.request(callback.path + '?' + callback.query)[0], 400)
        binding = self.auth.metadata('example-tools')
        binding['expires_at'] = time.time() - 1
        self.auth.write_metadata('example-tools', binding)
        self.assertEqual(self.auth.status(self.auth.server('example-tools'))['status'], 'expired')
        old_refresh = self.auth.read('example-tools', 'refresh')
        self.post('auth/refresh', {'name': 'example-tools'})
        self.assertNotEqual(old_refresh, self.auth.read('example-tools', 'refresh'))
        self.assertEqual(self.provider.last_grant, 'refresh_token')
        self.provider.rotate_refresh = False
        refresh = self.auth.read('example-tools', 'refresh')
        self.post('auth/refresh', {'name': 'example-tools'})
        self.assertEqual(refresh, self.auth.read('example-tools', 'refresh'))
        self.provider.fail_refresh = True
        self.assertEqual(self.request('/api/auth/refresh', {'name': 'example-tools'})[0], 400)
        self.assertEqual(self.auth.read('example-tools', 'refresh'), refresh)
        self.provider.fail_refresh = False
        self.connected()
        status, body = self.request('/api/status')
        self.transcripts.append(body.decode())
        self.assertIn('connected', body.decode())
        for path in self.server.store.secrets.iterdir():
            modes.check(self, path, 0o600)
        token_values = set(self.provider.values) - set(self.provider.clients)
        for value in token_values:
            for transcript in self.transcripts:
                self.assertNotIn(value, transcript)
            for path in self.root.rglob('*'):
                if path.is_file() and '.hub-store' not in path.parts and '.git' not in path.parts:
                    self.assertNotIn(value.encode(), path.read_bytes())

    def test_invalid_state_denial_expiry_and_pkce(self):
        callback = self.oauth()
        query = parse_qs(callback.query)
        self.assertEqual(self.request('/oauth/callback?' + urlencode({'state': 'invalid', 'code': query['code'][0]}))[0], 400)
        self.assertEqual(self.request('/oauth/callback?' + callback.query + '&state=duplicate')[0], 400)
        state = query['state'][0]
        self.auth.pending[state]['verifier'] = 'wrong-verifier'
        self.assertEqual(self.request('/oauth/callback?' + callback.query)[0], 400)
        callback = self.oauth()
        state = parse_qs(callback.query)['state'][0]
        self.auth.pending[state]['deadline'] -= 601
        self.assertEqual(self.request('/oauth/callback?' + callback.query)[0], 400)
        callback = self.oauth()
        state = parse_qs(callback.query)['state'][0]
        self.assertEqual(self.request('/oauth/callback?' + urlencode({'state': state, 'error': 'access_denied'}))[0], 400)
        self.assertFalse(self.server.store.has('example-auth'))

    def test_confidential_client_credentials_persist_and_refresh(self):
        for method in ('client_secret_basic', 'client_secret_post'):
            self.provider.auth_method = method
            self.connected()
            self.assertTrue(self.server.store.has(entry_prefix('example-tools') + '-client-secret'))
            self.post('auth/refresh', {'name': 'example-tools'})
        self.provider.registration = False
        self.assertEqual(self.request('/api/auth/oauth', {'name': 'example-tools'})[0], 400)

    def test_discover_reports_whether_oauth_is_offered(self):
        self.assertEqual(self.post('auth/discover', {'name': 'example-tools'}), {'oauth': True})
        self.provider.pkce = False
        self.assertEqual(self.post('auth/discover', {'name': 'example-tools'}), {'oauth': False})
        self.provider.pkce = True
        self.provider.bad_resource = True
        self.assertEqual(self.post('auth/discover', {'name': 'example-tools'}), {'oauth': False})
        self.assertEqual(self.request('/api/auth/discover', {'name': 'missing'})[0], 400)
        self.assertEqual(self.request('/api/auth/discover', {'name': 'example-tools'}, headers={'X-CSRF-Token': 'wrong'})[0], 403)

    def test_callback_host_and_post_csrf_remain_enforced(self):
        callback = self.oauth()
        self.assertEqual(self.request(callback.path + '?' + callback.query, headers={'Host': 'other.invalid'})[0], 403)
        value = self.provider.mint()
        self.assertEqual(self.request('/api/auth/bearer', {'name': 'example-tools', 'value': value}, headers={'X-CSRF-Token': 'wrong'})[0], 403)
        self.assertFalse(self.server.store.has('example-auth'))

    def test_discovery_challenge_metadata_validation_and_public_client(self):
        self.provider.challenge = True
        self.connected()
        self.provider.registration = False
        client_id = next(iter(self.provider.clients))
        result = self.post('auth/oauth', {'name': 'example-tools', 'client_id': client_id})
        self.assertEqual(parse_qs(urlsplit(result['url']).query)['client_id'], [client_id])
        for attribute in ('bad_issuer', 'bad_resource'):
            setattr(self.provider, attribute, True)
            self.assertEqual(self.request('/api/auth/oauth', {'name': 'example-tools', 'client_id': client_id})[0], 400)
            setattr(self.provider, attribute, False)
        self.provider.pkce = False
        self.assertEqual(self.request('/api/auth/oauth', {'name': 'example-tools', 'client_id': client_id})[0], 400)

    def test_oauth_binding_without_manifest_header_and_restart(self):
        self.server.stage_import('mcp', {'name': 'no-header', 'description': 'Example tools', 'transport': 'http',
                                       'url': self.provider.origin + '/mcp'})
        result = self.post('auth/oauth', {'name': 'no-header'})
        state = parse_qs(urlsplit(result['url']).query)['state'][0]
        flow = self.auth.pending[state]
        self.auth.save(flow, {'access_token': self.provider.mint(), 'token_type': 'bearer', 'expires_in': 10})
        restarted = Authentication(self.root, self.server.store)
        server = restarted.server('no-header')
        self.assertEqual(restarted.status(server)['status'], 'connected')
        binding = restarted.metadata('no-header')
        self.assertEqual(binding['headers']['Authorization'], entry_prefix('no-header') + '-header')
        self.assertTrue(self.server.store.resolve('secret://' + binding['headers']['Authorization']).startswith('Bearer '))

    def test_changed_resource_requires_reconnect_and_new_consent_clears_refresh(self):
        self.connected()
        server = self.auth.server('example-tools')
        server['url'] += '/changed'
        self.assertEqual(self.auth.status(server)['status'], 'needs-auth')
        result = self.post('auth/oauth', {'name': 'example-tools'})
        state = parse_qs(urlsplit(result['url']).query)['state'][0]
        self.auth.save(self.auth.pending[state], {'access_token': self.provider.mint(), 'token_type': 'bearer'})
        self.assertFalse(self.server.store.has(entry_prefix('example-tools') + '-refresh'))

    def test_malformed_provider_response_never_reaches_logs(self):
        self.connected()
        self.provider.malformed = True
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status, body = self.request('/api/auth/refresh', {'name': 'example-tools'})
        self.assertEqual(status, 400)
        self.assertEqual(output.getvalue(), '')
        for value in set(self.provider.values) - set(self.provider.clients):
            self.assertNotIn(value.encode(), body)
            self.assertNotIn(value, output.getvalue())
