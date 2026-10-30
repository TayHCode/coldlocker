import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

from gateway_render import render, render_argument


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.scratch/mcphub-runtime'
PACKAGE = RUNTIME / 'node_modules/@samanhappy/mcphub/package.json'


class ExampleServerTests(unittest.TestCase):
    def test_shipped_definition_over_stdio(self):
        requests = [
            {'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2024-11-05', 'capabilities': {},
                'clientInfo': {'name': 'example-test', 'version': '1.0.0'}}},
            {'method': 'notifications/initialized'},
            {'id': 2, 'method': 'tools/list'},
            {'id': 3, 'method': 'tools/call',
             'params': {'name': 'add', 'arguments': {'a': 2, 'b': 3}}},
            {'id': 4, 'method': 'tools/call',
             'params': {'name': 'add', 'arguments': {'a': True, 'b': 3}}},
            {'id': 5, 'method': 'tools/call',
             'params': {'name': 'unknown', 'arguments': {}}}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / 'manifest.json', root / 'manifest.json')
            for name in ('mcp', 'examples'):
                shutil.copytree(ROOT / name, root / name)
            server = render(root)['mcpServers']['example-server']
            self.assertEqual(set(server), {'type', 'command', 'args'})
            result = subprocess.run([server['command'], *server['args']], cwd=directory,
                                    input=''.join(json.dumps({'jsonrpc': '2.0', **r}) + '\n'
                                                  for r in requests),
                                    capture_output=True, text=True, timeout=10, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([r['id'] for r in responses], [1, 2, 3, 4, 5])
        self.assertEqual(responses[0]['result']['serverInfo']['name'], 'example-server')
        self.assertEqual(responses[1]['result']['tools'][0]['name'], 'add')
        self.assertEqual(responses[2]['result']['content'], [{'type': 'text', 'text': '5'}])
        self.assertFalse(responses[2]['result']['isError'])
        for response in responses[3:]:
            self.assertEqual(response['error']['code'], -32602)

    def test_root_prefix_uses_selected_source_and_rejects_unsafe_paths(self):
        scratch = ROOT / '.scratch/test-root'
        scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            root = Path(directory)
            script = root / 'server.py'
            script.write_text('', encoding='utf-8')
            self.assertEqual(render_argument('${AGENTLOCKER_ROOT}/server.py', root), str(script))
            (root / 'link.py').symlink_to(script)
            for value in ('../outside', '/outside', 'link.py', '${OTHER}/server.py', ''):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    render_argument('${AGENTLOCKER_ROOT}/' + value, root)
            with self.assertRaises(ValueError):
                render_argument('prefix${AGENTLOCKER_ROOT}/server.py', root)

    @unittest.skipUnless(PACKAGE.is_file(), 'MCPHub node runtime is absent; install the pinned runtime')
    def test_shipped_definition_through_mcphub(self):
        self.assertEqual(json.loads(PACKAGE.read_text(encoding='utf-8'))['version'], '1.1.7')
        scratch = ROOT / '.scratch/test-root'
        scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            checkout = Path(directory) / 'checkout with spaces'
            shutil.copytree(ROOT, checkout, ignore=shutil.ignore_patterns(
                '.git', '.scratch', '.hub-store', '.worktrees', '__pycache__', 'config.json'))
            runtime = checkout / '.scratch/mcphub-runtime'
            runtime.parent.mkdir(mode=0o700)
            shutil.copytree(RUNTIME, runtime)
            home = checkout / '.scratch/home'
            home.mkdir(mode=0o700)
            env = {**os.environ, 'HOME': str(home), 'USERPROFILE': str(home), 'XDG_CONFIG_HOME': str(home / 'config'),
                   'XDG_DATA_HOME': str(home / 'data')}
            with socket.socket() as listener:
                listener.bind(('.'.join(('127', '0', '0', '1')), 0))
                port = listener.getsockname()[1]

            def run(*args):
                result = subprocess.run([sys.executable, *args], cwd=checkout, env=env,
                                        capture_output=True, text=True, timeout=45, encoding='utf-8')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout

            try:
                run('gateway.py', 'prepare')
                run('gateway.py', 'start', '--port', str(port))
                output = run('examples/test-servers/http_client.py',
                             'http://' + '.'.join(('127', '0', '0', '1')) + f':{port}/mcp')
                self.assertIn('initialize:', output)
                self.assertIn('tools/list:', output)
                self.assertIn('tools/call:', output)
            finally:
                run('gateway.py', 'stop')
            with socket.socket() as probe:
                self.assertNotEqual(probe.connect_ex(('.'.join(('127', '0', '0', '1')), port)), 0)
