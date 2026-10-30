from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import modes
import calllog
import executor_connector
from store import FileStore


ROOT = Path(__file__).resolve().parents[1]
GATEWAY_LINE = 'Handling MCP post request for sessionId: fixture and group: undefined with body: '


class Recorder(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(request)
        data = json.dumps({'jsonrpc': '2.0', 'id': request.get('id'), 'result': {
            'protocolVersion': '2024-11-05', 'tools': []}}).encode()
        self.send_response(202 if 'id' not in request else 200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_DELETE(self):
        self.send_response(200)
        self.send_header('Content-Length', '0')
        self.end_headers()


class CallLogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_stdio_requests_client_first_call_and_no_arguments(self):
        for name in ('mcp_endpoint.py', 'calllog.py', 'store.py', 'portable.py'):
            shutil.copyfile(ROOT / name, self.root / name)
        canary = 'fixture-private-argument'
        requests = [{'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'fixture-agent'}}},
                    {'method': 'notifications/initialized'},
                    {'id': 2, 'method': 'tools/list'},
                    {'id': 3, 'method': 'tools/call', 'params': {'name': 'hub_list_skills', 'arguments': {'value': canary}}}]
        result = subprocess.run([sys.executable, str(self.root / 'mcp_endpoint.py')],
                                input=''.join(json.dumps(r) + '\n' for r in requests), capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        status = calllog.read(self.root)
        self.assertEqual(len(status['calls']), 4)
        self.assertFalse(status['waiting'])
        self.assertEqual(status['first_call']['fixture-agent']['method'], 'initialize')
        self.assertEqual(status['calls'][-1]['tool'], 'hub_list_skills')
        self.assertEqual(len(calllog.read(self.root, status['calls'][1]['ts'])['calls']), 2)
        for path in (self.root / '.hub-store').iterdir():
            modes.check(self, path, 0o600)
            self.assertNotIn(canary, path.read_text(encoding='utf-8'))
            self.assertNotIn('arguments', path.read_text(encoding='utf-8'))

    def test_rotation_preserves_first_call_and_permissions(self):
        with patch.object(calllog, 'MAX_BYTES', 1):
            for method in ('initialize', 'tools/list', 'tools/call'):
                calllog.append(self.root, 'fixture-agent', method, None, 1)
        result = calllog.read(self.root)
        self.assertEqual(len(result['calls']), 2)
        self.assertEqual(result['first_call']['fixture-agent']['method'], 'initialize')
        modes.check(self, self.root / '.hub-store/calls.jsonl.1', 0o600)

    def test_redaction_waiting_and_symlink_refusal(self):
        self.assertTrue(calllog.read(self.root)['waiting'])
        FileStore(self.root).put('canary', 'private-fixture-value')
        calllog.append(self.root, 'private-fixture-value', 'tools/call', 'private-fixture-value', 2)
        self.assertNotIn('private-fixture-value', json.dumps(calllog.read(self.root)))
        log = self.root / '.hub-store/calls.jsonl'
        log.rename(log.with_suffix('.backup'))
        target = self.root / 'unrelated'
        target.write_text('preserve', encoding='utf-8')
        log.symlink_to(target)
        with self.assertRaises(ValueError):
            calllog.append(self.root, 'agent', 'ping', None, 1)
        self.assertEqual(target.read_text(encoding='utf-8'), 'preserve')

    def test_gateway_metadata_has_no_payload_or_invented_timing(self):
        line = 'Handling MCP post request for sessionId: fixture and group: undefined with body: '
        calllog.gateway_request(self.root, line + json.dumps({'method': 'tools/call', 'params': {
            'name': 'demo-add', 'arguments': {'private': 'private-fixture-value'}}}))
        record = calllog.read(self.root)['calls'][0]
        self.assertEqual(record['source'], 'gateway')
        self.assertEqual(record['client'], 'unknown')
        self.assertIsNone(record['duration_ms'])
        self.assertNotIn('private-fixture-value', json.dumps(record))

    def gateway(self, session, body):
        calllog.gateway_request(self.root, 'Handling MCP post request for sessionId: ' + session
                                + ' and group: undefined with body: ' + json.dumps(body))

    def created(self, session):
        calllog.gateway_request(self.root, '[SESSION NEW] Creating new session with ID: ' + session)

    def initialize(self, name):
        self.gateway('undefined', {'method': 'initialize', 'params': {'clientInfo': {'name': name}}})

    def clients(self):
        return [c['client'] for c in calllog.read(self.root)['calls']]

    def test_gateway_requests_inherit_the_client_of_their_session(self):
        self.initialize('agent-one')
        self.created('session-one')
        self.initialize('agent-two')
        self.created('session-two')
        self.gateway('session-one', {'method': 'tools/list'})
        self.gateway('session-two', {'method': 'tools/call', 'params': {'name': 'hub_list_skills'}})
        self.gateway('session-one', {'method': 'tools/call', 'params': {'name': 'hub_list_rules'}})
        self.gateway('never-seen', {'method': 'tools/list'})
        self.gateway('session-one', {'method': 'tools/list', 'params': {'_meta': {'agentlocker': 'setup check'}}})
        result = calllog.read(self.root)
        calls = result['calls']
        self.assertEqual([c['client'] for c in calls], ['agent-one', 'agent-two', 'agent-one', 'agent-two',
                                                        'agent-one', 'unknown'])
        self.assertEqual(result['setup_checks'], 1)
        self.assertEqual(calls[3]['tool'], 'hub_list_skills')
        stored = (self.root / '.hub-store/calls-sessions.json').read_text(encoding='utf-8')
        for private in ('hub_list_skills', 'session-one', 'session-two'):
            self.assertNotIn(private, stored)
        modes.check(self, self.root / '.hub-store/calls-sessions.json', 0o600)

    def test_stateless_requests_are_never_credited_to_the_last_initialize(self):
        self.initialize('agent-one')
        self.created('session-one')
        self.gateway('undefined', {'method': 'tools/call', 'params': {'name': 'hub_list_skills'}})
        self.gateway('undefined', {'method': 'tools/list'})
        self.assertEqual(self.clients(), ['agent-one', 'unknown', 'unknown'])

    def test_interleaved_initializes_leave_both_sessions_unknown(self):
        self.initialize('agent-one')
        self.initialize('agent-two')
        self.created('session-x')
        self.created('session-y')
        self.gateway('session-x', {'method': 'tools/list'})
        self.gateway('session-y', {'method': 'tools/list'})
        self.assertEqual(self.clients()[2:], ['unknown', 'unknown'])
        self.initialize('agent-three')
        self.created('session-z')
        self.gateway('session-z', {'method': 'tools/list'})
        self.assertEqual(self.clients()[-1], 'agent-three')

    def test_a_stale_initialize_costs_one_attribution_and_state_recovers(self):
        self.initialize('stale-agent')
        self.initialize('agent-one')
        self.created('session-one')
        self.gateway('session-one', {'method': 'tools/list'})
        self.initialize('agent-two')
        self.created('session-two')
        self.gateway('session-two', {'method': 'tools/list'})
        self.assertEqual(self.clients(), ['stale-agent', 'agent-one', 'unknown', 'agent-two', 'agent-two'])

    def test_session_created_without_an_initialize_binds_nothing(self):
        self.created('session-one')
        self.gateway('session-one', {'method': 'tools/list'})
        self.assertEqual(self.clients(), ['unknown'])

    def test_session_clients_survive_a_new_process_and_stay_bounded(self):
        self.initialize('agent-one')
        self.created('kept')
        for name in ('calllog.py', 'store.py', 'portable.py'):
            shutil.copyfile(ROOT / name, self.root / name)
        script = 'import sys, pathlib, calllog; calllog.gateway_request(pathlib.Path(sys.argv[1]), sys.argv[2])'
        subprocess.run([sys.executable, '-c', script, str(self.root),
                        'Handling MCP post request for sessionId: kept and group: undefined with body: '
                        + json.dumps({'method': 'tools/list'})], cwd=self.root, check=True)
        self.assertEqual(self.clients(), ['agent-one', 'agent-one'])
        for number in range(calllog.SESSION_LIMIT + 5):
            self.initialize('bulk')
            self.created('bulk-%d' % number)
        sessions = json.loads((self.root / '.hub-store/calls-sessions.json').read_text(encoding='utf-8'))
        self.assertEqual(len(sessions['sessions']), calllog.SESSION_LIMIT)
        self.gateway('kept', {'method': 'tools/list'})
        self.assertEqual(self.clients()[-1], 'unknown')

    def test_setup_check_probes_are_labelled_and_never_first_call(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Recorder)
        server.requests = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        config = {'backend': 'executor', 'url': 'http://127.0.0.1:' + str(server.server_port) + '/mcp', 'headers': {}}
        with executor_connector.Session(self.root, config=config) as session:
            session.tools()
        self.assertEqual([r['method'] for r in server.requests], ['initialize', 'notifications/initialized', 'tools/list'])
        for request in server.requests:
            calllog.gateway_request(self.root, GATEWAY_LINE + json.dumps(request))
        result = calllog.read(self.root)
        self.assertEqual(result['calls'], [])
        self.assertEqual(result['setup_checks'], 3)
        self.assertEqual(result['first_call'], {})
        self.assertTrue(result['waiting'])
        calllog.gateway_request(self.root, GATEWAY_LINE + json.dumps({'method': 'initialize', 'params': {
            'clientInfo': {'name': 'fixture-agent'}}}))
        result = calllog.read(self.root)
        self.assertEqual(list(result['first_call']), ['fixture-agent'])
        self.assertFalse(result['waiting'])

    def test_read_returns_only_the_latest_calls_and_counts_older(self):
        for number in range(25):
            calllog.append(self.root, 'fixture-agent', 'tools/list', None, number)
        result = calllog.read(self.root)
        self.assertEqual(len(result['calls']), 20)
        self.assertEqual(result['older'], 5)
        self.assertEqual(result['calls'][-1]['duration_ms'], 24)
        self.assertEqual(result['calls'][0]['duration_ms'], 5)
        self.assertEqual(calllog.read(self.root, result['calls'][-1]['ts'])['older'], 0)

    def test_setup_checks_never_push_real_calls_out_of_the_list(self):
        for client in ('claude-code-probe', 'codex-probe'):
            calllog.append(self.root, client, 'initialize', None, 1)
        for number in range(20):
            calllog.append(self.root, calllog.SETUP_CHECK, 'tools/list', None, number)
        result = calllog.read(self.root)
        self.assertEqual([c['client'] for c in result['calls']], ['claude-code-probe', 'codex-probe'])
        self.assertEqual((result['older'], result['setup_checks']), (0, 20))
        for number in range(25):
            calllog.append(self.root, 'fixture-agent', 'tools/list', None, number)
        result = calllog.read(self.root)
        self.assertEqual((len(result['calls']), result['older'], result['setup_checks']), (20, 7, 20))

    def test_invalid_cursors(self):
        for value in ('nan', 'inf', '-1', 'invalid'):
            with self.assertRaises(ValueError):
                calllog.read(self.root, value)
