import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

import connect
import safety_fixtures
import test_ui


REPO = Path(__file__).resolve().parents[1]


class ScanUITests(unittest.TestCase):
    stop = test_ui.UITests.stop
    request = test_ui.UITests.request
    post = test_ui.UITests.post
    def setUp(self):
        test_ui.UITests.setUp(self)
        self.home = self.fixtures / 'home'
        shutil.copytree(REPO / 'tests/fixtures/scan/linux', self.home)
        config = self.home / '.config/opencode/opencode.jsonc'
        text = config.read_text(encoding='utf-8').replace('// synthetic configuration', '').replace(',\n}', '\n}')
        config.with_suffix('.json').write_text(text, encoding='utf-8')
        config.unlink()
        self.server.scan_home = self.home
        self.server.scan_platform = 'linux'
        self.server.scan_env = {}
        self.server.scan_runner = lambda command: ''

    def test_scan_csrf_immutable_inputs_and_round_trip(self):
        before = {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        self.assertEqual(self.request('/api/scan', {}, {'X-CSRF-Token': 'wrong'})[0], 403)
        report = self.post('scan', {})
        for kind in ('skill', 'routine', 'mcp_server', 'plugin', 'connection'):
            self.assertTrue(any(i['kind'] == kind for i in report['items']), kind)
        ids = [i['id'] for i in report['items'] if i['importable']]
        result = self.post('scan/import', {'ticket': report['ticket'], 'ids': ids})
        self.assertEqual(set(result['imported']), set(ids))
        self.assertEqual(result['rejected'], [])
        after = self.post('scan', {})
        self.assertFalse(any(i['id'] in ids and i['importable'] for i in after['items']))
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(self.post_status()['check'], 'Passed')
        process = subprocess.run([sys.executable, '-c', 'import sys; from pathlib import Path; from checks import checker; sys.exit(checker.run(Path(sys.argv[1])))', str(self.root)], cwd=REPO, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)

    def test_crontab_routine_item_carries_runner_schedule_and_timezone(self):
        self.server.scan_env = {'TZ': 'America/Chicago'}
        self.server.scan_runner = lambda command: '0 9 * * 1 /usr/local/bin/weekly-report\n'
        report = self.post('scan', {})
        item = next(i for i in report['items'] if i['kind'] == 'routine' and i['source'] == '<system:crontab>')
        self.assertEqual((item['runner'], item['schedule'], item['timezone']),
                         ('cron', {'type': 'cron', 'expression': '0 9 * * 1'}, 'America/Chicago'))
        self.post('scan/import', {'ticket': report['ticket'], 'ids': [item['id']]})
        again = next(i for i in self.post('scan', {})['items'] if i['name'] == item['name'] and i['kind'] == 'routine')
        self.assertEqual((again['status'], again['runner'], again['timezone']), ('already_in_locker', 'cron', 'America/Chicago'))
        details = json.loads(self.request('/api/status')[1])['inventory']['routines']['details']
        self.assertEqual(details[item['name']], {'runner': 'cron', 'schedule': {'type': 'cron', 'expression': '0 9 * * 1'}, 'timezone': 'America/Chicago'})

    def test_wizard_keeps_connection_of_needs_review_server_unticked_until_server_approved(self):
        safety_fixtures.write(self.home, safety_fixtures.MALICIOUS['unknown-remote'][2])
        report = self.post('scan', {})
        server = next(i for i in report['items'] if i['kind'] == 'mcp_server' and i['name'] == 'unknown-remote')
        connection = next(i for i in report['items'] if i['kind'] == 'connection' and i['name'] == 'unknown-remote')
        self.assertEqual((server['status'], connection['status']), ('needs_review', 'needs_review'))
        self.assertFalse(connection['importable'])
        self.assertEqual(connection['review'], server['review'])
        ids = [i['id'] for i in report['items'] if i['importable']]
        result = self.post('scan/import', {'ticket': report['ticket'], 'ids': ids})
        self.assertNotIn(connection['id'], result['imported'])
        self.assertFalse((self.root / '.hub-store/catalog/connections/unknown-remote.json').exists())
        report = self.post('scan', {})
        server = next(i for i in report['items'] if i['kind'] == 'mcp_server' and i['name'] == 'unknown-remote')
        connection = next(i for i in report['items'] if i['kind'] == 'connection' and i['name'] == 'unknown-remote')
        result = self.post('scan/import', {'ticket': report['ticket'], 'ids': [server['id']], 'approved': [server['id']]})
        self.assertEqual(set(result['imported']), {server['id'], connection['id']})
        self.assertTrue((self.root / '.hub-store/catalog/connections/unknown-remote.json').is_file())

    def post_status(self):
        code, body = self.request('/api/status')
        self.assertEqual(code, 200)
        return json.loads(body)

    def test_scan_selection_expiry_replay_and_stale_content(self):
        report = self.post('scan', {})
        for item in report['items']:
            if not item['importable']:
                self.assertEqual(self.request('/api/scan/import', {'ticket': report['ticket'], 'ids': [item['id']]})[0], 400)
        for ids in ([], ['sc-unknown'], 'bad', [None]):
            self.assertEqual(self.request('/api/scan/import', {'ticket': report['ticket'], 'ids': ids})[0], 400)
        item = next(i for i in report['items'] if i['kind'] == 'skill' and i['importable'])
        source = self.home / item['source'].removeprefix('~/') / 'SKILL.md'
        source.write_text(source.read_text(encoding='utf-8') + '\nChanged after scan.\n', encoding='utf-8')
        result = self.post('scan/import', {'ticket': report['ticket'], 'ids': [item['id']]})
        self.assertEqual(result['imported'], [])
        self.assertEqual(self.request('/api/scan/import', {'ticket': report['ticket'], 'ids': [item['id']]})[0], 400)
        report = self.post('scan', {})
        self.server.jobs[report['ticket']]['time'] -= 901
        self.assertEqual(self.request('/api/scan/import', {'ticket': report['ticket'], 'ids': [item['id']]})[0], 400)

    def test_canaries_absent_from_all_ui_responses(self):
        report = self.post('scan', {})
        transcripts = [json.dumps(report)]
        transcripts.append(json.dumps(self.post('scan/import', {'ticket': report['ticket'], 'ids': [i['id'] for i in report['items'] if i['importable']]})))
        for route in ('/', '/app.js', '/style.css', '/api/status', '/favicon.ico'):
            code, body = self.request(route)
            self.assertIn(code, (200, 204))
            transcripts.append(body.decode('latin-1') if route == '/favicon.ico' else body.decode())
        transcripts.append(json.dumps(self.post('connect', {})))
        for secret in ('canary-secret-value-0001', 'canary-neutral-value-0002'):
            for text in transcripts:
                self.assertNotIn(secret, text)

    def test_connect_snippets_without_config_write(self):
        report = self.post('connect', {})
        self.assertEqual({i['agent'] for i in report['agents']}, set(connect.AGENTS))
        originals = {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        self.assertNotIn('ticket', report)
        self.assertTrue(all('agentlocker' in i['snippet'] for i in report['agents']))
        self.assertEqual(self.request('/api/connect/write', {'confirm': True})[0], 404)
        self.assertEqual(self.request('/api/connect/write', {}, {'X-CSRF-Token': 'wrong'})[0], 403)
        self.assertEqual({p: p.read_bytes() for p in originals}, originals)
        self.assertFalse(list(self.home.rglob('*.bak')))

    def test_static_xss_safe_and_node_smoke(self):
        js = '\n'.join(path.read_text(encoding='utf-8') for path in (REPO / 'ui').glob('*.js'))
        for unsafe in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write(', 'new-password'):
            self.assertNotIn(unsafe, js)
        self.assertIn('textContent', js)
        if not shutil.which('node'):
            self.skipTest('Node is unavailable; JS smoke test skipped')
        result = subprocess.run(['node', str(REPO / 'tests/ui_scan_smoke.cjs')], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_import_confirms_counts_and_shows_result(self):
        if not shutil.which('node'):
            self.skipTest('Node is unavailable; JS smoke test skipped')
        result = subprocess.run(['node', str(REPO / 'tests/ui_import_message_smoke.cjs')], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_approve_checkbox_gates_import(self):
        if not shutil.which('node'):
            self.skipTest('Node is unavailable; JS smoke test skipped')
        result = subprocess.run(['node', str(REPO / 'tests/ui_approve_smoke.cjs')], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_connect_unsupported_agent_keeps_copy_fallback(self):
        path = self.home / '.config/opencode/opencode.json'
        path.rename(path.with_suffix('.jsonc'))
        result = self.post('connect', {})
        item = next(i for i in result['agents'] if i['agent'] == 'opencode')
        self.assertIn('agentlocker', item['snippet'])
