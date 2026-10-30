import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from ui_auth import Authentication, DiscoveryMissing, authorization_metadata
import test_ui_auth
import ui_auth


class DiscoveryTests(unittest.TestCase):
    def test_issuer_paths_try_oauth_then_both_openid_locations(self):
        for path in ('', '/tenant', '/tenant/'):
            issuer = 'https://issuer.invalid' + path
            oauth = 'https://issuer.invalid/.well-known/oauth-authorization-server' + path
            inserted = 'https://issuer.invalid/.well-known/openid-configuration' + path
            appended = issuer.rstrip('/') + '/.well-known/openid-configuration'
            locations = list(dict.fromkeys((oauth, inserted, appended)))
            for index, location in enumerate(locations):
                with self.subTest(issuer=issuer, location=location):
                    values = [DiscoveryMissing('missing')] * index + [{'issuer': issuer}]
                    with patch('ui_auth.request_json', side_effect=values) as request:
                        self.assertEqual(authorization_metadata(issuer), {'issuer': issuer})
                        self.assertEqual([call.args[0] for call in request.call_args_list], locations[:index + 1])

    def test_discovery_does_not_hide_errors_or_issuer_mismatch(self):
        with patch('ui_auth.request_json', side_effect=ValueError('invalid')) as request:
            with self.assertRaises(ValueError):
                authorization_metadata('https://issuer.invalid')
            self.assertEqual(request.call_count, 1)
        with patch('ui_auth.request_json', side_effect=DiscoveryMissing('missing')):
            with self.assertRaises(DiscoveryMissing):
                authorization_metadata('https://issuer.invalid')
        resource = 'https://resource.invalid/mcp'
        document = {'resource': resource, 'authorization_servers': ['https://issuer.invalid']}
        for metadata in ({'issuer': 'https://wrong.invalid'}, {'issuer': 'https://issuer.invalid', 'code_challenge_methods_supported': ['plain']}):
            with patch('ui_auth.resource_challenge', return_value=(None, None)), patch('ui_auth.request_json', return_value=document), patch('ui_auth.authorization_metadata', return_value=metadata):
                with self.assertRaises(ValueError):
                    Authentication(None, None).discover(resource)


class WizardDiscoveryTests(unittest.TestCase):
    setUp = test_ui_auth.AuthenticationTests.setUp
    stop = test_ui_auth.AuthenticationTests.stop
    request = test_ui_auth.AuthenticationTests.request
    post = test_ui_auth.AuthenticationTests.post
    oauth = test_ui_auth.AuthenticationTests.oauth
    connected = test_ui_auth.AuthenticationTests.connected

    def test_identifies_client_to_providers_rejecting_python_user_agent(self):
        self.provider.required_user_agent = 'agentlocker/3'
        self.connected()
        self.assertEqual(self.auth.status(self.auth.server('example-tools'))['status'], 'connected')

    def test_supplied_public_client_overrides_advertised_registration(self):
        self.connected()
        original = set(self.provider.clients)
        client_id = next(iter(original))
        result = self.post('auth/oauth', {'name': 'example-tools', 'client_id': client_id})
        self.assertEqual(set(self.provider.clients), original)
        self.assertEqual(parse_qs(urlsplit(result['url']).query)['client_id'], [client_id])
        self.provider.registration = False
        self.assertEqual(self.request('/api/auth/oauth', {'name': 'example-tools'})[0], 400)
        result = self.post('auth/oauth', {'name': 'example-tools', 'client_id': client_id})
        self.assertEqual(parse_qs(urlsplit(result['url']).query)['client_id'], [client_id])
        self.assertEqual(self.request('/api/auth/oauth', {'name': 'example-tools', 'client_id': []})[0], 400)

    def test_challenge_scope_precedes_resource_scopes(self):
        self.provider.challenge = True
        self.provider.scope = 'read:tools'
        result = self.post('auth/oauth', {'name': 'example-tools'})
        self.assertEqual(parse_qs(urlsplit(result['url']).query)['scope'], ['read:tools'])
        state = parse_qs(urlsplit(result['url']).query)['state'][0]
        self.assertEqual(self.auth.pending[state]['scope'], 'read:tools')
        self.provider.scope = ''
        result = self.post('auth/oauth', {'name': 'example-tools'})
        self.assertNotIn('scope', parse_qs(urlsplit(result['url']).query))
        self.provider.scope = None
        result = self.post('auth/oauth', {'name': 'example-tools'})
        self.assertEqual(parse_qs(urlsplit(result['url']).query)['scope'], ['tools'])

    def test_root_resource_metadata_fallback_and_absent_scope(self):
        self.provider.root_metadata = True
        with patch('ui_auth.request_json', wraps=ui_auth.request_json) as request:
            self.connected()
        self.assertIn(self.provider.origin + '/.well-known/oauth-protected-resource', [call.args[0] for call in request.call_args_list])
        resource, metadata = self.auth.discover(self.provider.origin + '/mcp')
        resource.pop('scopes_supported')
        with patch.object(self.auth, 'discover', return_value=(resource, metadata)):
            result = self.post('auth/oauth', {'name': 'example-tools'})
        self.assertNotIn('scope', parse_qs(urlsplit(result['url']).query))

    def test_invalid_scope_is_rejected_without_token_storage(self):
        resource, metadata = self.auth.discover(self.provider.origin + '/mcp')
        for scopes in ('read', [42], ['read write'], [''], ['read\\write'], ['read"write']):
            with self.subTest(scopes=scopes), patch.object(self.auth, 'discover', return_value=({**resource, 'scopes_supported': scopes}, metadata)):
                self.assertEqual(self.request('/api/auth/oauth', {'name': 'example-tools'})[0], 400)
        self.assertEqual(self.server.store.names(), [])
