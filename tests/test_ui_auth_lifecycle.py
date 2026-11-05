import http.client
import json
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import test_ui_auth


class ConsentLifecycleTests(unittest.TestCase):
    setUp = test_ui_auth.AuthenticationTests.setUp
    stop = test_ui_auth.AuthenticationTests.stop
    request = test_ui_auth.AuthenticationTests.request
    post = test_ui_auth.AuthenticationTests.post

    def consent(self, name='example-tools'):
        parts = urlsplit(self.post('auth/oauth', {'name': name})['url'])
        connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request('GET', '/approve?' + parts.query)
        response = connection.getresponse()
        callback = urlsplit(response.getheader('Location'))
        response.read()
        self.assertEqual(response.status, 302)
        return callback.query, parse_qs(callback.query)['state'][0]

    def snapshot(self):
        return {name: self.server.store.resolve('secret://' + name) for name in self.server.store.names()}

    def item(self, name='example-tools'):
        status, body = self.request('/api/status')
        self.assertEqual(status, 200)
        return next(item for item in json.loads(body)['auth'] if item['name'] == name)

    def test_late_sibling_denial_and_replay_preserve_saved_result(self):
        _, old_state = self.consent()
        query, _ = self.consent()
        self.assertEqual(self.request('/oauth/callback?' + query)[0], 200)
        saved = self.item()['oauth_result']
        credentials = self.snapshot()
        denial = urlencode({'state': old_state, 'error': 'access_denied'})
        for callback in (denial, denial, query):
            self.assertEqual(self.request('/oauth/callback?' + callback)[0], 400)
            self.assertEqual(self.item()['oauth_result'], saved)
            self.assertEqual(self.item()['state'], 'saved')
            self.assertEqual(self.snapshot(), credentials)

    def test_abandoned_sibling_expiry_preserves_saved_result(self):
        old_query, old_state = self.consent()
        old_flow = self.auth.pending[old_state]
        query, _ = self.consent()
        self.assertEqual(self.request('/oauth/callback?' + query)[0], 200)
        saved = self.item()['oauth_result']
        credentials = self.snapshot()
        old_flow['deadline'] -= 601
        self.assertEqual(self.item()['oauth_result'], saved)
        self.assertEqual(self.request('/oauth/callback?' + old_query)[0], 400)
        self.assertEqual(self.item()['oauth_result'], saved)
        self.assertEqual(self.snapshot(), credentials)

    def test_new_consent_rejects_older_approval_before_exchange(self):
        old_query, _ = self.consent()
        query, _ = self.consent()
        with patch.object(self.auth, 'exchange', wraps=self.auth.exchange) as exchange:
            self.assertEqual(self.request('/oauth/callback?' + old_query)[0], 400)
            exchange.assert_not_called()
        self.assertIsNone(self.auth.outcome('example-tools'))
        self.assertFalse(self.server.store.has('example-auth'))
        self.assertEqual(self.request('/oauth/callback?' + query)[0], 200)

    def test_manual_save_invalidates_pending_callbacks_and_expiry(self):
        for callback_kind in ('approval', 'denial', 'expiry'):
            with self.subTest(callback=callback_kind):
                query, state = self.consent()
                flow = self.auth.pending[state]
                self.post('auth/bearer', {'name': 'example-tools', 'header': 'Authorization',
                                        'scheme': 'bearer', 'value': self.provider.mint()})
                credentials = self.snapshot()
                if callback_kind == 'denial':
                    query = urlencode({'state': state, 'error': 'access_denied'})
                elif callback_kind == 'expiry':
                    flow['deadline'] -= 601
                self.assertIsNone(self.auth.outcome('example-tools'))
                self.assertEqual(self.request('/oauth/callback?' + query)[0], 400)
                self.assertNotIn('oauth_result', self.item())
                self.assertEqual(self.snapshot(), credentials)

    def test_current_expiry_is_visible_single_use_and_preserves_credentials(self):
        self.post('auth/bearer', {'name': 'example-tools', 'header': 'Authorization',
                                'scheme': 'bearer', 'value': self.provider.mint()})
        for via_status in (False, True):
            with self.subTest(via_status=via_status):
                query, state = self.consent()
                credentials = self.snapshot()
                self.auth.pending[state]['deadline'] -= 601
                if via_status:
                    self.assertEqual(self.item()['oauth_result']['status'], 'expired')
                self.assertEqual(self.request('/oauth/callback?' + query)[0], 400)
                expired = self.item()['oauth_result']
                self.assertEqual(expired['status'], 'expired')
                self.assertEqual(self.request('/oauth/callback?' + query)[0], 400)
                self.assertEqual(self.item()['oauth_result'], expired)
                self.assertEqual(self.snapshot(), credentials)

    def test_superseding_one_server_leaves_other_consent_usable(self):
        self.server.stage_import('mcp', {'name': 'other-tools', 'description': 'Other tools',
                                       'transport': 'http', 'url': self.provider.origin + '/mcp'})
        query, state = self.consent('other-tools')
        self.consent()
        self.consent()
        self.post('auth/bearer', {'name': 'example-tools', 'header': 'Authorization',
                                'scheme': 'bearer', 'value': self.provider.mint()})
        self.assertIn(state, self.auth.pending)
        self.assertEqual(self.request('/oauth/callback?' + query)[0], 200)
        saved = self.item('other-tools')['oauth_result']
        self.assertEqual(saved['status'], 'saved')
        self.consent()
        self.assertEqual(self.item('other-tools')['oauth_result'], saved)

    def test_rejected_replacements_leave_current_consent_usable(self):
        query, state = self.consent()
        self.assertEqual(self.request('/api/auth/bearer', {'name': 'example-tools', 'header': 'Authorization',
                                                        'scheme': 'bearer', 'value': ''})[0], 400)
        self.provider.pkce = False
        self.assertEqual(self.request('/api/auth/oauth', {'name': 'example-tools'})[0], 400)
        self.provider.pkce = True
        self.assertIn(state, self.auth.pending)
        self.assertEqual(self.request('/oauth/callback?' + query)[0], 200)

    def test_pending_limit_still_applies_across_servers_and_allows_replacement(self):
        resource, metadata = self.auth.discover(self.provider.origin + '/mcp')
        with patch.object(self.auth, 'server', side_effect=lambda name: {'name': name, 'url': resource['resource']}), \
                patch.object(self.auth, 'discover', return_value=(resource, metadata)):
            for index in range(32):
                self.auth.start('fixture-' + str(index), self.server.origin + '/oauth/callback', 'fixture-client')
            self.auth.start('fixture-0', self.server.origin + '/oauth/callback', 'fixture-client')
            self.assertEqual(len(self.auth.pending), 32)
            pending = dict(self.auth.pending)
            with self.assertRaisesRegex(ValueError, 'too many authorizations'):
                self.auth.start('fixture-extra', self.server.origin + '/oauth/callback', 'fixture-client')
            self.assertEqual(self.auth.pending, pending)
            next(iter(self.auth.pending.values()))['deadline'] -= 601
            self.auth.start('fixture-extra', self.server.origin + '/oauth/callback', 'fixture-client')
            self.assertEqual(len(self.auth.pending), 32)

    def run_blocked_callback(self, query, fail=False):
        entered, release = threading.Event(), threading.Event()
        errors = []
        exchange = self.auth.exchange

        def blocked(flow, grant):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('fixture callback was not released')
            if fail:
                raise ValueError('fixture exchange failed')
            return exchange(flow, grant)

        def callback():
            try:
                self.auth.callback(query)
            except Exception as error:
                errors.append(error)

        patched = patch.object(self.auth, 'exchange', side_effect=blocked)
        patched.start()
        self.addCleanup(patched.stop)
        thread = threading.Thread(target=callback, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 6)
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(5))
        return release, thread, errors, patched

    def test_inflight_exchange_cannot_overwrite_manual_save_or_republish_failure(self):
        for fail in (False, True):
            with self.subTest(exchange_fails=fail):
                query, _ = self.consent()
                release, thread, errors, patched = self.run_blocked_callback(query, fail)
                self.post('auth/bearer', {'name': 'example-tools', 'header': 'Authorization',
                                        'scheme': 'bearer', 'value': self.provider.mint()})
                credentials = self.snapshot()
                release.set()
                thread.join(5)
                patched.stop()
                self.assertFalse(thread.is_alive())
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], ValueError)
                self.assertEqual(self.snapshot(), credentials)
                self.assertNotIn('oauth_result', self.item())

    def test_inflight_exchange_cannot_override_new_consent_result(self):
        for fail in (False, True):
            with self.subTest(exchange_fails=fail):
                old_query, _ = self.consent()
                release, thread, errors, patched = self.run_blocked_callback(old_query, fail)
                query, _ = self.consent()
                patched.stop()
                self.assertEqual(self.request('/oauth/callback?' + query)[0], 200)
                saved = self.item()['oauth_result']
                credentials = self.snapshot()
                release.set()
                thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], ValueError)
                self.assertEqual(self.snapshot(), credentials)
                self.assertEqual(self.item()['oauth_result'], saved)

    def test_replayed_inflight_callback_cannot_consume_or_finish_current_attempt(self):
        query, _ = self.consent()
        release, thread, errors, patched = self.run_blocked_callback(query)
        self.assertEqual(self.request('/oauth/callback?' + query)[0], 400)
        self.assertIsNone(self.auth.outcome('example-tools'))
        self.assertFalse(self.server.store.has('example-auth'))
        release.set()
        thread.join(5)
        patched.stop()
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.item()['oauth_result']['status'], 'saved')
