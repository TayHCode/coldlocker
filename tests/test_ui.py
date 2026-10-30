import http.client
import json
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

import modes
import hub_ui
import importers
from store import FileStore
from windows_text import windows_text_writes


REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / 'tests' / 'fixtures' / 'ui'


class UITests(unittest.TestCase):
    def setUp(self):
        scratch = REPO / '.scratch' / 'ui-tests'
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'locker'
        self.root.mkdir()
        self.local = self.root / '.hub-store/catalog'
        for name in ('LICENSE', '.gitignore'):
            shutil.copyfile(REPO / name, self.root / name)
        shutil.copytree(REPO / 'config', self.root / 'config')
        (self.root / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'name': 'agentlocker', 'license': 'MIT',
                                                           'skills': [], 'rules': [], 'mcp_servers': [], 'routines': []}), encoding='utf-8')
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        self.fixtures = Path(temporary.name) / 'inputs'
        shutil.copytree(FIXTURES, self.fixtures)
        self.before = {str(path): path.read_bytes() for path in self.fixtures.rglob('*') if path.is_file()}
        self.server = hub_ui.SetupServer(self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        for name, content in self.before.items():
            self.assertEqual(Path(name).read_bytes(), content)

    def request(self, route, data=None, headers=None, raw=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        self.addCleanup(connection.close)
        supplied = {'Host': '127.0.0.1:' + str(self.server.server_port)}
        if data is not None or raw is not None:
            supplied.update({'Content-Type': 'application/json', 'X-CSRF-Token': self.server.token})
        supplied.update(headers or {})
        body = raw if raw is not None else json.dumps(data).encode() if data is not None else None
        connection.request('POST' if body is not None else 'GET', route, body=body, headers=supplied)
        response = connection.getresponse()
        return response.status, response.read()

    def post(self, route, data):
        status, body = self.request('/api/' + route, data)
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def test_loopback_only_and_static_allowlist(self):
        with self.assertRaises(ValueError):
            hub_ui.SetupServer(self.root, host='0.0.0.0')
        for port in (-1, 65536):
            with self.assertRaises(ValueError):
                hub_ui.SetupServer(self.root, port=port)
        self.assertEqual(self.request('/')[0], 200)
        self.assertEqual(self.request('/app.js')[0], 200)
        self.assertEqual(self.request('/../manifest.json')[0], 404)
        self.assertEqual(self.request('/.hub-store/secrets/example')[0], 404)

    def test_shell_assets_and_security_headers(self):
        assets = {'/favicon.svg': 'image/svg+xml', '/favicon.ico': 'image/x-icon',
                  '/fonts/inter-latin-wght-normal.woff2': 'font/woff2',
                  '/fonts/geist-mono-latin-400-normal.woff2': 'font/woff2',
                  '/fonts/geist-mono-latin-500-normal.woff2': 'font/woff2',
                  '/api.js': 'text/javascript'}
        for font in ('sans-latin-400', 'sans-latin-500', 'sans-latin-600', 'mono-latin-400'):
            assets['/fonts/ibm-plex-' + font + '-normal.woff2'] = 'font/woff2'
        for screen in ('welcome', 'scan', 'signin', 'connect'):
            assets['/' + screen + '.js'] = 'text/javascript'
            assets['/' + screen + '.css'] = 'text/css'
        for route, mime in assets.items():
            with self.subTest(route=route):
                connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
                self.addCleanup(connection.close)
                connection.request('GET', route)
                response = connection.getresponse()
                body = response.read()
                self.assertIn(response.status, (200, 204))
                self.assertEqual(response.getheader('Content-Type').split(';')[0], mime)
                self.assertEqual(response.getheader('Cache-Control'), 'no-store')
                self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')
                self.assertIn("font-src 'self'", response.getheader('Content-Security-Policy'))
                if route.endswith('.woff2'):
                    self.assertEqual(body[:4], b'wOF2')
        for route in ('/fonts/inter-OFL.txt', '/fonts/../manifest.json', '/welcome.js/extra'):
            self.assertEqual(self.request(route)[0], 404)

    def test_six_destinations_share_four_screens_and_copy_only_connect(self):
        html = (REPO / 'ui/index.html').read_text(encoding='utf-8')
        names = ['welcome', 'scan', 'signin', 'connect']
        self.assertEqual(re.findall(r'<section data-screen="([a-z]+)"', html), names)
        self.assertEqual(re.findall(r'<li data-nav="([a-z]+)"', html), ['welcome', 'skills', 'routines', 'mcp', 'signin', 'connect'])
        self.assertEqual(re.findall(r'data-group="([a-z_]+)"', html)[:3], ['skill', 'routine', 'mcp_server'])
        self.assertNotRegex(html, r'Step [1-6] of [46]')
        self.assertIn('Imports and credentials stay in the local', html)
        self.assertIn('Provider sign-in and tool calls contact their servers.', html)
        self.assertIn('<span class="mono">.hub-store</span>', html)
        self.assertNotIn('connect-all', html)
        js = '\n'.join(path.read_text(encoding='utf-8') for path in (REPO / 'ui').glob('*.js'))
        self.assertNotIn('connect/write', js)
        self.assertIn('clipboard.writeText', js)
        self.assertIn('Setup does not edit client files.', html)
        self.assertNotIn("won't edit it for you", html)
        self.assertNotIn('Or run', html)

    def test_local_runtime_assets_and_restricted_red(self):
        for path in (REPO / 'ui').iterdir():
            if path.suffix in ('.html', '.css', '.js'):
                self.assertNotRegex(path.read_text(encoding='utf-8'), r'https?://(?!127\.0\.0\.1(?=[:/]))', str(path))
        css = '\n'.join(path.read_text(encoding='utf-8') for path in (REPO / 'ui').glob('*.css'))
        for selector, declarations in re.findall(r'([^{}]+)\{([^{}]*)\}', css):
            if 'var(--red)' in declarations:
                self.assertTrue(all(re.search(r'\.(live|err|first-call)\b', part)
                                    for part in selector.split(',')), selector)
        self.assertIn('--red:#ff6369', css)
        self.assertIn('--red:#c4272d', css)
        self.assertIn('@media(prefers-color-scheme:light)', css)
        self.assertIn(':root[data-theme="light"]', css)
        self.assertIn('@media(max-width:600px)', css)

    def test_host_origin_csrf_and_body_cap(self):
        for headers in ({'Host': 'example.invalid'}, {'Host': 'localhost:' + str(self.server.server_port)},
                        {'Origin': 'https://example.invalid'}, {'Sec-Fetch-Site': 'cross-site'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request('/api/session', headers=headers)[0], 403)
        self.assertEqual(self.request('/api/import', {}, {'X-CSRF-Token': 'wrong'})[0], 403)
        self.assertEqual(self.request('/api/import', {}, {'Origin': 'http://127.0.0.1:1'})[0], 403)
        self.assertEqual(self.request('/api/import', raw=b'', headers={'Content-Length': str(importers.LIMIT + 1)})[0], 413)
        self.assertEqual(self.request('/api/import', raw=b'{}', headers={'Content-Type': 'text/plain'})[0], 400)
        self.assertEqual(self.request('/api/import', raw=b'{bad')[0], 400)
        status, body = self.request('/api/session')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['token'], self.server.token)

    def test_status_and_exact_connection_snippets(self):
        status, body = self.request('/api/status')
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result['check'], 'Passed')
        self.assertEqual(result['counts']['skills'], 0)
        self.assertEqual(json.loads(result['snippets']['Claude'])['mcpServers']['agentlocker']['args'], [str(self.root / 'mcp_endpoint.py')])
        self.assertIn('[mcp_servers.agentlocker]', result['snippets']['Codex'])

    def test_skill_and_routine_round_trip_and_conflict(self):
        preview = self.post('skills/preview', {'root': str(self.fixtures)})
        self.assertEqual(len(preview['items']), 1)
        result = self.post('import', {'ticket': preview['ticket'], 'selected': ['skills/sample-skill']})
        self.assertEqual(result['imported'], 1)
        preview = self.post('routines/preview', {'root': str(self.fixtures), 'path': 'routines/routine.json'})
        self.post('import', {'ticket': preview['ticket']})
        routine = json.loads((self.local / 'routines/fixture-review/routine.json').read_text(encoding='utf-8'))
        self.assertIs(routine['enabled'], False)
        self.assertEqual((self.local / 'routines/fixture-review/prompt.md').read_bytes(), (self.fixtures / 'routines/prompt.md').read_bytes())
        before = (self.local / 'manifest.json').read_bytes()
        preview = self.post('skills/preview', {'root': str(self.fixtures)})
        self.assertEqual(self.request('/api/import', {'ticket': preview['ticket'], 'selected': ['skills/sample-skill']})[0], 400)
        self.assertEqual((self.local / 'manifest.json').read_bytes(), before)

    def test_routine_import_keeps_prompt_bytes_with_windows_text_writes(self):
        with windows_text_writes():
            preview = self.post('routines/preview', {'root': str(self.fixtures), 'path': 'routines/routine.json'})
            self.post('import', {'ticket': preview['ticket']})
        imported = self.local / 'routines/fixture-review'
        self.assertEqual((imported / 'prompt.md').read_bytes(), (self.fixtures / 'routines/prompt.md').read_bytes())
        self.assertNotIn(b'\r', (imported / 'routine.json').read_bytes())
        self.assertNotIn(b'\r', (self.local / 'manifest.json').read_bytes())

    def test_pasted_routine_and_opaque_preview_expiration(self):
        text = (self.fixtures / 'routines/routine.json').read_text(encoding='utf-8')
        preview = self.post('routines/preview', {'text': text})
        self.server.jobs[preview['ticket']]['time'] -= 901
        self.assertEqual(self.request('/api/import', {'ticket': preview['ticket']})[0], 400)
        preview = self.post('routines/preview', {'text': text})
        self.post('import', {'ticket': preview['ticket']})
        self.assertTrue((self.local / 'routines/fixture-review/prompt.md').is_file())
        self.assertEqual(self.request('/api/import', {'ticket': preview['ticket']})[0], 400)

    def test_mcp_all_formats_round_trip_and_no_leak(self):
        transcripts = []
        secret_values = []
        for relative in ('claude/.mcp.json', 'claude/.claude.json', 'codex/config.toml', 'cursor/mcp.json'):
            original = self.fixtures / relative
            _, credentials = importers.parse_mcp(original.read_text(encoding='utf-8'), original.name)
            secret_values.extend(credentials.values())
            preview = self.post('mcp/preview', {'root': str(self.fixtures), 'path': relative})
            transcripts.append(json.dumps(preview))
            transcripts.append(json.dumps(self.post('import', {'ticket': preview['ticket'], 'approved': True})))
        status, body = self.request('/api/status')
        self.assertEqual(status, 200)
        transcripts.append(body.decode())
        self.assertEqual(json.loads(body)['counts']['mcp_servers'], 6)
        self.assertEqual(json.loads(body)['check'], 'Passed')
        self.assertIn('Credentials stored', body.decode())
        store = FileStore(self.root)
        self.assertEqual(len(store.names()), len(secret_values))
        self.assertEqual(sorted(store.resolve('secret://' + name) for name in store.names()), sorted(secret_values))
        modes.check(self, store.path, 0o700)
        for path in store.secrets.iterdir():
            modes.check(self, path, 0o600)
        files = [path for path in self.root.rglob('*') if path.is_file() and ('.hub-store' not in path.parts or 'catalog' in path.parts) and '.git' not in path.parts]
        for secret in secret_values:
            for transcript in transcripts:
                self.assertNotIn(secret, transcript)
            for path in files:
                self.assertNotIn(secret.encode(), path.read_bytes())
        tracked = subprocess.run(['git', 'status', '--short', '--untracked-files=all'], cwd=self.root, capture_output=True, text=True, check=True, encoding='utf-8')
        self.assertNotIn('.hub-store', tracked.stdout)

    def test_paths_invalid_input_and_symlink_destinations(self):
        for path in ('../.mcp.json', 'routines/prompt.md', 'missing/mcp.json'):
            self.assertEqual(self.request('/api/mcp/preview', {'root': str(self.fixtures), 'path': path})[0], 400)
        (self.fixtures / 'alias').symlink_to(self.fixtures / 'claude', target_is_directory=True)
        self.assertEqual(self.request('/api/mcp/preview', {'root': str(self.fixtures), 'path': 'alias/.mcp.json'})[0], 400)
        self.local.mkdir(parents=True)
        (self.local / 'skills').symlink_to(self.fixtures / 'skills', target_is_directory=True)
        preview = self.post('skills/preview', {'root': str(self.fixtures)})
        self.assertEqual(self.request('/api/import', {'ticket': preview['ticket'], 'selected': ['skills/sample-skill']})[0], 400)

    def test_malicious_secret_never_echoed_on_error(self):
        secret = 'sk' + '-' + 'x' * 35
        path = self.fixtures / '.mcp.json'
        path.write_text(json.dumps({'mcpServers': {'bad': {'command': 'example', 'env': {'TOKEN': secret}, 'args': [secret]}}}), encoding='utf-8')
        status, body = self.request('/api/mcp/preview', {'root': str(self.fixtures), 'path': '.mcp.json'})
        self.assertEqual(status, 400)
        self.assertNotIn(secret.encode(), body)
        self.assertFalse((self.root / '.hub-store').exists())
