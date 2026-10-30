import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import connect
import gateway
import modes
import test_gateway
from store import FileStore

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'examples/test-servers'))
from http_client import exchange

ENTRY = """import http from 'node:http';
http.createServer((req, res) => res.end('ok')).listen(process.env.PORT);
"""


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def wait_listening(port, process):
    for _ in range(100):
        if process.poll() is not None:
            raise AssertionError('fixture server exited early')
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', port)) == 0:
                return
        time.sleep(.1)
    raise AssertionError('fixture server did not listen')


def request(port, path='/', host=None, origin=None, method='GET', body=None, extra=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=15)
    try:
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if host is not None:
            connection.putheader('Host', host)
        if origin is not None:
            connection.putheader('Origin', origin)
        for key, value in (extra or {}).items():
            connection.putheader(key, value)
        if body is not None:
            connection.putheader('Content-Length', str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@unittest.skipUnless(shutil.which('node'), 'node is required')
class BootstrapGuardTests(unittest.TestCase):
    def setUp(self):
        scratch = SOURCE / '.scratch'
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        (directory / 'bootstrap.mjs').write_text(gateway.BOOTSTRAP, encoding='utf-8')
        (directory / 'entry.mjs').write_text(ENTRY, encoding='utf-8')
        self.port = free_port()
        process = subprocess.Popen(
            ['node', str(directory / 'bootstrap.mjs')], cwd=directory,
            env={**{name: os.environ[name] for name in ('PATH', 'SYSTEMROOT') if name in os.environ},
                 'PORT': str(self.port), 'AGENTLOCKER_BIND': '127.0.0.1',
                 'AGENTLOCKER_ENTRY': str(directory / 'entry.mjs')},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(process.wait)
        self.addCleanup(process.terminate)
        wait_listening(self.port, process)

    def status(self, **arguments):
        return request(self.port, **arguments)[0]

    def test_loopback_authorities_are_served(self):
        for host in (f'127.0.0.1:{self.port}', f'localhost:{self.port}', f'[::1]:{self.port}',
                     f'LocalHost:{self.port}'):
            with self.subTest(host=host):
                self.assertEqual(self.status(host=host), 200)

    def test_other_authorities_are_refused_on_every_route(self):
        port = self.port
        for host in (f'example.invalid:{port}', 'example.invalid', f'127.0.0.1:{port + 1}', '127.0.0.1',
                     'localhost', f'127.0.0.1:{port}.example.invalid', f'user@127.0.0.1:{port}',
                     f'127.0.0.1:{port},example.invalid', '', f'[::1]x:{port}', f'0x7f.1:{port}'):
            for path in ('/', '/mcp', '/sse', '/api/auth/login', '/messages?x=1'):
                with self.subTest(host=host, path=path):
                    self.assertEqual(self.status(host=host, path=path), 403)

    def test_missing_host_is_refused(self):
        with socket.create_connection(('127.0.0.1', self.port), timeout=10) as client:
            client.sendall(b'GET / HTTP/1.0\r\n\r\n')
            self.assertRegex(client.recv(64), rb'^HTTP/1\.[01] 403 ')

    def test_origin_must_be_a_matching_loopback_origin(self):
        host, port = f'127.0.0.1:{self.port}', self.port
        for origin, expected in ((None, 200), (f'http://127.0.0.1:{port}', 200), (f'http://localhost:{port}', 200),
                                 (f'http://[::1]:{port}', 200), ('http://example.invalid', 403),
                                 (f'http://example.invalid:{port}', 403), ('null', 403), ('', 403),
                                 (f'http://127.0.0.1:{port + 1}', 403), (f'https://127.0.0.1:{port}', 403),
                                 (f'http://127.0.0.1:{port}.example.invalid', 403)):
            with self.subTest(origin=origin):
                self.assertEqual(self.status(host=host, origin=origin), expected)

    def test_rebound_host_with_loopback_origin_is_refused(self):
        self.assertEqual(self.status(host=f'example.invalid:{self.port}', origin=f'http://127.0.0.1:{self.port}'), 403)

    def test_post_body_is_not_processed_when_refused(self):
        self.assertEqual(self.status(host='example.invalid', method='POST', body=b'{}',
                                     extra={'Content-Type': 'application/json'}), 403)

    def test_upgrade_requests_follow_the_same_rule(self):
        headers = 'Connection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n'
        with socket.create_connection(('127.0.0.1', self.port), timeout=10) as client:
            client.sendall(f'GET /ws HTTP/1.1\r\nHost: example.invalid\r\n{headers}\r\n'.encode())
            data = b''
            while True:
                chunk = client.recv(256)
                if not chunk:
                    break
                data += chunk
        self.assertNotIn(b'101', data.split(b'\r\n', 1)[0])


class DashboardDefaultTests(unittest.TestCase):
    def test_dashboard_is_off_unless_requested(self):
        runtime = SOURCE / 'runtime'
        default = gateway.child_environment({}, store_stub(), runtime, 43871, SOURCE / 'package')
        enabled = gateway.child_environment({}, store_stub(), runtime, 43871, SOURCE / 'package', dashboard=True)
        self.assertEqual(default['DISABLE_WEB'], 'true')
        self.assertEqual(enabled['DISABLE_WEB'], 'false')
        for env in (default, enabled):
            self.assertEqual(env['ADMIN_PASSWORD'], 'admin-value')
            self.assertEqual(env['JWT_SECRET'], 'jwt-value')
            self.assertEqual(env['AGENTLOCKER_BIND'], gateway.LOOPBACK)
            self.assertEqual(env['PORT'], '43871')


class OptionPassThroughTests(unittest.TestCase):
    def start_arguments(self, *flags):
        with tempfile.TemporaryDirectory() as directory, patch('sys.platform', modes.GATEWAY_PLATFORM), \
                patch('gateway.start') as start, patch('gateway.servers_line', return_value='servers: unknown'):
            self.assertEqual(test_gateway.cli(Path(directory), 'start', '--port', '43962', *flags)[0], 0)
        return start.call_args

    def test_flags_reach_start_only_when_given(self):
        self.assertEqual(self.start_arguments().kwargs, {})
        self.assertEqual(self.start_arguments('--dashboard').kwargs, {'dashboard': True})
        self.assertEqual(self.start_arguments('--require-token', '--dashboard').kwargs,
                         {'dashboard': True, 'require_token': True})

    def test_choices_survive_a_refresh_restart(self):
        self.assertEqual(gateway.enabled({'port': 1, 'dashboard': True, 'require_token': False}), {'dashboard': True})
        self.assertEqual(gateway.enabled({'port': 1}), {})


def store_stub():
    class Store:
        def resolve(self, reference):
            return {'secret://gateway-admin': 'admin-value', 'secret://gateway-jwt': 'jwt-value'}[reference]
    return Store()


class TokenTests(unittest.TestCase):
    def setUp(self):
        scratch = SOURCE / '.scratch'
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / '.hub-store/gateway'
        self.runtime.mkdir(parents=True)
        self.store = FileStore(self.root)
        (self.runtime / 'mcp_settings.json').write_text(json.dumps({
            'mcpServers': {}, 'systemConfig': {'routing': {'enableBearerAuth': False, 'skipAuth': False}}}))

    def test_token_is_generated_once_and_written_only_to_the_protected_settings(self):
        gateway.apply_token(self.runtime, self.store, True)
        written = gateway.settings(self.runtime)
        token = self.store.resolve('secret://' + gateway.TOKEN_SECRET)
        self.assertEqual(len(token), 64)
        self.assertTrue(written['systemConfig']['routing']['enableBearerAuth'])
        self.assertEqual([(k['id'], k['token'], k['enabled'], k['accessType']) for k in written['bearerKeys']],
                         [(gateway.TOKEN_KEY, token, True, 'all')])
        gateway.apply_token(self.runtime, self.store, True)
        self.assertEqual(self.store.resolve('secret://' + gateway.TOKEN_SECRET), token)
        self.assertEqual(len(gateway.settings(self.runtime)['bearerKeys']), 1)

    def test_turning_the_requirement_off_removes_only_the_owned_key(self):
        other = {'id': 'other', 'name': 'other', 'token': 'x', 'enabled': True, 'kind': 'system', 'accessType': 'all'}
        gateway.apply_token(self.runtime, self.store, True)
        written = gateway.settings(self.runtime)
        written['bearerKeys'].append(other)
        gateway.save(self.runtime / 'mcp_settings.json', written)
        gateway.apply_token(self.runtime, self.store, False)
        written = gateway.settings(self.runtime)
        self.assertEqual(written['bearerKeys'], [other])
        self.assertFalse(written['systemConfig']['routing']['enableBearerAuth'])

    def test_disabled_requirement_leaves_unrelated_bearer_settings_alone(self):
        written = gateway.settings(self.runtime)
        written['systemConfig']['routing']['enableBearerAuth'] = True
        gateway.save(self.runtime / 'mcp_settings.json', written)
        gateway.apply_token(self.runtime, self.store, False)
        self.assertTrue(gateway.settings(self.runtime)['systemConfig']['routing']['enableBearerAuth'])
        self.assertFalse(self.store.has(gateway.TOKEN_SECRET))

    def test_internal_clients_send_the_header_only_when_required(self):
        self.assertEqual(gateway.client_headers(self.root, None), {})
        self.assertEqual(gateway.client_headers(self.root, {'port': 1}), {})
        self.assertFalse(self.store.has(gateway.TOKEN_SECRET))
        headers = gateway.client_headers(self.root, {'require_token': True})
        self.assertEqual(headers, {'Authorization': 'Bearer ' + self.store.resolve('secret://' + gateway.TOKEN_SECRET)})

    def test_token_file_is_private_and_never_overwritten(self):
        destination = self.root / 'gateway.env'
        gateway.write_token_file(self.root, destination)
        if os.name != 'nt':
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        self.assertEqual(destination.read_text(), gateway.TOKEN_VARIABLE + '=' + self.store.resolve('secret://' + gateway.TOKEN_SECRET) + '\n')
        with self.assertRaises(FileExistsError):
            gateway.write_token_file(self.root, destination)

    def test_client_snippets_reference_an_environment_variable_only(self):
        running = {'pid': 1, 'port': 43871, 'require_token': True}
        result = connect.gateway_connection(self.root, state_reader=lambda runtime: running)
        token = gateway.token_value(self.store)
        self.assertTrue(result['tokenRequired'])
        self.assertEqual(json.loads(result['snippets']['Claude'])['mcpServers']['agentlocker-gateway']['headers'],
                         {'Authorization': 'Bearer ${AGENTLOCKER_GATEWAY_TOKEN}'})
        self.assertEqual(json.loads(result['snippets']['Cursor'])['mcpServers']['agentlocker-gateway']['headers'],
                         {'Authorization': 'Bearer ${env:AGENTLOCKER_GATEWAY_TOKEN}'})
        self.assertIn('bearer_token_env_var = "AGENTLOCKER_GATEWAY_TOKEN"', result['snippets']['Codex'])
        self.assertNotIn(token, json.dumps(result))
        opencode = connect.entries(self.root, 'opencode', result)['agentlocker-gateway']
        self.assertEqual(opencode['headers'], {'Authorization': 'Bearer {env:AGENTLOCKER_GATEWAY_TOKEN}'})
        codex = connect.entries(self.root, 'codex', result)['agentlocker-gateway']
        self.assertEqual(codex['bearer_token_env_var'], 'AGENTLOCKER_GATEWAY_TOKEN')

    def test_every_remote_client_entry_references_the_token_variable(self):
        running = {'pid': 1, 'port': 43871, 'require_token': True}
        result = connect.gateway_connection(self.root, state_reader=lambda runtime: running)
        expected = {'gemini': '${AGENTLOCKER_GATEWAY_TOKEN}', 'vscode': '${env:AGENTLOCKER_GATEWAY_TOKEN}',
                    'windsurf': '${env:AGENTLOCKER_GATEWAY_TOKEN}', 'hermes': '${AGENTLOCKER_GATEWAY_TOKEN}'}
        for agent, reference in expected.items():
            with self.subTest(agent=agent):
                entry = connect.entries(self.root, agent, result)['agentlocker-gateway']
                self.assertEqual(entry['headers'], {'Authorization': 'Bearer ' + reference})
                self.assertIn(reference, connect.render(self.root, agent, result))
        plain = connect.gateway_connection(self.root, state_reader=lambda runtime: {'pid': 1, 'port': 43871})
        for agent in expected:
            with self.subTest(agent=agent, required=False):
                self.assertNotIn('headers', connect.entries(self.root, agent, plain)['agentlocker-gateway'])

    def test_snippets_are_unchanged_when_the_token_is_not_required(self):
        result = connect.gateway_connection(self.root, state_reader=lambda runtime: {'pid': 1, 'port': 43871})
        self.assertFalse(result['tokenRequired'])
        self.assertEqual(json.loads(result['snippets']['Claude'])['mcpServers']['agentlocker-gateway'],
                         {'type': 'http', 'url': 'http://127.0.0.1:43871/mcp'})
        self.assertEqual(result['snippets']['Codex'], '[mcp_servers.agentlocker-gateway]\nurl = "http://127.0.0.1:43871/mcp"\n')


def post(url, body, headers=None):
    request = urllib.request.Request(url, json.dumps(body).encode(), {
        'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
        'MCP-Protocol-Version': '2024-11-05', **(headers or {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=15) as response:
            data = response.read().decode()
            session = response.headers.get('Mcp-Session-Id')
    except urllib.error.HTTPError as error:
        error.close()
        return error.code, None, None
    if data.startswith(('event:', 'data:')):
        data = next(line[5:].strip() for line in data.splitlines() if line.startswith('data:'))
    return 200, (json.loads(data) if data else None), session


INITIALIZE = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
    'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'guard-probe', 'version': '1'}}}


@unittest.skipUnless(shutil.which('node'), 'node is required')
class LiveGatewayGuardTests(unittest.TestCase):
    def fixture(self, *start):
        runtime = SOURCE / '.scratch/mcphub-runtime'
        if not (runtime / 'node_modules/@samanhappy/mcphub/package.json').is_file():
            self.skipTest('MCPHub 1.1.7 runtime absent; install pinned runtime for real gateway verification')
        scratch = SOURCE / '.scratch'
        scratch.mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / 'fixture'
        shutil.copytree(SOURCE, self.root, ignore=shutil.ignore_patterns(
            '.git', '.scratch', '.hub-store', '.worktrees', '__pycache__', 'config.json'))
        shutil.copytree(runtime, self.root / '.scratch/mcphub-runtime')
        self.port = free_port()
        self.addCleanup(lambda: subprocess.run([sys.executable, 'gateway.py', 'stop'], cwd=self.root,
                                               capture_output=True, timeout=60))
        self.run_gateway('prepare')
        self.run_gateway('start', '--port', str(self.port), *start)
        self.url = f'http://127.0.0.1:{self.port}/mcp'

    def run_gateway(self, *args):
        result = subprocess.run([sys.executable, 'gateway.py', *args], cwd=self.root, capture_output=True,
                                text=True, timeout=60, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def assert_legitimate_client_works(self, headers=None):
        status, response, session = post(self.url, INITIALIZE, headers)
        self.assertEqual(status, 200)
        self.assertIn('result', response)
        send = {**(headers or {}), 'Mcp-Session-Id': session}
        post(self.url, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, send)
        _, response, _ = post(self.url, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}, send)
        self.assertIn('example-server-add', [tool['name'] for tool in response['result']['tools']])
        _, response, _ = post(self.url, {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
            'name': 'example-server-add', 'arguments': {'a': 2, 'b': 3}}}, send)
        self.assertEqual(response['result']['content'][0]['text'], '5')

    def test_non_loopback_host_is_refused_while_loopback_client_works(self):
        self.fixture()
        port = self.port
        rebound = f'example.invalid:{port}'
        rpc = json.dumps(INITIALIZE).encode()
        post_headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
        self.assertEqual(request(port, '/mcp', host=rebound, method='POST', body=rpc, extra=post_headers)[0], 403)
        self.assertEqual(request(port, '/mcp', host=f'127.0.0.1:{port}', origin='http://example.invalid',
                                 method='POST', body=rpc, extra=post_headers)[0], 403)
        for path in ('/', '/mcp', '/sse', '/api/auth/login', '/health'):
            self.assertEqual(request(port, path, host=rebound)[0], 403, path)
        self.assertEqual(request(port, '/health', host=f'localhost:{port}')[0], 200)
        self.assertNotEqual(request(port, '/', host=f'127.0.0.1:{port}')[0], 200)
        self.assert_legitimate_client_works()

    def test_required_token_gates_mcp_and_the_env_file_token_works(self):
        self.fixture('--require-token')
        self.assertEqual(post(self.url, INITIALIZE)[0], 401)
        self.assertEqual(post(self.url, INITIALIZE, {'Authorization': 'Bearer ' + 'a' * 64})[0], 401)
        self.assertEqual(request(self.port, '/health', host=f'127.0.0.1:{self.port}')[0], 200)
        destination = self.root.parent / 'gateway.env'
        self.run_gateway('token', '--env-file', str(destination))
        name, _, token = destination.read_text().strip().partition('=')
        self.assertEqual(name, 'AGENTLOCKER_GATEWAY_TOKEN')
        self.assert_legitimate_client_works({'Authorization': 'Bearer ' + token})
        self.assertEqual(request(self.port, '/mcp', host=f'example.invalid:{self.port}', method='POST',
                                 body=json.dumps(INITIALIZE).encode(),
                                 extra={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})[0], 403)
        probe = subprocess.run([sys.executable, '-c', 'import hub_ui, json; print(json.dumps(hub_ui.gateway_connection(__import__("pathlib").Path(".").resolve())))'],
                               cwd=self.root, capture_output=True, text=True, timeout=60, encoding='utf-8')
        self.assertEqual(probe.returncode, 0, probe.stderr)
        connection = json.loads(probe.stdout)
        self.assertTrue(connection['tokenRequired'])
        self.assertGreater(connection['served'], 0)
        self.assertNotIn(token, probe.stdout)
        status = self.run_gateway('status')
        self.assertNotIn(token, status.stdout + status.stderr)
        log = (self.root / '.hub-store/gateway/gateway.log').read_text()
        self.assertNotIn(token, log)

    def test_restart_without_the_flag_removes_the_requirement(self):
        self.fixture('--require-token')
        self.run_gateway('stop')
        self.run_gateway('start', '--port', str(self.port))
        self.assertEqual(post(self.url, INITIALIZE)[0], 200)


if __name__ == '__main__':
    unittest.main()
