import contextlib
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import executor_connector as connector
import executor_sources as sources
import gateway
import hub
import modes
from hub_ui import gateway_connection
from store import FileStore


ROOT = Path(__file__).resolve().parents[1]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, value, code=200, headers=None):
        data = json.dumps(value).encode()
        if self.server.sse and code == 200:
            data = b'event: message\ndata: ' + data + b'\n\n'
        self.send_response(code)
        self.send_header('Content-Type', 'text/event-stream' if self.server.sse else 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Mcp-Session-Id', 'fixture-session')
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def do_DELETE(self):
        self.server.deleted += 1
        self.reply({})

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append((request, dict(self.headers)))
        if self.server.redirect:
            self.reply({}, 307, {'Location': self.server.redirect})
            return
        if self.headers.get('Authorization') != self.server.authorization:
            self.reply({}, 401)
            return
        method = request['method']
        params = request.get('params', {})
        if method == 'initialize':
            result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}}}
        elif method == 'notifications/initialized':
            self.reply({}, 202)
            return
        elif method == 'tools/list':
            result = {'tools': [{'name': 'execute', 'inputSchema': {'type': 'object'}}]}
            if self.server.paginate:
                if params.get('cursor'):
                    result = {'tools': [{'name': 'skills'}]}
                else:
                    result['nextCursor'] = 'next-page'
            if self.server.repeat:
                result['nextCursor'] = 'next-page'
        else:
            result = self.server.call(params)
        self.reply({'jsonrpc': '2.0', 'id': request['id'], 'result': result})


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.scratch/test-executor'
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.requests = []
        self.server.deleted = 0
        self.server.sse = False
        self.server.paginate = False
        self.server.repeat = False
        self.server.redirect = None
        self.server.authorization = 'synthetic-endpoint-value'
        self.server.call = lambda params: {'content': [{'type': 'text', 'text': '5'}]}
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = 'http://127.0.0.1:' + str(self.server.server_port) + '/mcp'
        self.config = {'gateway': {'backend': 'executor', 'executor': {
            'url': self.url, 'headers': {'Authorization': 'secret://executor-endpoint'}}}}
        self.save_config()
        FileStore(self.root).put('executor-endpoint', self.server.authorization)

    def save_config(self):
        (self.root / 'config.json').write_text(json.dumps(self.config), encoding='utf-8')

    def cli(self, command, *flags):
        output = io.StringIO()
        with patch.object(gateway, 'ROOT', self.root), patch('sys.argv', ['gateway.py', command, *flags]), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = gateway.main()
        return code, output.getvalue()

    def test_default_backend_needs_no_store_or_runtime(self):
        (self.root / 'config.json').unlink()
        self.assertEqual(connector.configuration(self.root), {'backend': 'mcphub'})
        self.assertFalse((self.root / '.hub-store/gateway').exists())

    def test_unknown_backend_and_invalid_config_fail(self):
        for value in ([], {'gateway': {'backend': 'other'}}, {'gateway': []}, {'gateway': {'backend': 'executor', 'executor': []}}):
            self.config = value
            self.save_config()
            with self.assertRaises(ValueError):
                connector.configuration(self.root)

    def test_only_numeric_loopback_without_credentials_queries_or_redirects(self):
        host = '.'.join(('192', '0', '2', '1'))
        for value in ('http://' + host + ':9000/mcp', 'http://example.invalid:9000/mcp',
                      'file:///mcp', self.url + '?token=fixture', self.url + '#fragment',
                      self.url.replace('://', '://user:pass@'), self.url + '\n'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                connector.endpoint_url(value)
        self.assertEqual(connector.endpoint_url('http://[::1]:4788/mcp'), 'http://[::1]:4788/mcp')
        self.server.redirect = self.url
        self.assertEqual(connector.status(self.root)['status'], 'unreachable')
        self.assertEqual(len(self.server.requests), 1)

    def test_header_values_require_references_and_protected_names_refused(self):
        for headers in ({'Authorization': 'literal'}, {'Host': 'secret://executor-endpoint'},
                        {'bad\nname': 'secret://executor-endpoint'}, {'Accept': 'secret://executor-endpoint'}):
            self.config['gateway']['executor']['headers'] = headers
            self.save_config()
            with self.assertRaises(ValueError):
                connector.configuration(self.root)

    def test_missing_secret_and_newline_value_fail_closed(self):
        FileStore(self.root).delete('executor-endpoint')
        self.assertEqual(connector.status(self.root)['status'], 'unreachable')
        FileStore(self.root).put('executor-endpoint', 'fixture\nheader')
        self.assertEqual(connector.status(self.root)['status'], 'unreachable')
        self.assertEqual(self.server.requests, [])

    def test_status_initializes_lists_and_closes_session(self):
        result = connector.status(self.root)
        self.assertEqual(result['tools'], ['execute'])
        self.assertEqual(result['status'], 'running')
        self.assertEqual(self.server.deleted, 1)
        self.assertEqual([r['method'] for r, h in self.server.requests], ['initialize', 'notifications/initialized', 'tools/list'])
        self.assertEqual(self.server.requests[-1][1]['Mcp-Session-Id'], 'fixture-session')
        self.assertNotIn(self.server.authorization, json.dumps(result))

    def test_sse_pagination_and_call(self):
        self.server.sse = True
        self.server.paginate = True
        with connector.Session(self.root) as session:
            self.assertEqual([t['name'] for t in session.tools()], ['execute', 'skills'])
            result = session.exchange('tools/call', {'name': 'execute', 'arguments': {}})
            self.assertEqual(result['content'][0]['text'], '5')

    def test_repeated_cursor_is_refused(self):
        self.server.repeat = True
        self.assertEqual(connector.status(self.root)['status'], 'unreachable')

    def test_wrong_endpoint_auth_reports_unreachable_without_secret(self):
        self.server.authorization = 'different-value'
        code, output = self.cli('status')
        self.assertEqual(code, 1)
        self.assertIn('unreachable', output)
        self.assertNotIn('synthetic-endpoint-value', output)

    def test_executor_lifecycle_commands_do_not_mutate_or_launch(self):
        before = sorted(p.relative_to(self.root) for p in self.root.rglob('*'))
        with patch('gateway.subprocess.Popen') as launch, patch('gateway.install') as install:
            for name in ('start', 'stop', 'prepare', 'sync', 'refresh', 'install'):
                code, output = self.cli(name)
                self.assertEqual(code, 0)
                self.assertIn('user-managed', output)
            launch.assert_not_called()
            install.assert_not_called()
        self.assertEqual(before, sorted(p.relative_to(self.root) for p in self.root.rglob('*')))
        self.assertEqual(self.server.requests, [])

    def test_url_and_status_use_selected_backend(self):
        self.assertEqual(self.cli('url'), (0, self.url + '\n'))
        code, output = self.cli('status')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['backend'], 'executor')

    @patch('sys.platform', modes.GATEWAY_PLATFORM)
    def test_mcphub_status_and_url_keep_existing_state_path(self):
        self.config = {'gateway': {'backend': 'mcphub'}}
        self.save_config()
        with patch('gateway.state', return_value={'port': 43871}):
            code, output = self.cli('status')
            self.assertEqual(code, 0)
            self.assertEqual(output.splitlines()[:2], ['running', 'url: http://127.0.0.1:43871/mcp'])
            self.assertIn(':43871/mcp', self.cli('url')[1])
        with patch('gateway.state', return_value=None):
            self.assertEqual(self.cli('status'), (1, 'stopped\n'))
            self.assertEqual(self.cli('url')[0], 1)

    def test_wizard_uses_executor_endpoint_and_env_headers(self):
        result = gateway_connection(self.root)
        self.assertEqual(result['url'], self.url)
        self.assertEqual(result['status'], 'running')
        self.assertNotIn(self.server.authorization, json.dumps(result))
        self.assertIn('AGENTLOCKER_EXECUTOR_AUTHORIZATION', result['snippets']['Claude'])
        self.assertIn('env_http_headers', result['snippets']['Codex'])

    def test_wizard_no_auth_snippets_and_unreachable_state(self):
        self.config['gateway']['executor']['headers'] = {}
        self.server.authorization = None
        self.save_config()
        result = gateway_connection(self.root)
        entry = json.loads(result['snippets']['Cursor'])['mcpServers']['agentlocker-gateway']
        self.assertEqual(entry, {'url': self.url})
        self.server.redirect = self.url
        self.assertEqual(gateway_connection(self.root)['snippets'], {})

    def test_config_symlink_refused(self):
        (self.root / 'config.json').rename(self.root / 'other.json')
        (self.root / 'config.json').symlink_to('other.json')
        with self.assertRaises(ValueError):
            connector.configuration(self.root)

    def integration(self):
        return {'slug': 'fixture-source', 'kind': 'mcp', 'authMethods': [{'kind': 'none'}],
                'config': {'transport': 'stdio', 'command': 'node', 'args': ['server.py']}}

    def test_optional_copy_preserves_refs_and_omits_auth_metadata(self):
        integration = self.integration()
        integration['config']['env'] = {'API_TOKEN': 'secret://existing-reference'}
        definition = sources.nonsecret_definition(integration)
        self.assertEqual(definition['env']['API_TOKEN'], 'secret://existing-reference')
        self.assertNotIn('authMethods', definition)

    def test_optional_copy_refuses_credentials_auth_paths_and_queries(self):
        for change in ({'env': {'API_TOKEN': 'synthetic-private-value'}},
                       {'args': ['/' + 'home' + '/fixture/server.py']}, {'cwd': '/fixture'},
                       {'envVars': ['API_TOKEN']}, {'transport': 'remote', 'endpoint': 'https://example.invalid/mcp?secret=fixture'}):
            integration = self.integration()
            integration['config'].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                sources.nonsecret_definition(integration)
        integration = self.integration()
        integration['authMethods'] = [{'kind': 'oauth2'}]
        with self.assertRaises(ValueError):
            sources.nonsecret_definition(integration)

    def test_optional_copy_writes_provenance_and_preserves_existing(self):
        manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
        manifest['mcp_servers'] = []
        (self.root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        args = type('Args', (), {'slug': 'fixture-source', 'apply': False})()
        with patch.object(hub, 'ROOT', self.root), patch.object(sources, 'invoke', return_value={'integration': self.integration()}), contextlib.redirect_stdout(io.StringIO()):
            sources.import_source(args)
            self.assertFalse((self.root / '.hub-store/catalog/mcp').exists())
            args.apply = True
            sources.import_source(args)
            entry = json.loads((self.root / '.hub-store/catalog/manifest.json').read_text(encoding='utf-8'))['mcp_servers'][0]
            self.assertEqual(entry['provenance']['origin'], 'executor-copy')
            with self.assertRaises(ValueError):
                sources.import_source(args)

    def prepare_manifest(self):
        (self.root / 'manifest.json').write_bytes((ROOT / 'manifest.json').read_bytes())
        (self.root / 'mcp/servers').mkdir(parents=True)
        self.definition = json.loads((ROOT / 'mcp/servers/example-server.json').read_text(encoding='utf-8'))
        self.definition['command'] = 'node'
        self.definition['args'] = ['server.py']
        (self.root / 'mcp/servers/example-server.json').write_text(json.dumps(self.definition), encoding='utf-8')

    def test_registration_preview_is_offline(self):
        self.prepare_manifest()
        with patch.object(sources, 'Session') as session, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sources.registration(self.root, 'example-server'), 0)
            session.assert_not_called()

    def test_registration_refuses_store_bindings(self):
        self.prepare_manifest()
        self.definition['env'] = {'API_TOKEN': 'secret://example'}
        (self.root / 'mcp/servers/example-server.json').write_text(json.dumps(self.definition), encoding='utf-8')
        with self.assertRaises(ValueError):
            sources.registration(self.root, 'example-server', True)

    def test_registration_is_idempotent_and_preserves_conflicting_sources(self):
        self.prepare_manifest()
        calls = []
        config = {'transport': 'stdio', 'command': 'node', 'args': ['server.py']}
        def invoke(session, path, arguments, approve=False):
            calls.append(path)
            if path.endswith('integrations.list'):
                return {'integrations': [{'slug': 'locker-example-server'}]}
            if path.endswith('getServer'):
                return {'integration': {'kind': 'mcp', 'config': config, 'authMethods': [{'kind': 'none'}]}}
            return {'connections': [{'integration': 'locker-example-server', 'owner': 'user', 'name': 'agentlocker', 'template': 'none'}]}
        with patch.object(sources, 'invoke', side_effect=invoke), contextlib.redirect_stdout(io.StringIO()):
            sources.registration(self.root, 'example-server', True)
            self.assertFalse(any(p.endswith(('addServer', 'create')) for p in calls))
            config['command'] = 'unfamiliar'
            with self.assertRaises(ValueError):
                sources.registration(self.root, 'example-server', True)

    def test_invoke_approves_only_exact_empty_form_for_requested_write(self):
        class Fake:
            def __init__(self, address):
                self.address = address
                self.calls = []
            def exchange(self, method, params):
                self.calls.append(params)
                if len(self.calls) == 1:
                    return {'structuredContent': {'status': 'waiting_for_interaction', 'executionId': 'fixture',
                        'interaction': {'address': self.address, 'args': {}, 'requestedSchema': {'properties': {}}}}}
                return {'structuredContent': {'status': 'completed', 'result': {'ok': True, 'data': {}}}}
        for approve, address in ((False, 'executor.mcp.addServer'), (True, 'unrelated')):
            session = Fake(address)
            with self.assertRaises(ValueError):
                sources.invoke(session, 'executor.mcp.addServer', {}, approve)
            self.assertEqual(len(session.calls), 1)
        session = Fake('executor.mcp.addServer')
        self.assertEqual(sources.invoke(session, 'executor.mcp.addServer', {}, True), {})
        self.assertEqual(session.calls[1]['name'], 'resume')

    def test_malformed_tools_and_protocol_fail_closed(self):
        with patch.object(connector.Session, 'exchange', return_value={'protocolVersion': []}):
            self.assertEqual(connector.status(self.root)['status'], 'unreachable')
        with patch.object(connector.Session, 'exchange', return_value={'tools': {}}):
            with self.assertRaises(ValueError):
                connector.Session(self.root).tools()

    def test_non_sensitive_header_literals_are_also_refused_on_copy(self):
        integration = self.integration()
        integration['config']['env'] = {'CUSTOM_VALUE': 'synthetic-private-value'}
        with self.assertRaises(ValueError):
            sources.nonsecret_definition(integration)

    def test_status_redacts_endpoint_credential_echoed_as_tool_name(self):
        with patch.object(connector.Session, 'tools', return_value=[{'name': self.server.authorization}]):
            self.assertNotIn(self.server.authorization, json.dumps(connector.status(self.root)))
