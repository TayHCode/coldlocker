from argparse import Namespace
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from checks import validators
from hub import parser
import routine_enable as enable
import routine_runtime as runtime


ROOT = Path(__file__).resolve().parents[1]


class TaskSchedulerStub:
    def __init__(self):
        self.tasks = {'\\foreign-task': '<Task />'}
        self.calls = []
        self.fail = None

    def run(self, argv, **kwargs):
        if not isinstance(argv, list) or argv[0] != 'schtasks.exe' or kwargs.get('shell'):
            raise AssertionError('only shell-free schtasks argument lists are allowed')
        self.calls.append(argv)
        operation = argv[1]
        task = argv[argv.index('/TN') + 1]
        text = kwargs.get('text', False)
        code, output = 0, ''
        if operation == self.fail:
            code = 0x80070005
        elif operation == '/Query':
            if task in self.tasks:
                output = self.tasks[task]
            else:
                code = 0x80070002
        elif operation == '/Create':
            if task in self.tasks:
                code = 1
            else:
                path = Path(argv[argv.index('/XML') + 1])
                self.tasks[task] = path.read_text(encoding='utf-8')
        elif operation == '/Delete':
            self.tasks.pop(task)
        else:
            raise AssertionError('unexpected operation')
        if not text and output:
            output = '<?xml version="1.0" encoding="UTF-16"?>\n' + output
        return subprocess.CompletedProcess(argv, code, output if text else output.encode('utf-16'),
                                           'fixture scheduler failure' if text else b'fixture scheduler failure')


class RoutineEnableTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.scratch'
        scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'fixture home'
        self.home.mkdir()
        shutil.copytree(ROOT / 'routines', self.root / 'routines')
        (self.root / 'manifest.json').write_bytes((ROOT / 'manifest.json').read_bytes())
        self.base = self.root / '.hub-store/routine-enablement'
        self.output = io.StringIO()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.table = self.root / 'crontab.txt'
        self.table.write_text('0 8 * * * existing-job\n', encoding='utf-8')
        self.calls = self.root / 'native-calls.jsonl'
        script = ('#!' + sys.executable + '\nimport json, os, pathlib, sys\n'
                  'name = pathlib.Path(sys.argv[0]).name\n'
                  'table = pathlib.Path(os.environ["FIXTURE_TABLE"])\n'
                  'if name == "crontab":\n'
                  '    if sys.argv[1] == "-l": print(table.read_text(), end="")\n'
                  '    else: table.write_text(sys.stdin.read())\n'
                  'else:\n'
                  '    with open(os.environ["FIXTURE_CALLS"], "a") as f: f.write(json.dumps([name] + sys.argv[1:]) + "\\n")\n'
                  '    if os.environ.get("FIXTURE_FAIL") == "1": sys.exit(2)\n')
        for name in ('crontab', 'systemctl', 'launchctl', 'codex'):
            target = self.bin / name
            target.write_text(script, encoding='utf-8')
            target.chmod(0o700)
        self.env = patch.dict(os.environ, {'HOME': str(self.home), 'USERPROFILE': str(self.home),
                              'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
                              'FIXTURE_TABLE': str(self.table), 'FIXTURE_CALLS': str(self.calls)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def args(self, runner='cron', operation='enable', write=True, yes=True, **kwargs):
        return Namespace(id='inventory-digest', runner=runner, operation=operation,
                         write=write, yes=yes, agent_command=kwargs.get('agent_command', 'codex exec'))

    def run_enable(self, runner='cron', operation='enable', **kwargs):
        with redirect_stdout(self.output):
            return enable.execute(self.args(runner, operation, **kwargs), self.root, self.home)

    def state(self):
        return json.loads((self.base / 'state.json').read_text(encoding='utf-8'))

    def test_cli_registration(self):
        args = parser().parse_args(['routine', 'enable', 'inventory-digest', '--runner', 'cron'])
        self.assertFalse(args.write)
        self.assertEqual(args.action, enable.execute)

    def test_dry_run_writes_nothing_and_reports_actual_command(self):
        self.run_enable(write=False)
        self.assertFalse(self.base.exists())
        self.assertIn('codex', self.output.getvalue())
        self.assertIn('routine_runtime.py', self.output.getvalue())
        self.assertEqual(self.table.read_text(), '0 8 * * * existing-job\n')

    def test_non_tty_refusal(self):
        with patch('sys.stdin.isatty', return_value=False), self.assertRaisesRegex(ValueError, 'requires --yes'):
            self.run_enable(yes=False)
        self.assertFalse(self.base.exists())

    def test_interactive_refusal(self):
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='no'), self.assertRaisesRegex(ValueError, 'cancelled'):
            self.run_enable(yes=False)
        self.assertFalse(self.base.exists())

    @unittest.skipIf(sys.platform == 'win32', 'POSIX executable fixture')
    def test_cron_enable_disable_idempotency_and_backup(self):
        original = self.table.read_bytes()
        self.run_enable()
        self.assertEqual(self.table.read_text().count('# agentlocker-'), 1)
        state = self.state()
        self.assertEqual(state['inventory-digest:cron']['status'], 'enabled')
        backups = list((self.base / 'backups').iterdir())
        self.assertTrue(any(p.read_bytes() == original for p in backups))
        self.run_enable()
        self.assertEqual(backups, list((self.base / 'backups').iterdir()))
        self.run_enable(operation='disable')
        self.assertEqual(self.table.read_bytes(), original)
        self.assertEqual(self.state(), {})
        self.run_enable(operation='disable')
        self.assertEqual(self.table.read_bytes(), original)
        definition = json.loads((self.root / 'routines/inventory-digest/routine.json').read_text())
        self.assertFalse(definition['enabled'])

    @unittest.skipIf(sys.platform == 'win32', 'POSIX executable fixture')
    def test_modified_cron_entry_is_preserved(self):
        self.run_enable()
        modified = self.table.read_text().replace('routine_runtime.py', 'changed.py')
        self.table.write_text(modified)
        with self.assertRaisesRegex(ValueError, 'modified crontab'):
            self.run_enable(operation='disable')
        self.assertEqual(self.table.read_text(), modified)

    @unittest.skipIf(sys.platform == 'win32', 'POSIX executable fixture')
    def test_changed_owned_file_refuses_disable(self):
        self.run_enable()
        path = Path(next(iter(self.state()['inventory-digest:cron']['files'])))
        path.write_text('unfamiliar')
        with self.assertRaisesRegex(ValueError, 'modified destination'):
            self.run_enable(operation='disable')
        self.assertEqual(path.read_text(), 'unfamiliar')

    @unittest.skipIf(sys.platform == 'win32', 'POSIX executable fixture')
    def test_systemd_files_native_calls_and_disable(self):
        with patch.object(enable.sys, 'platform', 'linux'):
            self.run_enable('systemd-timer')
            files = self.state()['inventory-digest:systemd-timer']['files']
            self.assertTrue(any('ExecStart=' in content for content in files.values()))
            self.run_enable('systemd-timer')
            self.assertEqual(len(self.calls.read_text().splitlines()), 2)
            self.run_enable('systemd-timer', 'disable')
        self.assertFalse(any(Path(name).exists() for name in files))
        self.assertIn('disable', self.calls.read_text())

    @unittest.skipIf(sys.platform == 'win32', 'POSIX executable fixture')
    def test_launchd_plist_native_calls_and_disable(self):
        with patch.object(enable.sys, 'platform', 'darwin'):
            self.run_enable('launchd')
            plan = self.state()['inventory-digest:launchd']
            path = Path(plan['native']['path'])
            plist = plistlib.loads(path.read_bytes())
            self.assertEqual(plist['StartInterval'], 60)
            self.assertIn('fixture home', str(path))
            self.run_enable('launchd')
            self.assertEqual(len(self.calls.read_text().splitlines()), 1)
            self.run_enable('launchd', 'disable')
        self.assertFalse(path.exists())
        self.assertIn('bootout', self.calls.read_text())

    @unittest.skipIf(sys.platform == 'win32', 'POSIX executable fixture')
    def test_failed_native_install_rolls_back_files(self):
        with patch.object(enable.sys, 'platform', 'linux'), patch.dict(os.environ, {'FIXTURE_FAIL': '1'}):
            with self.assertRaisesRegex(ValueError, 'runner command failed'):
                self.run_enable('systemd-timer')
        self.assertFalse((self.base / 'state.json').exists())
        self.assertFalse(list(self.home.rglob('*.service')))
        self.assertFalse(list(self.base.rglob('jobs/*.json')))

    def test_windows_local_write_refuses_missing_scheduler(self):
        with patch.object(enable.sys, 'platform', 'win32'), patch.object(enable.shutil, 'which', return_value=None):
            self.run_enable('schtasks', write=False)
            with self.assertRaisesRegex(ValueError, 'runner executable not found'):
                self.run_enable('schtasks')
        self.assertFalse(self.base.exists())

    def test_researched_handoffs_have_sources_and_limits(self):
        value, prompt = enable.load(self.root, 'inventory-digest')
        for runner, terms in {
            'dots': ('Recent activity', 'Scheduled', 'API', 'UNCONFIRMED'),
            'muse': ('session', '7 days', 'local runner', 'UNCONFIRMED', 'connect --remote'),
            'mausbot': ('5', '1,440', 'bot', 'computer', 'API', 'UNCONFIRMED'),
        }.items():
            plan = enable.ADAPTERS[runner].plan(runner, value, prompt)
            self.assertTrue(plan['sources'])
            self.assertTrue(all(url.startswith('https://') for url in plan['sources']))
            self.assertNotIn('await', plan['steps'])
            for term in terms:
                self.assertIn(term, plan['steps'])

    def scheduler(self):
        stub = TaskSchedulerStub()
        runner = patch.object(enable.subprocess, 'run', side_effect=stub.run)
        platform = patch.object(enable, 'platform_check')
        executable = patch.object(enable.shutil, 'which', return_value=sys.executable)
        for mocked in (runner, platform, executable):
            mocked.start()
            self.addCleanup(mocked.stop)
        return stub

    def test_schtasks_install_status_idempotency_disable_and_backups(self):
        stub = self.scheduler()
        self.run_enable('schtasks')
        plan = self.state()['inventory-digest:schtasks']
        task = plan['native']['task']
        self.assertTrue(task.startswith('\\agentlocker-'))
        self.assertEqual(plan['status'], 'enabled')
        self.assertEqual(plan['native']['installed_xml'], stub.tasks[task])
        xml = ET.fromstring(stub.tasks[task])
        ns = {'t': enable.schtasks.NAMESPACE}
        self.assertEqual(xml.findtext('.//t:Interval', namespaces=ns), 'PT1M')
        self.assertEqual(xml.findtext('.//t:LogonType', namespaces=ns), 'InteractiveToken')
        self.assertEqual(xml.findtext('.//t:RunLevel', namespaces=ns), 'LeastPrivilege')
        backups = list((self.base / 'backups').iterdir())
        self.assertTrue(any(p.read_text() == stub.tasks[task] for p in backups))
        for path in backups:
            ET.fromstring(path.read_bytes())
        self.run_enable('schtasks')
        self.assertEqual(sum(c[1] == '/Create' for c in stub.calls), 1)
        self.assertEqual(backups, list((self.base / 'backups').iterdir()))
        self.run_enable(operation='status')
        self.assertIn('"matches_record": true', self.output.getvalue())
        self.assertTrue(any(c[1] == '/Query' for c in stub.calls))
        self.run_enable('schtasks', 'disable')
        self.assertNotIn(task, stub.tasks)
        self.assertEqual(stub.tasks, {'\\foreign-task': '<Task />'})
        self.assertEqual(self.state(), {})
        self.assertFalse(any(Path(path).exists() for path in plan['files']))
        self.assertTrue(any(p.read_text() == plan['native']['installed_xml'] for p in (self.base / 'backups').iterdir()))
        self.assertFalse(json.loads((self.root / 'routines/inventory-digest/routine.json').read_text())['enabled'])

    def test_schtasks_confirmation_and_preview_never_probe_or_write(self):
        stub = self.scheduler()
        self.run_enable('schtasks', write=False)
        with patch('sys.stdin.isatty', return_value=False), self.assertRaisesRegex(ValueError, 'requires --yes'):
            self.run_enable('schtasks', yes=False)
        self.assertEqual(stub.calls, [])
        self.assertFalse(self.base.exists())

    def test_schtasks_unrecorded_task_is_preserved(self):
        stub = self.scheduler()
        value, prompt = enable.load(self.root, 'inventory-digest')
        plan = enable.local_plan(self.root, self.home, self.base, 'schtasks', value, prompt, 'codex exec')
        task = plan['native']['task']
        stub.tasks[task] = '<Task><foreign /></Task>'
        with self.assertRaisesRegex(ValueError, 'unfamiliar or modified Task Scheduler'):
            self.run_enable('schtasks')
        self.assertEqual(stub.tasks[task], '<Task><foreign /></Task>')
        self.assertFalse(any(c[1] in ('/Create', '/Delete') for c in stub.calls))

    def test_schtasks_modified_task_refuses_update_and_disable(self):
        stub = self.scheduler()
        self.run_enable('schtasks')
        task = self.state()['inventory-digest:schtasks']['native']['task']
        stub.tasks[task] = stub.tasks[task].replace('PT1M', 'PT5M')
        for operation in ('enable', 'disable'):
            with self.assertRaisesRegex(ValueError, 'unfamiliar or modified Task Scheduler'):
                self.run_enable('schtasks', operation)
        self.run_enable(operation='status')
        self.assertIn('"matches_record": false', self.output.getvalue())
        self.assertIn('PT5M', stub.tasks[task])
        self.assertFalse(any(c[1] == '/Delete' for c in stub.calls))

    def test_schtasks_query_errors_are_not_missing_tasks(self):
        stub = self.scheduler()
        stub.fail = '/Query'
        with self.assertRaisesRegex(ValueError, 'cannot query Task Scheduler'):
            self.run_enable('schtasks')
        self.assertFalse(any(c[1] == '/Create' for c in stub.calls))
        self.assertFalse((self.base / 'state.json').exists())

    def test_schtasks_failed_create_restores_files(self):
        stub = self.scheduler()
        stub.fail = '/Create'
        with self.assertRaisesRegex(ValueError, 'runner command failed'):
            self.run_enable('schtasks')
        self.assertEqual(stub.tasks, {'\\foreign-task': '<Task />'})
        self.assertFalse(list((self.base / 'jobs').glob('*')))
        self.assertFalse((self.base / 'state.json').exists())

    def test_schtasks_state_failure_rolls_back_native_create_or_delete(self):
        stub = self.scheduler()
        write = enable.private_write
        def fail_state(path, content):
            if path.name == 'state.json':
                raise OSError('fixture state write failure')
            return write(path, content)
        with patch.object(enable, 'private_write', side_effect=fail_state):
            with self.assertRaisesRegex(OSError, 'fixture state write failure'):
                self.run_enable('schtasks')
        self.assertEqual(stub.tasks, {'\\foreign-task': '<Task />'})
        self.assertFalse(list((self.base / 'jobs').glob('*')))
        self.run_enable('schtasks')
        before = dict(stub.tasks)
        state = self.state()
        with patch.object(enable, 'private_write', side_effect=fail_state):
            with self.assertRaisesRegex(OSError, 'fixture state write failure'):
                self.run_enable('schtasks', 'disable')
        self.assertEqual(stub.tasks, before)
        self.assertEqual(self.state(), state)
        self.assertTrue(all(Path(path).exists() for path in state['inventory-digest:schtasks']['files']))

    def test_schtasks_missing_task_reported_and_recreated(self):
        stub = self.scheduler()
        self.run_enable('schtasks')
        task = self.state()['inventory-digest:schtasks']['native']['task']
        stub.tasks.pop(task)
        self.run_enable(operation='status')
        self.assertIn('"exists": false', self.output.getvalue())
        self.run_enable('schtasks')
        self.assertIn(task, stub.tasks)

    def test_schtasks_changed_job_file_is_preserved(self):
        stub = self.scheduler()
        self.run_enable('schtasks')
        plan = self.state()['inventory-digest:schtasks']
        job = next(Path(p) for p in plan['files'] if p.endswith('.json'))
        job.write_text('user edit')
        with self.assertRaisesRegex(ValueError, 'modified destination'):
            self.run_enable('schtasks', 'disable')
        self.assertEqual(job.read_text(), 'user edit')
        self.assertIn(plan['native']['task'], stub.tasks)

    def test_schtasks_task_changed_between_queries_is_preserved(self):
        stub = self.scheduler()
        self.run_enable('schtasks')
        plan = self.state()['inventory-digest:schtasks']
        task = plan['native']['task']
        original_query = enable.schtasks.query
        count = 0
        def changed(name):
            nonlocal count
            count += 1
            if count == 2:
                stub.tasks[name] = stub.tasks[name].replace('PT1M', 'PT5M')
            return original_query(name)
        with patch.object(enable.schtasks, 'query', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'task changed'):
                self.run_enable('schtasks', 'disable')
        self.assertIn('PT5M', stub.tasks[task])
        self.assertTrue(all(Path(p).exists() for p in plan['files']))
        self.assertFalse(any(c[1] == '/Delete' for c in stub.calls))

    def test_schtasks_status_reports_query_failure(self):
        stub = self.scheduler()
        self.run_enable('schtasks')
        stub.fail = '/Query'
        self.run_enable(operation='status')
        self.assertIn('cannot query Task Scheduler', self.output.getvalue())
        self.assertIn('"error":', self.output.getvalue())

    def test_schtasks_legacy_handoff_status_and_disable_need_no_scheduler(self):
        self.base.mkdir(parents=True)
        (self.base / 'state.json').write_text(json.dumps({'inventory-digest:schtasks':
            {'runner': 'schtasks', 'status': 'handoff-ready', 'steps': 'Manual setup'}}))
        with patch.object(enable.subprocess, 'run', side_effect=AssertionError('live scheduler')):
            self.run_enable(operation='status')
            self.assertIn('Legacy handoff record', self.output.getvalue())
            self.run_enable('schtasks', 'disable')
        self.assertEqual(self.state(), {})

    def test_schtasks_native_definition_update_and_failed_update_recovery(self):
        stub = self.scheduler()
        self.run_enable('schtasks')
        before = dict(stub.tasks)
        state = self.state()
        original_xml = enable.schtasks.task_xml
        def new_xml(command, task):
            return original_xml(command, task).replace('PT1M', 'PT2M')
        stub.fail = '/Create'
        with patch.object(enable.schtasks, 'task_xml', side_effect=new_xml):
            with self.assertRaisesRegex(ValueError, 'recovery failed'):
                self.run_enable('schtasks')
        self.assertEqual(self.state(), state)
        self.assertTrue(all(Path(p).exists() for p in state['inventory-digest:schtasks']['files']))
        self.assertTrue(any(p.read_text() == before[state['inventory-digest:schtasks']['native']['task']]
                            for p in (self.base / 'backups').iterdir()))
        stub.fail = None
        self.run_enable('schtasks')
        with patch.object(enable.schtasks, 'task_xml', side_effect=new_xml):
            self.run_enable('schtasks')
        task = self.state()['inventory-digest:schtasks']['native']['task']
        self.assertIn('PT2M', stub.tasks[task])

    def test_schtasks_xml_escaping_and_windows_argument_quoting(self):
        command = [r'C:\Python & tools\python.exe', r'C:\Project with spaces\routine_runtime.py',
                   'C:\\Store & data\\job "quoted".json']
        xml = ET.fromstring(enable.schtasks.task_xml(command, '\\agentlocker-fixture'))
        ns = {'t': enable.schtasks.NAMESPACE}
        self.assertEqual(xml.findtext('.//t:Command', namespaces=ns), command[0])
        self.assertEqual(xml.findtext('.//t:Arguments', namespaces=ns),
                         r'"C:\Project with spaces\routine_runtime.py" "C:\Store & data\job \"quoted\".json"')
        self.assertNotIn('cmd.exe', ET.tostring(xml, encoding='unicode'))

    def test_schtasks_paths_and_prompt_stay_separate_arguments(self):
        stub = self.scheduler()
        (self.root / 'config.json').write_text(json.dumps({'store_path': 'store with spaces'}))
        self.base = self.root / 'store with spaces/routine-enablement'
        prompt = 'Review "quoted path" & $(anything) %PATH% \\ folder\nnext line'
        (self.root / 'routines/inventory-digest/routine-prompt.md').write_text(prompt)
        self.run_enable('schtasks', agent_command=shlex.join([sys.executable, '-c', 'print("quoted argument")']))
        plan = self.state()['inventory-digest:schtasks']
        create = next(c for c in stub.calls if c[1] == '/Create')
        self.assertEqual(create[-1], plan['native']['xml_path'])
        self.assertIn('store with spaces', create[-1])
        self.assertNotIn('/F', create)
        self.assertTrue(all(prompt not in arg for call in stub.calls for arg in call))
        xml = plan['files'][plan['native']['xml_path']]
        self.assertNotIn('quoted argument', xml)
        job = next(Path(p) for p in plan['files'] if p.endswith('.json'))
        self.assertEqual(json.loads(job.read_text())['prompt'], prompt)
        with patch.object(runtime.time, 'time', return_value=1792400400), patch.object(runtime.subprocess, 'run', return_value=Namespace(returncode=0)) as invoked:
            self.assertEqual(runtime.run_job(job), 0)
        self.assertEqual(invoked.call_args.args[0], [sys.executable, '-c', 'print("quoted argument")', prompt])
        self.assertFalse(invoked.call_args.kwargs.get('shell', False))

    def test_all_handoffs_and_t3_exact_payload(self):
        for runner in enable.ADAPTERS:
            with self.subTest(runner=runner):
                self.run_enable(runner, write=False)
        self.assertFalse(self.base.exists())
        self.run_enable('t3-scheduler')
        plan = self.state()['inventory-digest:t3-scheduler']
        self.assertEqual(plan['status'], 'handoff-ready')
        self.assertEqual(plan['payload']['schedule'], {'type': 'fixed_time', 'timeOfDay': '09:00', 'weekdays': [1, 2, 3, 4, 5]})
        self.run_enable('t3-scheduler', 'disable')
        self.assertEqual(self.state(), {})

    def test_t3_interval_webhook_and_unsupported_cron(self):
        self.assertEqual(enable.t3_payload({'schedule': {'type': 'interval', 'every_seconds': 90}}, 'Review'),
                         {'schedule': {'type': 'interval', 'everyMs': 90000}, 'prompt': 'Review'})
        self.assertEqual(enable.t3_payload({'schedule': {'type': 'webhook'}}, 'Review')['schedule'], {'type': 'webhook'})
        with self.assertRaisesRegex(ValueError, 'translate the cron'):
            enable.t3_payload({'schedule': {'type': 'cron', 'expression': '0 9 * * *'}}, 'Review')

    def test_claude_skill_confirmation_idempotency_disable_and_conflict(self):
        self.run_enable('claude-code')
        path = self.home / '.claude/scheduled-tasks/agentlocker-inventory-digest/SKILL.md'
        text = path.read_text()
        self.assertTrue(text.startswith('---\nname: agentlocker-inventory-digest\n'))
        self.assertIn('# Read-only inventory', text)
        self.run_enable('claude-code')
        self.assertFalse((self.base / 'backups').exists())
        self.run_enable('claude-code', 'disable')
        self.assertFalse(path.exists())
        self.assertTrue(list((self.base / 'backups').iterdir()))
        path.write_text('user skill')
        with self.assertRaisesRegex(ValueError, 'unfamiliar'):
            self.run_enable('claude-code')
        self.assertEqual(path.read_text(), 'user skill')

    @unittest.skipIf(sys.platform == 'win32', 'symlink fixture on POSIX')
    def test_symlinked_destination_is_refused(self):
        (self.home / '.claude').symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            self.run_enable('claude-code')
        self.assertFalse((self.root / 'scheduled-tasks').exists())

    def test_status_has_no_live_probe(self):
        with patch.object(enable, 'call', side_effect=AssertionError('live probe')):
            self.run_enable(operation='status')
        self.assertIn('not verified', self.output.getvalue())

    def test_validators_allow_platforms_but_never_enabled_definitions(self):
        value, prompt = enable.load(self.root, 'inventory-digest')
        for runner in enable.RUNNERS:
            validators.routine({**value, 'runner': runner})
        with self.assertRaisesRegex(ValueError, 'portable routine definitions'):
            validators.routine({**value, 'enabled': True})

    def test_local_schedule_boundaries(self):
        value, prompt = enable.load(self.root, 'inventory-digest')
        for schedule in ({'type': 'interval', 'every_seconds': 5}, {'type': 'webhook'},
                         {'type': 'cron', 'expression': '@daily'}):
            with self.subTest(schedule=schedule), self.assertRaises(ValueError):
                enable.local_plan(self.root, self.home, self.base, 'cron', {**value, 'schedule': schedule}, prompt, 'codex exec')
        plan = enable.local_plan(self.root, self.home, self.base, 'systemd-timer', value, prompt, '"agent path" exec')
        self.assertEqual(plan['agent_argv'][0], 'agent path')

    def test_runtime_timezone_interval_cron_and_duplicates(self):
        value, _ = enable.load(self.root, 'inventory-digest')
        monday = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc).timestamp()
        self.assertTrue(runtime.due(value, {}, monday))
        self.assertFalse(runtime.due(value, {'slot': '2026-10-19T09:00+0000'}, monday))
        self.assertFalse(runtime.due(value, {}, monday + 60))
        interval = {**value, 'schedule': {'type': 'interval', 'every_seconds': 120}}
        self.assertFalse(runtime.due(interval, {'timestamp': monday}, monday + 60))
        self.assertTrue(runtime.due(interval, {'timestamp': monday}, monday + 120))
        cron = {**value, 'schedule': {'type': 'cron', 'expression': '*/5 9 * * 1'}}
        self.assertTrue(runtime.due(cron, {}, monday))
        self.assertFalse(runtime.due(cron, {}, monday + 60))

    @unittest.skipIf(sys.platform == 'win32', 'POSIX executable fixture')
    def test_end_to_end_shipped_inventory_cron_executes_pinned_prompt(self):
        record = self.root / 'agent-args.json'
        stub = self.root / 'agent stub.py'
        stub.write_text('import json, pathlib, sys\npathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n')
        agent = shlex.join([sys.executable, str(stub), str(record), 'exec'])
        self.run_enable(agent_command=agent)
        entry = self.table.read_text().splitlines()[-1]
        installed = entry.split(' # ', 1)[0].split(' ', 5)[5]
        clock = self.root / 'clock'
        clock.mkdir()
        (clock / 'sitecustomize.py').write_text('import time\ntime.time = lambda: 1792400400\n')
        env = {**os.environ, 'PYTHONPATH': str(clock)}
        result = subprocess.run(shlex.split(installed), env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = json.loads(record.read_text())
        self.assertEqual(argv[0], 'exec')
        self.assertEqual(argv[1], (self.root / 'routines/inventory-digest/routine-prompt.md').read_text())
        self.run_enable(operation='disable')
        self.assertEqual(self.table.read_text(), '0 8 * * * existing-job\n')

    def test_runtime_timeout_and_overlap_lock(self):
        value, prompt = enable.load(self.root, 'inventory-digest')
        plan = enable.local_plan(self.root, self.home, self.base, 'cron', value, prompt, 'codex exec')
        name, content = next(iter(plan['files'].items()))
        path = Path(name)
        runtime.private_write(path, content)
        with patch.object(runtime.time, 'time', return_value=1792400400), patch.object(runtime.subprocess, 'run', side_effect=subprocess.TimeoutExpired('agent', 600)):
            self.assertEqual(runtime.run_job(path), 124)
        self.assertTrue(path.with_suffix('.last.json').exists())
        with patch.object(runtime.subprocess, 'run', side_effect=AssertionError('must skip')):
            with patch.object(runtime.time, 'time', return_value=1792400400):
                self.assertEqual(runtime.run_job(path), 0)

    @unittest.skipIf(sys.platform == 'win32', 'POSIX executable fixture')
    def test_update_backs_up_prompt_and_keeps_single_cron_job(self):
        self.run_enable()
        job = Path(next(iter(self.state()['inventory-digest:cron']['files'])))
        previous = job.read_bytes()
        prompt = self.root / 'routines/inventory-digest/routine-prompt.md'
        prompt.write_text('New approved prompt\n')
        self.run_enable()
        self.assertEqual(self.table.read_text().count('# agentlocker-'), 1)
        self.assertEqual(json.loads(job.read_text())['prompt'], 'New approved prompt\n')
        self.assertTrue(any(p.read_bytes() == previous for p in (self.base / 'backups').iterdir()))

    def test_check_gate_rejects_enabled_definition_even_with_matching_hash(self):
        from checks.checker import check_content
        manifest_path = self.root / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        for collection in ('skills', 'rules', 'mcp_servers'):
            manifest[collection] = []
        path = self.root / 'routines/inventory-digest/routine.json'
        value = json.loads(path.read_text())
        value['enabled'] = True
        path.write_text(json.dumps(value))
        manifest['routines'][0]['sha256'] = validators.digest(path)
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'portable routine definitions'):
            check_content(self.root)

    def test_runtime_skip_queue_and_allow_lock_behavior(self):
        value, prompt = enable.load(self.root, 'inventory-digest')
        for overlap in ('skip', 'queue', 'allow'):
            definition = {**value, 'overlap': overlap}
            plan = enable.local_plan(self.root, self.home, self.base, 'cron', definition, prompt, 'codex exec')
            name, content = next(iter(plan['files'].items()))
            path = Path(name)
            runtime.private_write(path, content)
            path.with_suffix('.last.json').unlink(missing_ok=True)
            with patch.object(runtime.time, 'time', return_value=1792400400), patch.object(runtime, 'lock') as locking, patch.object(runtime.subprocess, 'run', return_value=Namespace(returncode=0)) as invoked:
                runtime.run_job(path)
                self.assertEqual(invoked.call_count, 1)
                self.assertEqual(locking.call_count, 1 if overlap == 'allow' else 2)
                if overlap != 'allow':
                    self.assertEqual(locking.call_args.kwargs, {'wait': overlap == 'queue'})
        path.with_suffix('.last.json').unlink(missing_ok=True)
        runtime.private_write(path, json.dumps({**json.loads(content), 'definition': {**value, 'overlap': 'skip'}}))
        with patch.object(runtime.time, 'time', return_value=1792400400), patch.object(runtime, 'lock', side_effect=[None, BlockingIOError]), patch.object(runtime.subprocess, 'run') as invoked:
            self.assertEqual(runtime.run_job(path), 0)
            invoked.assert_not_called()

    def test_utc_does_not_need_optional_timezone_database(self):
        with patch.object(runtime, 'ZoneInfo', side_effect=AssertionError('database lookup')):
            self.assertEqual(runtime.zone('UTC'), timezone.utc)

    def test_control_characters_in_local_command_are_refused(self):
        value, prompt = enable.load(self.root, 'inventory-digest')
        with self.assertRaisesRegex(ValueError, 'control characters'):
            enable.local_plan(self.root, self.home, self.base, 'cron', value, prompt, 'agent "line\nbreak"')

    @unittest.skipIf(sys.platform == 'win32', 'POSIX executable fixture')
    def test_state_write_failure_restores_cron_and_owned_files(self):
        original = self.table.read_text()
        write = enable.private_write
        def fail_state(path, content):
            if path.name == 'state.json':
                raise OSError('fixture state write failure')
            return write(path, content)
        with patch.object(enable, 'private_write', side_effect=fail_state):
            with self.assertRaisesRegex(OSError, 'fixture state write failure'):
                self.run_enable()
        self.assertEqual(self.table.read_text(), original)
        self.assertFalse(list((self.base / 'jobs').glob('*.json')))

    @unittest.skipIf(sys.platform == 'win32', 'POSIX executable fixture')
    def test_cron_concurrent_changes_are_preserved(self):
        read = enable.crontab
        calls = 0
        changed = self.table.read_text() + '0 7 * * * new-foreign-job\n'
        def changing():
            nonlocal calls
            calls += 1
            if calls == 3:
                self.table.write_text(changed)
            return read()
        with patch.object(enable, 'crontab', side_effect=changing):
            with self.assertRaisesRegex(ValueError, 'crontab changed'):
                self.run_enable()
        self.assertEqual(self.table.read_text(), changed)
        self.assertFalse(list((self.base / 'jobs').glob('*.json')))

    @unittest.skipIf(sys.platform == 'win32', 'POSIX executable fixture')
    def test_missing_agent_refuses_before_native_install(self):
        with self.assertRaisesRegex(ValueError, 'agent CLI not found'):
            self.run_enable(agent_command='nonexistent-fixture-agent exec')
        self.assertFalse(self.base.exists())
        self.assertEqual(self.table.read_text(), '0 8 * * * existing-job\n')
