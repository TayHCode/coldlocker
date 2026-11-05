import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from remote_settings import catalog_url, oauth_discovery_url


URL = 'https://catalog.example.org/mcp'


class RemoteSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / '.hub-store/remote.json'

    def configure(self, value):
        self.config.parent.mkdir(exist_ok=True)
        self.config.write_text(json.dumps(value), encoding='utf-8')

    def test_reads_public_url_without_store_or_mutation(self):
        self.configure({'public_url': URL, 'account_id': 'fixture', 'namespace_id': 'fixture',
                        'api_token_ref': 'secret://fixture-token', 'future_publisher_field': {}})
        before = self.config.read_bytes(), self.config.stat().st_mtime_ns
        with patch('store.FileStore', side_effect=AssertionError('store opened')):
            self.assertEqual(catalog_url(self.root), URL)
        self.assertEqual(before, (self.config.read_bytes(), self.config.stat().st_mtime_ns))
        self.assertEqual(list(self.config.parent.iterdir()), [self.config])

    def test_override_precedes_missing_invalid_and_symlink_config(self):
        self.assertEqual(catalog_url(self.root, URL), URL)
        self.configure({})
        self.config.write_text('invalid')
        self.assertEqual(catalog_url(self.root, URL), URL)
        self.config.unlink()
        self.config.symlink_to(self.root / 'absent')
        self.assertEqual(catalog_url(self.root, URL), URL)

    def test_missing_url_is_actionable(self):
        for value in (None, {}, {'account_id': 'fixture'}):
            if value is not None:
                self.configure(value)
            with self.assertRaisesRegex(ValueError, 'public_url.*remote.json.*--url') as raised:
                catalog_url(self.root)
            self.assertNotIn('agentlocker.dev', str(raised.exception))
        self.assertFalse((self.root / 'config.json').exists())

    def test_rejects_malformed_config_without_echoing_contents(self):
        self.config.parent.mkdir()
        for contents in ('{', '[]', 'null', '{"public_url":"' + URL + '","public_url":"' + URL + '"}',
                         '{"public_url":"' + URL + '","publisher":{"id":1,"id":2}}',
                         '{"public_url":"' + URL + '","bad":NaN}', '\udcff'):
            self.config.write_bytes(contents.encode('utf-8', errors='surrogatepass'))
            with self.subTest(contents=repr(contents)), self.assertRaises(ValueError):
                catalog_url(self.root)

    def test_rejects_symlink_config_and_parent(self):
        target = self.root / 'target.json'
        target.write_text(json.dumps({'public_url': URL}))
        self.config.parent.mkdir()
        self.config.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            catalog_url(self.root)
        self.config.unlink()
        self.config.parent.rmdir()
        directory = self.root / 'elsewhere'
        directory.mkdir()
        (directory / 'remote.json').write_bytes(target.read_bytes())
        self.config.parent.symlink_to(directory, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            catalog_url(self.root)
        self.assertEqual(catalog_url(self.root, URL), URL)

    def test_rejects_nonordinary_config(self):
        self.config.mkdir(parents=True)
        with self.assertRaises(ValueError):
            catalog_url(self.root)

    def test_rejects_unsafe_urls_in_config_overrides_and_discovery(self):
        urls = (None, False, 4, '', 'http://example.org/mcp', 'https:///mcp', 'https://example.org',
                URL + '/', URL + '?', URL + '?token=fixture', URL + '#', URL + '#fragment',
                'https://user:pass@example.org/mcp', 'https://example.org:0/mcp',
                'https://example.org:65536/mcp', 'https://example.org:bad/mcp',
                'https://example.org:/mcp', 'https://example.org\\evil/mcp',
                URL + '\n', '\t' + URL, 'https://exa mple.org/mcp',
                'https://example.org\x00/mcp', 'https://example.org\x7f/mcp',
                'https://example.org\x85/mcp', 'https://example.org/%6dcp',
                'https://example.org/other/mcp', 'https://example.org;bad/mcp',
                'https://$(fixture)/mcp', 'https://`fixture`/mcp', 'https://example.org%2fother/mcp',
                'https://[::1%$(fixture)]/mcp', 'https://[::1%fixture]/mcp')
        for url in urls:
            with self.subTest(url=url):
                self.configure({'public_url': url})
                with self.assertRaises(ValueError):
                    catalog_url(self.root)
                if url is not None:
                    self.configure({'public_url': URL})
                    with self.assertRaises(ValueError):
                        catalog_url(self.root, url)
                with self.assertRaises(ValueError):
                    oauth_discovery_url(url)

    def test_discovery_uses_validated_origin_with_port(self):
        for origin in ('https://catalog.example.org', 'https://catalog.example.org:8443', 'https://[2001:db8::1]:8443'):
            url = origin + '/mcp'
            self.assertEqual(catalog_url(self.root, url), url)
            self.assertEqual(oauth_discovery_url(url), origin + '/.well-known/oauth-authorization-server')
