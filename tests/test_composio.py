from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from checks import validators
from composio_connector import catalog, catalog_payload, connect, definition, run
from composio_fake import FakeComposio
from gateway_adapter import environment, redact_log
from gateway_render import placeholder, render
from hub import parser
from store import FileStore


ROOT = Path(__file__).resolve().parents[1]
URL = 'https://example.com/session/demo/mcp'
CATALOG = 'https://catalog.example.org/mcp'


class ComposioTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.scratch'
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.local = self.root / '.hub-store/catalog'
        self.manifest = {'schema_version': 1, 'name': 'agentlocker', 'license': 'MIT',
                         'skills': [], 'rules': [], 'mcp_servers': [], 'routines': []}
        (self.root / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')
        self.output = io.StringIO()
        self.config = self.root / '.hub-store/remote.json'
        self.config.parent.mkdir()
        self.config.write_text(json.dumps({'public_url': CATALOG, 'namespace_id': 'fixture-namespace'}))

    def connect(self, url=URL, name='demo'):
        with redirect_stdout(self.output):
            return connect(self.root, name, url)

    def test_catalog_cli_prints_oauth_registration_without_writes(self):
        result = subprocess.run([sys.executable, str(ROOT / 'hub.py'),
                                 'connect-composio', '--catalog', '--url', CATALOG],
                                cwd=self.root, env={**os.environ, 'HOME': str(self.root)},
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(CATALOG, result.stdout)
        self.assertNotIn('agentlocker.dev', result.stdout)
        self.assertIn('DCR_OAUTH', result.stdout)
        self.assertIn('CUSTOM_AGENTLOCKER', result.stdout)
        self.assertEqual(list((self.root / '.hub-store').iterdir()), [self.config])

    def test_catalog_preview_never_opens_store_or_network(self):
        with patch('composio_connector.FileStore', side_effect=AssertionError), \
                patch('urllib.request.build_opener', side_effect=AssertionError), \
                redirect_stdout(self.output):
            self.assertEqual(catalog(self.root), 0)
        payload = catalog_payload(self.root)
        self.assertEqual(payload['toolkit_config']['app_url'], CATALOG)
        self.assertEqual(payload['toolkit_config']['auth_schemes'], [{
            'mode': 'DCR_OAUTH', 'discovery_url':
            'https://catalog.example.org/.well-known/oauth-authorization-server'}])

    def test_catalog_command_uses_fixture_root(self):
        args = parser().parse_args(['connect-composio', '--catalog'])
        with patch('composio_connector.FileStore', side_effect=AssertionError), \
                patch('urllib.request.build_opener', side_effect=AssertionError), \
                redirect_stdout(self.output):
            self.assertEqual(run(args, self.root), 0)
        self.assertIn(CATALOG, self.output.getvalue())
        self.assertNotIn('agentlocker.dev', self.output.getvalue())

    def test_catalog_registration_uses_private_key_and_exact_payload(self):
        canary = 'canary-' + secrets.token_hex(24)
        FileStore(self.root).put('fixture-key', canary)
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with FakeComposio(canary) as fake, \
                patch('composio_connector.UPSERT_URL', fake.api_url), redirect_stdout(self.output):
            self.assertEqual(catalog(self.root, True, True, 'secret://fixture-key'), 0)
            self.assertEqual(fake.server.registrations, [catalog_payload(self.root)])
            self.assertEqual(fake.server.denied, 0)
        self.assertNotIn(canary, self.output.getvalue())
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_catalog_requires_explicit_confirmation_and_secret_reference(self):
        with patch('composio_connector.FileStore', side_effect=AssertionError), \
                patch('urllib.request.build_opener', side_effect=AssertionError):
            for kwargs in ({'write': True}, {'key_ref': 'raw-value'}, {'key_ref': 'secret://BAD'}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    catalog(self.root, **kwargs)
        for flags in (['--catalog', '--name', 'demo'], ['--catalog', '--url', URL],
                      ['--name', 'demo', '--url', URL, '--write'], ['--yes']):
            args = parser().parse_args(['connect-composio', *flags])
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                args.action(args)

    def test_catalog_errors_do_not_leak_or_retry_or_follow_redirects(self):
        canary = 'canary-' + secrets.token_hex(24)
        FileStore(self.root).put('composio-api-key', canary)
        with FakeComposio(canary) as fake, \
                patch('composio_connector.UPSERT_URL', fake.api_url), redirect_stdout(self.output):
            for status in (401, 409, 500, 302, 200):
                fake.server.api_status = status
                fake.server.api_response = {'diagnostic': canary}
                before = len(fake.server.registrations)
                with self.subTest(status=status), self.assertRaises(ValueError) as raised:
                    catalog(self.root, True, True)
                self.assertNotIn(canary, str(raised.exception))
                self.assertEqual(len(fake.server.registrations), before + 1)
            self.assertEqual(fake.server.methods, [])
        self.assertNotIn(canary, self.output.getvalue())

    def test_catalog_missing_key_fails_before_network(self):
        with patch('urllib.request.build_opener', side_effect=AssertionError), \
                redirect_stdout(self.output), self.assertRaises(ValueError):
            catalog(self.root, True, True)

    def test_registers_valid_manifest_and_rendered_http_header(self):
        self.assertEqual(self.connect(), 0)
        manifest = validators.read_local(self.root)
        entry = manifest['mcp_servers'][0]
        path = self.local / entry['path']
        self.assertEqual(entry['name'], 'composio-demo')
        self.assertEqual(entry['sha256'], validators.digest(path))
        validators.mcp(json.loads(path.read_text(encoding='utf-8')))
        rendered = render(self.root)['mcpServers']['composio-demo']
        self.assertEqual(rendered, {'type': 'streamable-http', 'url': URL,
                         'headers': {'x-api-key': placeholder('secret://composio-api-key')}})
        self.assertIn('python3 store.py set composio-api-key', self.output.getvalue())

    def test_idempotent_without_file_writes(self):
        self.connect()
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file()}
        self.connect()
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before})

    def test_conflicts_preserve_existing_files(self):
        self.connect()
        before = (self.local / 'manifest.json').read_bytes()
        path = self.local / 'mcp/servers/composio-demo.json'
        contents = path.read_bytes()
        with self.assertRaises(ValueError):
            self.connect(URL + '/other')
        self.assertEqual(contents, path.read_bytes())
        self.assertEqual(before, (self.local / 'manifest.json').read_bytes())
        path.write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.connect()
        self.assertEqual(path.read_text(encoding='utf-8'), '{}')

    def test_unindexed_file_and_foreign_owner_preserved(self):
        self.connect()
        path = self.local / 'manifest.json'
        value = json.loads(path.read_text(encoding='utf-8'))
        value['mcp_servers'][0]['owner'] = 'another'
        path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.connect()
        path.write_text(json.dumps(self.manifest), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.connect()

    def test_rejects_unsafe_urls_before_writing(self):
        for url in (URL + '?key=value', URL + '?', URL + '#', URL + '#fragment',
                    'https://user:password@example.com/mcp', 'https:///mcp', 'https://',
                    'ftp://example.com/mcp', 'http://example.com/mcp', URL + '\n',
                    'https://example.com:99999/mcp', 'https://example.com\\bad/mcp',
                    URL + '/${BAD}', 'https://example.com:0/mcp'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.connect(url)
        self.assertEqual(json.loads((self.root / 'manifest.json').read_text(encoding='utf-8')), self.manifest)
        self.assertFalse((self.local / 'manifest.json').exists())
        self.assertFalse((self.local / 'mcp').exists())

    def test_rejects_invalid_and_overlong_names(self):
        for name in ('../bad', 'UPPER', '', 'a' * 55):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.connect(name=name)

    def test_symlink_destination_preserved(self):
        target = self.root / 'elsewhere'
        target.mkdir()
        self.local.mkdir(parents=True)
        (self.local / 'mcp').symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.connect()
        self.assertFalse(list(target.iterdir()))

    def test_manifest_symlink_is_preserved(self):
        self.local.mkdir(parents=True)
        path = self.local / 'manifest.json'
        path.write_text(json.dumps(self.manifest), encoding='utf-8')
        target = self.root / 'original.json'
        path.rename(target)
        path.symlink_to(target)
        before = target.read_bytes()
        with self.assertRaises(ValueError):
            self.connect()
        self.assertEqual(target.read_bytes(), before)
        self.assertFalse((self.local / 'mcp').exists())

    def test_cli_registration_and_private_errors(self):
        args = parser().parse_args(['connect-composio', '--name', 'demo', '--url', URL])
        self.assertEqual(args.name, 'demo')
        canary = 'canary-' + secrets.token_hex(24)
        result = subprocess.run([sys.executable, str(ROOT / 'hub.py'), 'connect-composio',
                                 '--name', 'demo', '--url', URL + '?key=' + canary],
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(canary, result.stdout + result.stderr)

    def test_store_render_header_flow_and_no_leaks(self):
        canary = 'canary-' + secrets.token_hex(24)
        store = FileStore(self.root)
        store.put('composio-api-key', canary)
        with FakeComposio(canary) as fake:
            self.connect(fake.url)
            rendered = render(self.root)
            header = rendered['mcpServers']['composio-demo']['headers']['x-api-key']
            env = environment(rendered, store)
            resolved = env[header[2:-1]]
            request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with self.assertRaises(urllib.error.HTTPError) as denied:
                opener.open(urllib.request.Request(fake.url, json.dumps(request).encode()), timeout=5)
            self.assertEqual(denied.exception.code, 401)
            denied.exception.close()
            for method in ('initialize', 'tools/list', 'tools/call'):
                request.update(method=method, params={'arguments': {'a': 2, 'b': 3}})
                with opener.open(urllib.request.Request(fake.url, json.dumps(request).encode(),
                                 {'x-api-key': resolved}), timeout=5) as response:
                    result = json.load(response)
                self.assertIn('result', result)
            self.assertEqual(result['result']['content'][0]['text'], '5')
            self.assertEqual(fake.server.methods, ['initialize', 'tools/list', 'tools/call'])
        public = self.output.getvalue() + json.dumps(rendered)
        public += redact_log(store, 'upstream diagnostic: ' + canary)
        for path in self.root.rglob('*'):
            if path.is_file() and '.hub-store' not in path.parts:
                public += path.read_text(encoding='utf-8')
        self.assertNotIn(canary, public)
        self.assertIn('[REDACTED]', public)

    def test_missing_store_key_fails_before_request(self):
        self.connect()
        with self.assertRaises(FileNotFoundError):
            environment(render(self.root), FileStore(self.root))

    def test_catalog_override_ignores_invalid_config_and_follows_origin(self):
        self.config.write_text('invalid')
        url = 'https://library.example.net:8443/mcp'
        payload = catalog_payload(self.root, url)
        self.assertEqual(payload['toolkit_config']['app_url'], url)
        self.assertEqual(payload['toolkit_config']['auth_schemes'][0]['discovery_url'],
                         'https://library.example.net:8443/.well-known/oauth-authorization-server')
        args = parser().parse_args(['connect-composio', '--catalog', '--url', url])
        with patch('composio_connector.FileStore', side_effect=AssertionError), patch('urllib.request.build_opener', side_effect=AssertionError), redirect_stdout(self.output):
            self.assertEqual(run(args, self.root), 0)
        self.assertIn(url, self.output.getvalue())
        self.assertNotIn('agentlocker.dev', self.output.getvalue())

    def test_catalog_missing_url_fails_without_secrets_or_network(self):
        self.config.unlink()
        with patch('composio_connector.FileStore', side_effect=AssertionError), patch('urllib.request.build_opener', side_effect=AssertionError):
            with self.assertRaisesRegex(ValueError, 'public_url.*remote.json'):
                catalog(self.root)
        self.assertEqual(self.output.getvalue(), '')

    def test_catalog_rejects_invalid_override_before_secrets_or_network(self):
        for url in ('http://example.org/mcp', 'https://user:pass@example.org/mcp', CATALOG + '?', CATALOG + '#', CATALOG + '\\bad'):
            with self.subTest(url=url), patch('composio_connector.FileStore', side_effect=AssertionError), patch('urllib.request.build_opener', side_effect=AssertionError):
                with self.assertRaises(ValueError):
                    catalog(self.root, True, True, url=url)
