import ipaddress
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from checks.scan import address_findings, allowed_ipv4, findings, scan


def synthetic(*parts):
    return ''.join(parts)


class PrivateScanTests(unittest.TestCase):
    def test_documentation_and_listener_allowlist(self):
        for value in ('127.0.0.1', '0.0.0.0', '192.0.2.7', '198.51.100.8', '203.0.113.9'):
            with self.subTest(value=value):
                self.assertEqual(findings(value), [])
                self.assertTrue(allowed_ipv4(ipaddress.IPv4Address(value)))

    def test_plain_ipv4_is_checked_in_any_text(self):
        with patch('checks.scan.allowed_ipv4', return_value=False):
            for source in ('endpoint=192.0.2.7', 'Endpoint 192.0.2.7.'):
                self.assertEqual(findings(source), [(1, 'ipv4-address')])
        self.assertEqual(findings('version=999.2.3.4'), [])

    def test_plain_private_host_and_email_are_checked_for_scan_and_import(self):
        hostname = synthetic('daemon', '-7421.internal')
        email = synthetic('operator', '@', 'systems', '.lan')
        source = f'Connect to {hostname}; email {email}.\n'
        self.assertEqual(findings(source), [(1, 'email-address'), (1, 'private-host')])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = root / 'rules/policy.md'
            policy.parent.mkdir()
            policy.write_text(source, encoding='utf-8')
            with patch('checks.scan.git_files', return_value=[Path('rules/policy.md')]):
                result = scan(root)
            self.assertEqual(result, ['rules/policy.md:1: email-address', 'rules/policy.md:1: private-host'])
            self.assertNotIn(hostname, repr(result))
            self.assertNotIn(email, repr(result))
        from checks.validators import safe_fragment
        with self.assertRaisesRegex(ValueError, 'definition contains a secret or private path/name'):
            safe_fragment(source)

    def test_numeric_host_and_package_or_encoding_labels(self):
        hostname = synthetic('worker', '-7319')
        self.assertEqual(findings(hostname), [(1, 'private-host')])
        self.assertEqual(findings('Connect to ' + hostname), [(1, 'private-host')])
        self.assertEqual(findings(synthetic('cache-tool', '-3.2.1')), [])
        self.assertEqual(findings(synthetic('UTF', '-8')), [])
        self.assertEqual(findings('server uses ' + synthetic('UTF', '-8')), [])

    def test_numeric_structured_identifiers_are_allowed_but_private_names_are_not(self):
        from checks.validators import mcp, routine, safe_identifier

        for identifier in ('conv-0', 'cron-1'):
            with self.subTest(identifier=identifier):
                self.assertEqual(findings(identifier), [(1, 'private-host')])
                safe_identifier(identifier)

        mcp({"name": "conv-0", "description": "Fixture server", "transport": "stdio",
             "command": "python3", "args": []})
        routine({"id": "cron-1", "description": "Fixture routine", "owner": "agentlocker",
                 "runner": "cron", "schedule": {"type": "cron", "expression": "0 1 * * *"},
                 "timezone": "UTC", "prompt": "prompt.md", "overlap": "skip",
                 "timeout_seconds": 600, "enabled": False})

        for private_name in (synthetic('worker', '.internal'),
                             synthetic('operator', '@', 'systems', '.lan')):
            with self.subTest(private_name=private_name):
                with self.assertRaisesRegex(ValueError, 'definition contains a secret or private path/name'):
                    safe_identifier(private_name)

    def test_private_dns_suffixes_and_code_attribute_exceptions(self):
        hostname = synthetic('worker', '.internal')
        self.assertEqual(findings('Contact ' + hostname + ' tomorrow'), [(1, 'private-host')])
        local_hostname = synthetic('worker', '.local')
        self.assertEqual(findings('Connect to ' + local_hostname), [(1, 'private-host')])
        self.assertEqual(findings('{"host":"' + local_hostname + '"}'), [(1, 'private-host')])
        self.assertEqual(findings('path = Path.home()'), [])

    def test_explicit_documentation_fixture_and_encoding_exceptions(self):
        examples = (
            'Host: ' + synthetic('example', '-01'),
            'Contact ' + synthetic('reader', '@', 'example.', 'invalid'),
            'Use ' + synthetic('UTF', '-8') + ' and ' + synthetic('Windows', '-1252')
            + ' with package ' + synthetic('cache-tool', '-3.2'),
        )
        for source in examples:
            with self.subTest(source=source):
                self.assertEqual(findings(source), [])

    def test_commit_email_allowlist_identity_file_is_exempt_from_plain_email_check(self):
        address = synthetic('maintainer', '@', 'identity', '.corp')
        source = address + '\n'
        self.assertEqual(findings(source), [(1, 'email-address')])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowlist = root / 'config/commit-email-allowlist.txt'
            allowlist.parent.mkdir()
            allowlist.write_text(source, encoding='utf-8')
            with patch('checks.scan.git_files', return_value=[Path('config/commit-email-allowlist.txt')]):
                self.assertEqual(scan(root), [])

    def test_tailnet_boundaries(self):
        for second, expected in ((63, 'ipv4-address'), (64, 'tailnet-ip'),
                                 (127, 'tailnet-ip'), (128, 'ipv4-address')):
            address = ipaddress.IPv4Address((100 << 24) + (second << 16) + 7)
            self.assertEqual(address_findings(str(address)), {expected})

    def test_python_address_assemblies(self):
        sources = (
            "address = '.'.join(map(str, (192, 0, 2, 7)))",
            "parts = [192, 0, 2, 7]\naddress = '.'.join(map(str, parts))",
            "a, b, c, d = (192, 0, 2, 7)\naddress = '.'.join(map(str, (a, b, c, d)))",
            "address = '192' + '.0.2.7'",
            "a = 192\nb = 0\nc = 2\nd = 7\naddress = f'{a}.{b}.{c}.{d}'",
            "address = '%s.%s.%s.%s' % (192, 0, 2, 7)",
            "address = '{}.{}.{}.{}'.format(192, 0, 2, 7)",
            "address = '.'.join(('192', '0', '2', '7'))",
        )
        for source in sources:
            with self.subTest(source=source):
                self.assertEqual(findings(source, 'sample.py'), [])
                with patch('checks.scan.allowed_ipv4', return_value=False):
                    self.assertTrue(any(rule == 'ipv4-address' for _, rule in findings(source, 'sample.py')))

    def test_python_hostname_assemblies(self):
        sources = (
            "host = 'node' + '-01'",
            "host = 'node' + '.internal'",
            "host = 'node' '-01'",
            "name = 'node'\nhost = f'{name}-01'",
            "host = '%s-%02d' % ('node', 1)",
            "host = '{}-01'.format('node')",
            "host = '-'.join(['node', '01'])",
        )
        for source in sources:
            with self.subTest(source=source):
                self.assertTrue(any(rule == 'assembled-host' for _, rule in findings(source, 'sample.py')))

    def test_non_python_fragment_concatenation(self):
        for source in ("const host = 'node' + '-01';", "host = f\"{'node'}-01\"",
                       "host = '%s-%02d' % ('node', 1)"):
            self.assertEqual(findings(source, 'sample.txt'), [(1, 'assembled-host')])
        with patch('checks.scan.allowed_ipv4', return_value=False):
            self.assertEqual(findings("'192' + '.0.2.7'", 'sample.txt'), [(1, 'ipv4-address')])

    def test_legitimate_code_and_explicit_synthetic_hosts(self):
        for source in ("version = (1, 2, 3, 4)", "host = 'fixture' + '-01'",
                       "host = 'example' + '-01'", "text = 'Build ' + 'release-01'",
                       "value = unknown_call()", "broken Python {{{"):
            self.assertEqual(findings(source, 'sample.py'), [])

    def test_scan_reports_file_and_line_without_values_or_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'sample.py').write_text("raise RuntimeError('never run')\nhost = 'node' + '-01'\n", encoding='utf-8')
            with patch('checks.scan.git_files', return_value=[Path('sample.py')]):
                self.assertEqual(scan(root), ['sample.py:2: assembled-host'])

    def test_bounded_formatting(self):
        for source in ("x = '%999999999s' % 'node'", "x = f'{1:999999999}'",
                       "x = '{0.__class__}'.format('node')", "x = '%*s' % (999999999, 'node')"):
            self.assertEqual(findings(source, 'sample.py'), [])


