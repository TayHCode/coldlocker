import contextlib
import io
import unittest
import time
from unittest.mock import patch

from gateway_adapter import BindingRefresher, environment, prepare
import gateway
from gateway import settings
import modes
from portable import lock as acquire
import test_gateway_ui


class UnattendedRefreshTests(unittest.TestCase):
    setUp = test_gateway_ui.GatewayUITests.setUp
    stop = test_gateway_ui.GatewayUITests.stop
    request = test_gateway_ui.GatewayUITests.request
    post = test_gateway_ui.GatewayUITests.post
    oauth = test_gateway_ui.GatewayUITests.oauth
    connected = test_gateway_ui.GatewayUITests.connected

    def fixture(self, expires):
        self.connected()
        runtime = prepare(self.root, self.root)
        binding = self.auth.metadata('example-tools')
        binding['expires_at'] = time.time() + expires
        self.auth.write_metadata('example-tools', binding)
        self.lines = []
        self.refresher = BindingRefresher(self.root, self.server.store, 30, 60, self.lines.append)
        return settings(runtime)

    def test_expired_and_soon_expiring_rotate_without_user_action(self):
        for expiry in (-1, 20):
            value = self.fixture(expiry)
            old = self.auth.read('example-tools', 'access'), self.auth.read('example-tools', 'refresh')
            self.assertTrue(self.refresher.check(value, now=0))
            access = self.auth.read('example-tools', 'access')
            self.assertNotEqual(access, old[0])
            self.assertNotEqual(self.auth.read('example-tools', 'refresh'), old[1])
            self.assertEqual(self.server.store.resolve('secret://example-auth'), 'Bearer ' + access)
            self.assertIn('Bearer ' + access, environment(value, self.server.store).values())
            self.assertFalse(self.refresher.check(value, now=30))
            for secret in self.provider.values:
                self.assertNotIn(secret, ''.join(self.lines))

    def test_failure_preserves_all_credentials_and_backs_off(self):
        value = self.fixture(-1)
        before = {n: self.server.store.resolve('secret://' + n) for n in self.server.store.names()}
        self.provider.fail_refresh = True
        with patch.object(self.refresher.authentication, 'refresh', wraps=self.refresher.authentication.refresh) as refresh:
            for now, count in ((0, 1), (29, 1), (30, 2), (89, 2), (90, 3), (209, 3)):
                self.assertFalse(self.refresher.check(value, now=now))
                self.assertEqual(refresh.call_count, count)
        self.assertEqual(before, {n: self.server.store.resolve('secret://' + n) for n in self.server.store.names()})
        self.assertIn('retry in 120s', ''.join(self.lines))
        self.provider.fail_refresh = False
        self.assertTrue(self.refresher.check(value, now=210))
        for secret in self.provider.values:
            self.assertNotIn(secret, ''.join(self.lines))

    def test_same_header_does_not_restart_and_resource_change_is_rejected(self):
        value = self.fixture(-1)
        with patch.object(self.refresher.authentication, 'refresh'):
            self.assertFalse(self.refresher.check(value, now=0))
        value['mcpServers']['example-tools']['url'] += '/changed'
        with patch.object(self.refresher.authentication, 'refresh') as refresh:
            self.assertFalse(self.refresher.check(value, now=30))
            refresh.assert_not_called()
        self.assertIn('refresh failed', ''.join(self.lines))

    def test_retry_delay_is_capped(self):
        value = self.fixture(-1)
        with patch.object(self.refresher.authentication, 'refresh', side_effect=ValueError('private diagnostic')):
            for i in range(12):
                self.refresher.check(value, now=i * 1000)
        self.assertEqual(self.refresher.retries['example-tools'][0], 300)
        self.assertNotIn('private diagnostic', ''.join(self.lines))


    @patch('sys.platform', modes.GATEWAY_PLATFORM)
    def test_cli_is_opt_in(self):
        for flags, expected in (([], (False,)), (['--auto-refresh'], (True,))):
            with patch.object(gateway, 'ROOT', self.root), patch.object(gateway, 'start') as start, \
                    patch.object(gateway, 'servers_line', return_value='servers: unknown'):
                with patch('sys.argv', ['gateway.py', 'start', *flags]), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(gateway.main(), 0)
                self.assertEqual(start.call_args.args[2:], expected)

    def test_lifetime_lock_rejects_another_supervisor(self):
        runtime = prepare(self.root, self.root)
        with open(runtime / 'supervisor.lock', 'a', encoding='utf-8') as lock:
            acquire(lock, wait=False)
            with patch.object(gateway, 'ROOT', self.root), patch.object(gateway, 'run_supervisor') as run:
                with self.assertRaises(BlockingIOError):
                    gateway.supervise(43871, True)
                run.assert_not_called()
