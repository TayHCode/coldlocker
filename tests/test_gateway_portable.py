import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import gateway
from gateway_adapter import save


ROOT = Path(__file__).resolve().parents[1]
STARTED = 'Mon Oct 20 10:00:00 2026'


def ps_output(table):
    def run(argv, **kwargs):
        field = argv[argv.index('-o') + 1].rstrip('=')
        pid = int(argv[argv.index('-p') + 1])
        value = table.get((pid, field), '')
        return subprocess.CompletedProcess(argv, 0 if value else 1, value + '\n' if value else '', '')
    return run


class PlatformGateTests(unittest.TestCase):
    def run_main(self, command):
        errors = io.StringIO()
        with patch.object(sys, 'argv', ['gateway.py', command]), patch.object(sys, 'platform', 'win32'), \
                patch('gateway.check_node', side_effect=AssertionError('reached install work')), \
                patch('gateway.subprocess.Popen', side_effect=AssertionError('launched')), \
                contextlib.redirect_stderr(errors):
            return gateway.main(), errors.getvalue()

    def test_unsupported_platform_names_the_stdio_route_for_install_and_start(self):
        for command in ('install', 'start'):
            with self.subTest(command=command):
                code, message = self.run_main(command)
                self.assertEqual(code, 1)
                self.assertEqual(len(message.strip().splitlines()), 1)
                self.assertIn('stdio', message)
                self.assertIn('hub.py connect', message)
                self.assertNotIn('is required', message)

    def test_gateway_module_imports_without_fcntl(self):
        code = f"import sys; sys.path.insert(0, {str(ROOT)!r}); sys.modules['fcntl'] = None; import gateway"
        result = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)


class DarwinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=self.scratch())
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name)
        platform = patch.object(sys, 'platform', 'darwin')
        platform.start()
        self.addCleanup(platform.stop)

    @staticmethod
    def scratch():
        path = ROOT / '.scratch/test-root'
        path.mkdir(parents=True, exist_ok=True)
        return path

    def supervisor_line(self):
        return ' '.join([sys.executable, str(gateway.ROOT / 'gateway.py'), '_run', '--port', '43871'])

    def test_identity_is_the_ps_start_time_and_none_for_a_dead_pid(self):
        table = {(4242, 'lstart'): STARTED}
        with patch('gateway.subprocess.run', side_effect=ps_output(table)):
            self.assertEqual(gateway.identity(4242), STARTED)
            self.assertIsNone(gateway.identity(4243))

    def test_state_accepts_only_the_recorded_start_time_and_supervisor_command(self):
        save(self.runtime / 'pid.json', {'pid': 4242, 'identity': STARTED, 'port': 43871})
        table = {(4242, 'lstart'): STARTED, (4242, 'args'): self.supervisor_line()}
        with patch('gateway.subprocess.run', side_effect=ps_output(table)):
            self.assertEqual(gateway.state(self.runtime)['pid'], 4242)
        reused = {**table, (4242, 'lstart'): 'Tue Oct 21 11:00:00 2026'}
        with patch('gateway.subprocess.run', side_effect=ps_output(reused)):
            self.assertIsNone(gateway.state(self.runtime))
        foreign = {**table, (4242, 'args'): '/usr/bin/python3 other.py _run'}
        with patch('gateway.subprocess.run', side_effect=ps_output(foreign)):
            self.assertIsNone(gateway.state(self.runtime))
        with patch('gateway.subprocess.run', side_effect=ps_output({})):
            self.assertIsNone(gateway.state(self.runtime))

    def test_state_accepts_the_framework_python_reexec_path(self):
        save(self.runtime / 'pid.json', {'pid': 4242, 'identity': STARTED, 'port': 43871})
        framework = '/opt/homebrew/Cellar/python@3.14/3.14.4/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python'
        line = ' '.join([framework, str(gateway.ROOT / 'gateway.py'), '_run', '--port', '43871'])
        table = {(4242, 'lstart'): STARTED, (4242, 'args'): line}
        with patch('gateway.subprocess.run', side_effect=ps_output(table)):
            self.assertEqual(gateway.state(self.runtime)['pid'], 4242)

    def test_state_refuses_another_script_or_a_non_python_launcher(self):
        save(self.runtime / 'pid.json', {'pid': 4242, 'identity': STARTED, 'port': 43871})
        script = str(gateway.ROOT / 'gateway.py')
        for line in ('/opt/homebrew/bin/python3.14 /elsewhere/gateway.py _run --port 43871',
                     '/bin/sh ' + script + ' _run --port 43871',
                     '/usr/bin/python3 ' + script + ' status',
                     'python3 -c ' + script + ' _run'):
            with self.subTest(line=line):
                table = {(4242, 'lstart'): STARTED, (4242, 'args'): line}
                with patch('gateway.subprocess.run', side_effect=ps_output(table)):
                    self.assertIsNone(gateway.state(self.runtime))

    def test_state_refuses_a_command_that_only_shares_a_prefix(self):
        save(self.runtime / 'pid.json', {'pid': 4242, 'identity': STARTED, 'port': 43871})
        table = {(4242, 'lstart'): STARTED, (4242, 'args'): self.supervisor_line().replace('_run', '_runner')}
        with patch('gateway.subprocess.run', side_effect=ps_output(table)):
            self.assertIsNone(gateway.state(self.runtime))

    def test_listeners_come_from_lsof(self):
        output = 'p777\nn127.0.0.1:43871\n'
        with patch('gateway.subprocess.run', return_value=subprocess.CompletedProcess([], 0, output, '')) as run:
            self.assertEqual(gateway.listeners(43871), [('127.0.0.1:43871', (777,))])
        self.assertEqual(run.call_args.args[0][0], 'lsof')
        with patch('gateway.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', '')):
            self.assertEqual(gateway.listeners(43871), [])

    def test_start_requires_lsof_instead_of_ss(self):
        (self.runtime / 'mcp_settings.json').touch()
        wanted = []

        def which(name):
            wanted.append(name)
            return None if name == 'lsof' else '/bin/tool'

        with patch('gateway.check_node'), patch('gateway.check_runtime'), patch('gateway.shutil.which', side_effect=which):
            with self.assertRaisesRegex(gateway.SetupError, 'lsof is required'):
                gateway.start(self.runtime, 43871)
        self.assertNotIn('ss', wanted)


class LinuxTests(unittest.TestCase):
    def test_listeners_parse_ss_with_owning_pids(self):
        output = 'LISTEN 0 511 127.0.0.1:43871 0.0.0.0:* users:(("node",pid=555,fd=19))\n'
        with patch.object(sys, 'platform', 'linux'), \
                patch('gateway.subprocess.run', return_value=subprocess.CompletedProcess([], 0, output, '')) as run:
            self.assertEqual(gateway.listeners(43871), [('127.0.0.1:43871', (555,))])
        self.assertEqual(run.call_args.args[0][0], 'ss')

    def test_identity_still_reads_proc_on_linux(self):
        stat = '4242 (py thon) S ' + ' '.join(str(number) for number in range(1, 19)) + ' 98765 0\n'
        with patch.object(sys, 'platform', 'linux'), patch.object(Path, 'read_text', return_value=stat) as read, \
                patch('gateway.subprocess.run') as run:
            self.assertEqual(gateway.identity(4242), '98765')
        read.assert_called_once()
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