class LocalPatternTests(unittest.TestCase):
    def repository(self, patterns):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / 'notes.txt').write_text('safe\nowner is Zebulon Quux\n', encoding='utf-8')
        if patterns is not None:
            (root / '.hub-store').mkdir()
            (root / '.hub-store' / 'private-patterns.txt').write_text(patterns, encoding='utf-8')
        return root

    def test_local_pattern_file_flags_matching_line_without_value(self):
        root = self.repository('\nzebulon[ ]quux\n')
        with patch('checks.scan.git_files', return_value=[Path('notes.txt')]):
            self.assertEqual(scan(root), ['notes.txt:2: private-pattern'])

    def test_no_local_pattern_file_means_no_extra_findings(self):
        root = self.repository(None)
        with patch('checks.scan.git_files', return_value=[Path('notes.txt')]):
            self.assertEqual(scan(root), [])

    def test_invalid_local_pattern_fails_the_scan(self):
        root = self.repository('(unclosed\n')
        with patch('checks.scan.git_files', return_value=[Path('notes.txt')]):
            self.assertEqual(scan(root), ['.hub-store/private-patterns.txt:1: invalid pattern'])

    def test_tracked_scanner_has_no_owner_specific_rules(self):
        from checks import scan as module
        self.assertNotIn('private-name', module.RULES)
        self.assertNotIn('private-host', module.RULES)
