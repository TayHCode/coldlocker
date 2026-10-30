import json
import os
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch

import modes
from gateway_adapter import environment, oauth_name, prepare, save, seed, sync, redact_log
from gateway import identity, state
from store import FileStore
from test_store import make_hub


class AdapterTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / '.scratch/test-root'
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        make_hub(self.root)
        self.store = FileStore(self.root)
        self.canary = 'canary-' + secrets.token_hex(24)
        self.store.put('sample-token', self.canary)

    def test_prepare_reference_only_and_permissions(self):
        runtime = prepare(self.root, self.root)
        settings = json.loads((runtime / 'mcp_settings.json').read_text(encoding='utf-8'))
        self.assertNotIn(self.canary, json.dumps(settings))
        env = environment(settings, self.store)
        self.assertIn(self.canary, env.values())
        for path in (runtime.parent, runtime):
            modes.check(self, path, 0o700)
        modes.check(self, runtime / 'mcp_settings.json', 0o600)

    def test_multiline_log_secret_redacted(self):
        self.store.put('multiline', self.canary + '\nsecond-private-line')
        self.assertNotIn(self.canary, redact_log(self.store, self.canary + '\n'))
        self.assertNotIn('second-private-line', redact_log(self.store, 'second-private-line\n'))

    def test_missing_reference_fails_before_launch(self):
        runtime = prepare(self.root, self.root)
        self.store.delete('sample-token')
        with self.assertRaises(OSError):
            environment(json.loads((runtime / 'mcp_settings.json').read_text(encoding='utf-8')), self.store)

    def test_undeclared_inherited_secret_removed(self):
        with patch.dict(os.environ, {'AGENTLOCKER_SECRET_00': self.canary}):
            self.assertNotIn('AGENTLOCKER_SECRET_00', environment({}, self.store))

    def test_oauth_roundtrip_refresh_null_and_redaction(self):
        oauth = {'clientId': 'client-' + secrets.token_hex(8), 'clientSecret': self.canary,
                 'accessToken': 'access-' + secrets.token_hex(24),
                 'refreshToken': 'refresh-' + secrets.token_hex(24),
                 'expiresAt': 123456, 'scopes': ['demo'], 'enabled': True,
                 'pendingAuthorization': {'codeVerifier': 'verifier-' + secrets.token_hex(12)}}
        fixture = {'mcpServers': {'sample': {'oauth': oauth}}}
        path = self.root / 'oauth-fixture.json'
        save(path, fixture)
        self.assertEqual(sync(json.loads(path.read_text(encoding='utf-8')), self.store), 1)
        seeded = seed({'mcpServers': {'sample': {}, 'other': {}}}, self.store)
        self.assertEqual(seeded['mcpServers']['sample']['oauth'], oauth)
        self.assertNotIn('oauth', seeded['mcpServers']['other'])
        self.assertNotIn(self.canary, self.store.redact(json.dumps(fixture)))
        oauth['accessToken'] = 'replacement-' + secrets.token_hex(24)
        oauth['refreshToken'] = None
        sync(fixture, self.store)
        self.assertEqual(seed({'mcpServers': {'sample': {}}}, self.store)['mcpServers']['sample']['oauth'], oauth)

    def test_prepare_preserves_oauth_and_local_configuration(self):
        runtime = prepare(self.root, self.root)
        path = runtime / 'mcp_settings.json'
        fixture = json.loads(path.read_text(encoding='utf-8'))
        fixture['mcpServers']['sample']['oauth'] = {'accessToken': self.canary, 'clientId': 'demo'}
        fixture['users'] = [{'username': 'local'}]
        fixture['systemConfig']['marker'] = True
        fixture['bearerKeys'] = [{'id': 'local'}]
        fixture['groups'] = [{'id': 'demo'}]
        save(path, fixture)
        prepare(self.root, self.root)
        result = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(result, fixture)
        path.unlink()
        prepare(self.root, self.root)
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['mcpServers']['sample']['oauth'], fixture['mcpServers']['sample']['oauth'])

    def test_symlinks_refused_without_target_write(self):
        runtime = prepare(self.root, self.root)
        path = runtime / 'mcp_settings.json'
        path.unlink()
        outside = self.root / 'outside'
        outside.write_text('preserve', encoding='utf-8')
        path.symlink_to(outside)
        with self.assertRaises(ValueError):
            prepare(self.root, self.root)
        self.assertEqual(outside.read_text(encoding='utf-8'), 'preserve')

    def test_opaque_names_distinguish_boundaries(self):
        self.assertNotEqual(oauth_name('ab', ('c',)), oauth_name('a', ('bc',)))
        self.assertLessEqual(len(oauth_name('sample', ('clientSecret',))), 63)

    @unittest.skipIf(os.name == 'nt', 'process identity comes from /proc or ps, which the unsupported Windows gateway never uses')
    def test_stale_pid_does_not_identify_current_process(self):
        runtime = prepare(self.root, self.root)
        save(runtime / 'pid.json', {'pid': os.getpid(), 'identity': 'different'})
        self.assertIsNone(state(runtime))
        self.assertIsNotNone(identity(os.getpid()))
        save(runtime / 'pid.json', {'pid': os.getpid(), 'identity': identity(os.getpid())})
        self.assertIsNone(state(runtime))

    def test_demo_real_stdio_protocol(self):
        import subprocess
        import sys
        script = Path(__file__).resolve().parents[1] / 'examples/test-servers/stdio.py'
        requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                     'params': {'name': 'add', 'arguments': {'a': 2, 'b': 3}}}]
        result = subprocess.run([sys.executable, str(script)], text=True, capture_output=True,
                                input=''.join(json.dumps(r) + '\n' for r in requests), check=True, encoding='utf-8')
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(responses[0]['result']['tools'][0]['name'], 'add')
        self.assertEqual(responses[1]['result']['content'][0]['text'], '5')
