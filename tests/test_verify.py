from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import modes
import hub
import hub_ui
from store import FileStore
import verify


def gateway_call(calls):
    return any(c.get('tool') == 'example-server-add' and c['source'] == 'gateway' for c in calls['calls'])


def wait_for_gateway_call(api, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        calls = api('/api/calls')
        if gateway_call(calls) or time.monotonic() >= deadline:
            return calls
        time.sleep(.05)


class Endpoint(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_DELETE(self):
        self.send_response(204)
        self.end_headers()

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.methods.append(request['method'])
        method = request['method']
        if method == 'notifications/initialized':
            self.send_response(202)
            self.end_headers()
            return
        if method == 'initialize':
            result = {'protocolVersion': '2024-11-05'}
        else:
            result = {'tools': self.server.tools}
            if self.server.paginate and not request.get('params', {}).get('cursor'):
                result = {'tools': [], 'nextCursor': 'page2'}
        message = {'jsonrpc': '2.0', 'id': request['id'], 'result': result}
        if self.server.fail:
            message = {'id': request['id'], 'error': {'message': self.server.canary}}
        raw = json.dumps(message).encode()
        if self.server.sse:
            raw = b'data: ' + raw + b'\n\n'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream' if self.server.sse else 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'mcp/servers').mkdir(parents=True)
        self.server_path = self.root / 'mcp/servers/demo.json'
        self.definition = {'name': 'demo', 'description': 'Test tool', 'transport': 'stdio', 'command': 'unused', 'args': []}
        self.server_path.write_text(json.dumps(self.definition), encoding='utf-8')
        manifest = {'schema_version': 1, 'name': 'agentlocker', 'license': 'MIT', 'skills': [], 'rules': [], 'routines': [], 'mcp_servers': []}
        hub.record(manifest, 'mcp_servers', 'demo', self.server_path, 'agentlocker', 'authored', root=self.root)
        (self.root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
        self.http.methods = []
        self.http.tools = [{'name': 'demo-add'}]
        self.http.fail = False
        self.http.paginate = False
        self.http.sse = False
        self.http.canary = 'canary-fixture-private-value'
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        self.config = {'gateway': {'backend': 'executor', 'executor': {'url': 'http://127.0.0.1:' + str(self.http.server_port) + '/mcp'}}}
        (self.root / 'config.json').write_text(json.dumps(self.config), encoding='utf-8')

    def test_live_success_pagination_sse_and_permissions(self):
        self.assertIsNone(verify.current(self.root, 'demo'))
        self.http.paginate = True
        self.http.sse = True
        result = verify.check(self.root, 'demo')
        self.assertTrue(result['ok'])
        self.assertEqual(result['tools'], 1)
        self.assertEqual(self.http.methods, ['initialize', 'notifications/initialized', 'tools/list', 'tools/list'])
        modes.check(self, self.root / '.hub-store/verify.json', 0o600)
        entry = hub_ui.auth_status(self.root, FileStore(self.root))[0]
        self.assertEqual(entry['state'], 'verified')
        self.assertIsInstance(entry['verified_at'], float)

    def test_zero_or_unattributed_tools_never_verified(self):
        for tools in ([], [{'name': 'execute'}], [{'name': 'other-add'}]):
            self.http.tools = tools
            result = verify.check(self.root, 'demo')
            self.assertFalse(result['ok'])
            self.assertEqual(result['error'], 'Checked, listed 0 tools')

    def test_stale_definition_binding_and_credentials(self):
        store = FileStore(self.root)
        self.definition['env'] = {'API_TOKEN': 'secret://fixture-token'}
        self.server_path.write_text(json.dumps(self.definition), encoding='utf-8')
        store.put('fixture-token', 'first-private-value')
        verify.check(self.root, 'demo')
        store.put('fixture-token', 'second-private-value')
        self.assertIsNone(verify.current(self.root, 'demo'))
        verify.check(self.root, 'demo')
        self.definition['args'] = ['changed']
        self.server_path.write_text(json.dumps(self.definition), encoding='utf-8')
        self.assertIsNone(verify.current(self.root, 'demo'))
        verify.check(self.root, 'demo')
        from ui_auth import Authentication
        Authentication(self.root, store).write_metadata('demo', {'headers': {}, 'method': 'bearer'})
        self.assertIsNone(verify.current(self.root, 'demo'))

    def test_failure_canary_never_persisted_or_returned(self):
        self.http.fail = True
        result = verify.check(self.root, 'demo')
        self.assertFalse(result['ok'])
        status = hub_ui.auth_status(self.root, FileStore(self.root))
        self.assertNotIn(self.http.canary, json.dumps([result, status]))
        for path in (self.root / '.hub-store').rglob('*'):
            if path.is_file():
                self.assertNotIn(self.http.canary, path.read_text(encoding='utf-8'))

    def test_unreachable_and_missing_gateway(self):
        (self.root / 'config.json').unlink()
        result = verify.check(self.root, 'demo')
        self.assertEqual(result['status'], 'unreachable')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], verify.START)
        self.assertEqual(hub_ui.auth_status(self.root, FileStore(self.root))[0]['state'], 'check_failed')

    def test_stopped_gateway_error_names_no_command(self):
        (self.root / 'config.json').unlink()
        result = verify.check(self.root, 'demo')
        self.assertEqual(result['status'], 'unreachable')
        self.assertNotIn('python3', result['error'])
        entry = hub_ui.auth_status(self.root, FileStore(self.root))[0]
        self.assertEqual(entry['check'], 'unreachable')

    def test_zero_tools_is_distinct_from_failure_and_success(self):
        self.http.tools = []
        result = verify.check(self.root, 'demo')
        self.assertEqual(result['status'], 'no_tools')
        self.assertEqual(hub_ui.auth_status(self.root, FileStore(self.root))[0]['check'], 'no_tools')
        self.http.tools = [{'name': 'demo-add'}]
        self.assertEqual(verify.check(self.root, 'demo')['status'], 'verified')
        self.assertEqual(hub_ui.auth_status(self.root, FileStore(self.root))[0]['check'], 'verified')

    def test_gateway_definition_snapshot_required(self):
        (self.root / 'config.json').unlink()
        with patch('gateway.state', return_value={'port': self.http.server_port}):
            self.assertFalse(verify.check(self.root, 'demo')['ok'])
        self.assertEqual(self.http.methods, [])

    def test_prepared_configuration_must_match_current_definition(self):
        from gateway_render import render
        prepared = render(self.root)
        self.assertIn('demo', verify.gateway_fingerprints(self.root, prepared))
        self.definition['command'] = 'changed-after-prepare'
        self.server_path.write_text(json.dumps(self.definition), encoding='utf-8')
        self.assertNotIn('demo', verify.gateway_fingerprints(self.root, prepared))

    def test_longest_server_prefix_prevents_cross_attribution(self):
        path = self.root / 'mcp/servers/demo-other.json'
        path.write_text(json.dumps({**self.definition, 'name': 'demo-other'}), encoding='utf-8')
        manifest = json.loads((self.root / 'manifest.json').read_text(encoding='utf-8'))
        hub.record(manifest, 'mcp_servers', 'demo-other', path, 'agentlocker', 'authored', root=self.root)
        self.http.tools = [{'name': 'demo-other-add'}]
        self.assertFalse(verify.check(self.root, 'demo')['ok'])
        self.assertTrue(verify.check(self.root, 'demo-other')['ok'])

    def test_saved_credentials_are_not_verified(self):
        self.definition['env'] = {'API_TOKEN': 'secret://fixture-token'}
        self.server_path.write_text(json.dumps(self.definition), encoding='utf-8')
        store = FileStore(self.root)
        store.put('fixture-token', 'fixture-value')
        entry = hub_ui.auth_status(self.root, store)[0]
        self.assertEqual(entry['state'], 'saved')
        self.assertIsNotNone(entry['saved_at'])
        self.assertEqual(entry['detail'], 'Not checked yet')
        self.assertEqual(self.http.methods, [])


class GatewayObservationTests(unittest.TestCase):
    def test_waits_for_the_exact_tool_and_gateway_source(self):
        snapshots = [
            {'calls': []},
            {'calls': [{'tool': 'example-server-add', 'source': 'stdio'}]},
            {'calls': [{'tool': 'other-add', 'source': 'gateway'}]},
            {'calls': [{'tool': 'example-server-add', 'source': 'gateway'}]},
        ]
        with patch(__name__ + '.time.sleep'), patch(__name__ + '.time.monotonic', return_value=0):
            api = Mock(side_effect=snapshots)
            result = wait_for_gateway_call(api)
        self.assertEqual(result, snapshots[-1])
        self.assertEqual(api.call_count, 4)

    def test_missing_attribution_still_fails_after_deadline(self):
        calls = {'calls': [{'tool': 'example-server-add', 'source': 'stdio'}]}
        with patch(__name__ + '.time.monotonic', side_effect=[0, 11]):
            result = wait_for_gateway_call(lambda route: calls)
        self.assertFalse(gateway_call(result))


class RealGatewayTests(unittest.TestCase):
    def test_example_server_live_ui_check_and_calls(self):
        import http.client
        import shutil
        import socket
        import subprocess
        import sys
        import urllib.request
        import urllib.error
        import gateway
        source = Path(__file__).resolve().parents[1]
        runtime = source / '.scratch/mcphub-runtime'
        if not (runtime / 'node_modules/@samanhappy/mcphub/package.json').is_file():
            self.skipTest('MCPHub 1.1.7 runtime absent; install pinned runtime for real gateway verification')
        scratch = source / '.scratch'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            root = Path(directory) / 'fixture'
            shutil.copytree(source, root, ignore=shutil.ignore_patterns(
                '.git', '.scratch', '.hub-store', '.worktrees', '__pycache__', 'config.json'))
            shutil.copytree(runtime, root / '.scratch/mcphub-runtime')
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            def run(*args):
                result = subprocess.run([sys.executable, *args], cwd=root, capture_output=True, text=True, timeout=40, encoding='utf-8')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout
            try:
                run('gateway.py', 'prepare')
                run('gateway.py', 'start', '--port', str(port))
                with patch.object(gateway, 'ROOT', root), hub_ui.SetupServer(root, scan_home=root / '.scratch/home') as server:
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    def api(route, data=None):
                        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=15)
                        try:
                            connection.request('GET' if data is None else 'POST', route,
                                body=None if data is None else json.dumps(data),
                                headers={'Content-Type': 'application/json', 'X-CSRF-Token': server.token})
                            response = connection.getresponse()
                            body = json.load(response)
                            self.assertEqual(response.status, 200, body)
                            return body
                        finally:
                            connection.close()
                    try:
                        result = api('/api/auth/check', {'server': 'example-server'})
                        self.assertTrue(result['ok'], result)
                        self.assertEqual(result['tools'], 1)
                        base = 'http://127.0.0.1:' + str(port)
                        login = urllib.request.Request(base + '/api/auth/login', json.dumps({
                            'username': 'admin', 'password': FileStore(root).resolve('secret://gateway-admin')}).encode(),
                            {'Content-Type': 'application/json'})
                        with urllib.request.urlopen(login) as response:
                            token = json.load(response)['token']
                        with urllib.request.urlopen(urllib.request.Request(base + '/api/activities/available',
                                headers={'x-auth-token': token})) as response:
                            activity = json.load(response)
                        self.assertFalse(activity['data']['available'])
                        transcript = run('examples/test-servers/http_client.py', 'http://127.0.0.1:' + str(port) + '/mcp')
                        requests = [{'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'fixture-agent'}}},
                                    {'id': 2, 'method': 'tools/list'}]
                        process = subprocess.run([sys.executable, 'mcp_endpoint.py'], cwd=root,
                            input=''.join(json.dumps(r) + '\n' for r in requests), capture_output=True, text=True, timeout=10, encoding='utf-8')
                        self.assertEqual(process.returncode, 0, process.stderr)
                        calls = wait_for_gateway_call(api)
                        self.assertIn('fixture-agent', calls['first_call'])
                        self.assertTrue(gateway_call(calls), calls)
                        cursor = max(c['ts'] for c in calls['calls'])
                        self.assertEqual(api('/api/calls?since=' + str(cursor))['calls'], [])
                        self.assertEqual(api('/api/status')['auth'][0]['state'], 'verified')
                        evidence = source / '.scratch/evidence/v6-l2'
                        evidence.mkdir(parents=True, exist_ok=True)
                        for filename, value in (('auth-check.json', result), ('calls.json', calls), ('activity-available.json', activity),
                                                ('verify.json', json.loads((root / '.hub-store/verify.json').read_text(encoding='utf-8')))):
                            (evidence / filename).write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
                        (evidence / 'gateway-client.txt').write_text(transcript, encoding='utf-8')
                        (evidence / 'gateway.log').write_text((root / '.hub-store/gateway/gateway.log').read_text(encoding='utf-8'), encoding='utf-8')
                    finally:
                        server.shutdown()
                        thread.join()
            finally:
                run('gateway.py', 'stop')
            (source / '.scratch/evidence/v6-l2/gateway-stopped.txt').write_text('Fixture gateway stopped successfully.\n', encoding='utf-8')
