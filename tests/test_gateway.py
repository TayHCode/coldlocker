import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import gateway
import hub
import modes
import test_verify


def cli(root, *arguments):
    output, errors = io.StringIO(), io.StringIO()
    with patch.object(gateway, 'ROOT', root), patch('sys.argv', ['gateway.py', *arguments]), \
            contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        code = gateway.main()
    return code, output.getvalue(), errors.getvalue()


class SupervisorIdentityTests(unittest.TestCase):
    def cmdline(self, *argv):
        return patch.object(Path, 'read_bytes', return_value='\0'.join(argv).encode())

    @patch('sys.platform', 'linux')
    def test_is_supervisor_accepts_other_python_path(self):
        script = str(gateway.ROOT / 'gateway.py')
        with patch.object(sys, 'executable', '/usr/bin/python3'):
            for interpreter in ('/usr/bin/python3.14', 'python3', '/venv/bin/python', '/usr/bin/python3'):
                with self.subTest(interpreter=interpreter), self.cmdline(interpreter, script, '_run', '--port', '43961'):
                    self.assertTrue(gateway.is_supervisor(4242))

    @patch('sys.platform', 'linux')
    def test_is_supervisor_refuses_other_scripts_and_launchers(self):
        script = str(gateway.ROOT / 'gateway.py')
        for argv in (('/usr/bin/python3', '/elsewhere/gateway.py', '_run'),
                     ('/bin/sh', script, '_run'),
                     ('/usr/bin/python3', script, 'status'),
                     ('/usr/bin/python3', '-c', script, '_run'),
                     ('/usr/bin/python3', script)):
            with self.subTest(argv=argv), self.cmdline(*argv):
                self.assertFalse(gateway.is_supervisor(4242))


class StopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.running = True
        for name, value in (('sys.platform', modes.GATEWAY_PLATFORM),
                            ('gateway.state', lambda runtime: {'pid': 4242, 'port': 43961} if self.running else None),
                            ('gateway.os.kill', lambda pid, signum: setattr(self, 'running', False))):
            patcher = patch(name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_stop_fails_naming_the_port_when_it_keeps_listening(self):
        with patch('gateway.listeners', return_value=[('127.0.0.1:43961', (999,))]):
            code, output, errors = cli(self.root, 'stop')
        self.assertEqual(code, 1)
        self.assertIn('43961', errors)
        self.assertNotIn('complete', output)

    def test_stop_succeeds_when_the_port_is_released(self):
        with patch('gateway.listeners', return_value=[]):
            code, output, errors = cli(self.root, 'stop')
        self.assertEqual((code, output, errors), (0, 'gateway stop complete\n', ''))


class ReportTests(unittest.TestCase):
    setUp = test_verify.VerifyTests.setUp

    def add_server(self, name):
        path = self.root / f'mcp/servers/{name}.json'
        path.write_text(json.dumps({**self.definition, 'name': name}), encoding='utf-8')
        manifest = json.loads((self.root / 'manifest.json').read_text(encoding='utf-8'))
        hub.record(manifest, 'mcp_servers', name, path, 'agentlocker', 'authored', root=self.root)
        (self.root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    def running(self, tools):
        (self.root / 'config.json').unlink()
        runtime = self.root / '.hub-store/gateway'
        runtime.mkdir(parents=True)
        (runtime / 'pid.json').write_text('{}', encoding='utf-8')
        (runtime / 'mcp_settings.json').write_text('{}', encoding='utf-8')
        for name in ('docs', 'fetcher'):
            self.add_server(name)
        self.http.tools = [{'name': tool} for tool in tools]
        for name, value in (('sys.platform', modes.GATEWAY_PLATFORM),
                            ('gateway.state', lambda path: {'pid': 4242, 'port': self.http.server_port}),
                            ('gateway.start', lambda *arguments: None)):
            patcher = patch(name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_status_reports_failed_upstreams(self):
        self.running(['demo-add'])
        code, output, errors = cli(self.root, 'status')
        self.assertEqual(code, 0)
        self.assertEqual(output.splitlines(), ['running', f'url: http://127.0.0.1:{self.http.server_port}/mcp',
                                                'servers: 1/3 connected; failed: docs, fetcher'])

    def test_start_reports_failed_upstreams(self):
        self.running(['demo-add'])
        code, output, errors = cli(self.root, 'start')
        self.assertEqual(code, 0)
        self.assertEqual(output.splitlines(), ['gateway start complete', 'servers: 1/3 connected; failed: docs, fetcher'])

    def test_status_with_every_upstream_connected_lists_no_failures(self):
        self.running(['demo-add', 'docs-read', 'fetcher-get'])
        self.assertEqual(cli(self.root, 'status')[1].splitlines()[-1], 'servers: 3/3 connected')

    def test_status_says_so_when_the_gateway_does_not_answer(self):
        self.running([])
        self.http.shutdown()
        self.http.server_close()
        self.assertEqual(cli(self.root, 'status')[1].splitlines()[-1], 'servers: unknown')


class OptionTests(unittest.TestCase):
    def test_refresh_timing_flags_are_gone(self):
        for flag in ('--refresh-lead', '--refresh-interval'):
            with self.subTest(flag=flag), patch('sys.argv', ['gateway.py', 'start', flag, '5']), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    gateway.main()

    def test_port_and_auto_refresh_remain(self):
        with tempfile.TemporaryDirectory() as directory, patch('sys.platform', modes.GATEWAY_PLATFORM), \
                patch('gateway.start') as start:
            self.assertEqual(cli(Path(directory), 'start', '--port', '43962', '--auto-refresh')[0], 0)
        self.assertEqual(start.call_args.args[1:], (43962, True))


if __name__ == '__main__':
    unittest.main()
