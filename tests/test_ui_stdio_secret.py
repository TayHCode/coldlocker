import json
from pathlib import Path
import subprocess
import unittest

import safety_fixtures as fx
import test_ui


class StdioSecretTests(unittest.TestCase):
    stop = test_ui.UITests.stop
    request = test_ui.UITests.request
    post = test_ui.UITests.post

    def setUp(self):
        test_ui.UITests.setUp(self)
        self.home = self.fixtures / 'home'
        self.copied = 'copied-fixture-value'
        self.typed = 'typed-fixture-value'
        fx.write(self.home, fx.servers('.cursor/mcp.json', {
            'fx-needs-secret': {'command': 'python3', 'args': ['-c', 'pass'], 'env': {'FX_API_KEY': self.copied}}}))
        self.server.scan_home = self.home
        self.server.scan_platform = 'linux'
        self.server.scan_env = {}
        self.server.scan_runner = lambda command: ''
        report = self.post('scan', {})
        ids = [item['id'] for item in report['items'] if item['kind'] == 'mcp_server' and item['name'] == 'fx-needs-secret']
        self.assertEqual(self.post('scan/import', {'ticket': report['ticket'], 'ids': ids})['imported'], ids)

    def row(self):
        status, body = self.request('/api/status')
        self.assertEqual(status, 200)
        self.assertNotIn(self.copied.encode(), body)
        self.assertNotIn(self.typed.encode(), body)
        return next(row for row in json.loads(body)['auth'] if row['name'] == 'fx-needs-secret')

    def test_row_names_variables_and_where_the_value_came_from(self):
        row = self.row()
        self.assertEqual(row['state'], 'saved')
        self.assertEqual(row['secrets'], ['FX_API_KEY'])
        self.assertEqual(row['copied_from'], 'Cursor')

    def test_set_replaces_stored_value_without_returning_it(self):
        ref = self.server.store.names()[0]
        result = self.post('auth/secret', {'name': 'fx-needs-secret', 'key': 'FX_API_KEY', 'value': self.typed})
        self.assertNotIn(self.typed, json.dumps(result))
        self.assertEqual(self.server.store.resolve('secret://' + ref), self.typed)
        row = self.row()
        self.assertEqual(row['state'], 'saved')
        self.assertIsNone(row['copied_from'])

    def test_set_fills_a_missing_value(self):
        ref = next(name for name in self.server.store.names() if name.startswith('scan-'))
        self.server.store.delete(ref)
        self.assertEqual(self.row()['state'], 'needs_signin')
        self.post('auth/secret', {'name': 'fx-needs-secret', 'key': 'FX_API_KEY', 'value': self.typed})
        self.assertEqual(self.server.store.resolve('secret://' + ref), self.typed)
        self.assertEqual(self.row()['state'], 'saved')

    def test_remove_deletes_only_that_secret_and_returns_to_needs_signin(self):
        ref = next(name for name in self.server.store.names() if name.startswith('scan-'))
        self.server.store.put('unrelated-fixture', self.typed)
        result = self.post('auth/secret/remove', {'name': 'fx-needs-secret', 'key': 'FX_API_KEY'})
        self.assertNotIn(self.copied, json.dumps(result))
        self.assertFalse(self.server.store.has(ref))
        self.assertTrue(self.server.store.has('unrelated-fixture'))
        row = self.row()
        self.assertEqual((row['state'], row['secrets']), ('needs_signin', ['FX_API_KEY']))
        self.post('auth/secret/remove', {'name': 'fx-needs-secret', 'key': 'FX_API_KEY'})
        self.post('auth/secret', {'name': 'fx-needs-secret', 'key': 'FX_API_KEY', 'value': self.typed})
        self.assertEqual(self.row()['state'], 'saved')

    def test_remove_rejects_unknown_server_key_and_untrusted_requests(self):
        ref = next(name for name in self.server.store.names() if name.startswith('scan-'))
        for data in ({'name': 'missing', 'key': 'FX_API_KEY'}, {'name': 'fx-needs-secret', 'key': 'OTHER'}, {'name': 'fx-needs-secret'}):
            self.assertEqual(self.request('/api/auth/secret/remove', data)[0], 400)
        data = {'name': 'fx-needs-secret', 'key': 'FX_API_KEY'}
        self.assertEqual(self.request('/api/auth/secret/remove', data, {'X-CSRF-Token': 'wrong'})[0], 403)
        self.assertEqual(self.request('/api/auth/secret/remove', data, {'Origin': 'http://example.invalid'})[0], 403)
        self.assertEqual(self.request('/api/auth/secret/remove', data, {'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertTrue(self.server.store.has(ref))

    def test_rejects_unknown_server_key_and_bad_values(self):
        for data in ({'name': 'missing', 'key': 'FX_API_KEY'}, {'name': 'fx-needs-secret', 'key': 'OTHER'},
                     {'name': 'fx-needs-secret', 'key': 'FX_API_KEY', 'value': ''},
                     {'name': 'fx-needs-secret', 'key': 'FX_API_KEY', 'value': self.typed + '\r\nX: bad'}):
            data.setdefault('value', self.typed)
            self.assertEqual(self.request('/api/auth/secret', data)[0], 400)
        self.assertNotEqual(self.server.store.resolve('secret://' + next(n for n in self.server.store.names() if n.startswith('scan-'))), self.typed)

    def test_sign_in_view_offers_set_and_replace_for_stdio(self):
        result = subprocess.run(['node', 'tests/ui_stdio_secret_smoke.cjs'], cwd=test_ui.REPO, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
