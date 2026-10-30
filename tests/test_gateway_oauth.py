import importlib.util
import json
from pathlib import Path
import secrets
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit

from gateway_adapter import redact_log


spec = importlib.util.spec_from_file_location('oauth_fixture', Path(__file__).resolve().parents[1] / 'examples/test-servers/oauth.py')
oauth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oauth)


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.fixture = oauth.OAuthFixture('http://' + oauth.LOOPBACK + ':45678')
        self.redirect = 'http://' + oauth.LOOPBACK + ':45679/oauth/callback'
        self.client = self.request('POST', '/register', {'redirect_uris': [self.redirect]})[2]
        self.verifier = secrets.token_urlsafe(48)

    def request(self, method, path, value=None, headers=None):
        body = json.dumps(value).encode() if value is not None else b''
        return self.fixture.request(method, path, headers or {}, body)

    def authorize(self):
        query = {'client_id': self.client['client_id'], 'redirect_uri': self.redirect,
                 'code_challenge_method': 'S256', 'code_challenge': oauth.challenge(self.verifier),
                 'state': secrets.token_urlsafe(24), 'response_type': 'code'}
        status, headers, _ = self.request('GET', '/authorize?' + urlencode(query))
        self.assertEqual(status, 302)
        callback = urlsplit(headers['Location'])
        self.assertEqual(callback.path, '/oauth/callback')
        params = parse_qs(callback.query)
        self.assertEqual(params['state'], [query['state']])
        return params['code'][0]

    def token(self, **grant):
        query = {'client_id': self.client['client_id'], 'client_secret': self.client['client_secret'], **grant}
        return self.fixture.request('POST', '/token', {}, urlencode(query).encode())

    def test_discovery_and_auth_challenge(self):
        status, headers, _ = self.request('POST', '/mcp', {'method': 'initialize'})
        self.assertEqual(status, 401)
        self.assertIn('resource_metadata=', headers['WWW-Authenticate'])
        resource = self.request('GET', '/.well-known/oauth-protected-resource')[2]
        self.assertEqual(resource['authorization_servers'], [self.fixture.origin])
        issuer = self.request('GET', '/.well-known/oauth-authorization-server')[2]
        self.assertEqual(issuer['code_challenge_methods_supported'], ['S256'])
        self.assertEqual(issuer['registration_endpoint'], self.fixture.origin + '/register')

    def test_registration_rejects_non_loopback_redirect(self):
        self.assertEqual(self.request('POST', '/register', {'redirect_uris': ['https://example.invalid/callback']})[0], 400)
        self.assertEqual(self.request('POST', '/register', {'redirect_uris': []})[0], 400)
        self.assertEqual(self.fixture.counts['registration'], 1)

    def test_pkce_client_binding_and_one_use_code(self):
        code = self.authorize()
        grant = {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': self.redirect}
        self.assertEqual(self.token(**grant, code_verifier='wrong')[0], 400)
        self.assertEqual(self.token(**grant, code_verifier=self.verifier, client_secret='wrong')[0], 401)
        self.assertEqual(self.token(**{**grant, 'redirect_uri': self.redirect + '/wrong'}, code_verifier=self.verifier)[0], 400)
        self.assertEqual(self.token(**grant, code_verifier=self.verifier)[0], 200)
        self.assertEqual(self.token(**grant, code_verifier=self.verifier)[0], 400)
        self.assertEqual(self.fixture.counts['pkce_verified'], 1)

    def test_refresh_rotation_and_two_client_calls_without_authorization(self):
        code = self.authorize()
        tokens = self.token(grant_type='authorization_code', code=code,
                            redirect_uri=self.redirect, code_verifier=self.verifier)[2]
        status, _, rotated = self.token(grant_type='refresh_token', refresh_token=tokens['refresh_token'])
        self.assertEqual(status, 200)
        self.assertNotEqual(tokens['access_token'], rotated['access_token'])
        self.assertNotEqual(tokens['refresh_token'], rotated['refresh_token'])
        self.assertEqual(self.token(grant_type='refresh_token', refresh_token=tokens['refresh_token'])[0], 400)
        headers = {'Authorization': 'Bearer ' + rotated['access_token']}
        for identity in (1, 2):
            listing = self.request('POST', '/mcp', {'id': identity, 'method': 'tools/list'}, headers)[2]
            self.assertEqual(listing['result']['tools'][0]['name'], 'add')
            result = self.request('POST', '/mcp', {'id': identity, 'method': 'tools/call',
                'params': {'name': 'add', 'arguments': {'a': 2, 'b': 3}}}, headers)[2]
            self.assertEqual(result['result']['content'][0]['text'], '5')
        self.assertEqual(self.fixture.counts['authorization'], 1)
        self.assertEqual(self.fixture.counts['tools_call'], 2)
        self.fixture.access[rotated['access_token']] = 0
        self.assertEqual(self.request('POST', '/mcp', {}, headers)[0], 401)


class OAuthLogTests(unittest.TestCase):
    def test_pending_values_masked_before_store_persistence(self):
        class EmptyStore:
            def redact(self, text):
                return text

            def names(self):
                return []
        value = secrets.token_urlsafe(32)
        lines = ['Authorization URL: https://example.invalid/authorize?client_id=' + value,
                 '"clientId": "' + value + '",', 'state: ' + value,
                 'access_token=' + value, 'codeVerifier: ' + value]
        for line in lines:
            with self.subTest(field=line.split(':')[0].split('=')[0]):
                self.assertNotIn(value, redact_log(EmptyStore(), line))
        safe = 'Successfully completed OAuth flow for server'
        self.assertEqual(redact_log(EmptyStore(), safe), safe)
