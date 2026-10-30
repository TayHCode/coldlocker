import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from connect import python_command
import hub
import scanner
from test_scanner import CANARY, FIXTURES, ROOT, snapshot


class ScanCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'locker'
        self.home = Path(self.temp.name) / 'home'
        shutil.copytree(FIXTURES / 'linux', self.home)
        self.root.mkdir()
        for name in ('manifest.json', 'LICENSE', '.gitignore'):
            shutil.copyfile(ROOT / name, self.root / name)
        for name in ('skills', 'rules', 'routines', 'mcp'):
            shutil.copytree(ROOT / name, self.root / name)

    def command(self, *argv, tty=False, answer='yes'):
        args = hub.parser().parse_args(['scan', '--home', str(self.home), *argv])
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(scanner, 'ROOT', self.root), patch.object(sys.stdin, 'isatty', return_value=tty), patch('builtins.input', return_value=answer), patch.object(scanner, 'default_runner', return_value=''), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = args.action(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_dry_run_json_and_human_do_not_write(self):
        before = snapshot(self.home), snapshot(self.root)
        code, output, error = self.command('--json')
        self.assertEqual(code, 0)
        report = json.loads(output)
        self.assertEqual(report['version'], 1)
        self.assertNotIn(CANARY, output + error)
        code, output, error = self.command()
        self.assertEqual(code, 0)
        self.assertIn('claude:', output)
        self.assertIn('plugins', output)
        self.assertIn('connections', output)
        self.assertNotIn(CANARY, output + error)
        self.assertEqual(before, (snapshot(self.home), snapshot(self.root)))

    def test_compact_and_all_views_preserve_json(self):
        _, data, _ = self.command('--json')
        report = json.loads(data)
        _, compact, _ = self.command()
        _, expanded, _ = self.command('--all')
        _, all_data, _ = self.command('--all', '--json')
        self.assertEqual(json.loads(all_data), report)
        hidden = []
        for item in report['items']:
            self.assertIn(item['id'], expanded)
            if item['status'] in ('vendor', 'duplicate', 'already_in_locker'):
                self.assertNotIn(item['id'], compact)
                hidden.append(item)
            else:
                self.assertIn(item['id'], compact)
                if item['status'] == 'skipped':
                    line = next(line for line in compact.splitlines() if item['id'] in line)
                    self.assertIn('reason:', line)
                    for flag in item['flags']:
                        self.assertIn(flag, line)
        self.assertTrue(hidden)
        self.assertLess(len(compact.splitlines()), len(expanded.splitlines()))
        for agent in report['agents']:
            items = [i for i in hidden if i['agent'] == agent]
            if not items:
                continue
            section = compact.split(agent + ': ', 1)[1].split('\n')
            collapsed = next(line for line in section if 'Collapsed:' in line)
            for kind in ('vendor', 'plugin_cache', 'duplicate', 'already_in_locker'):
                count = sum(('plugin_cache' if i['status'] == 'vendor' and i['ownership'] == 'plugin_cache'
                             else i['status']) == kind for i in items)
                self.assertIn(f'{kind} {count}', collapsed)
        self.assertNotIn(CANARY, compact + expanded)

    def test_human_view_covers_every_status_and_collapsed_category(self):
        items = []
        for index, (status, ownership) in enumerate([
            ('new', 'user'), ('connect', 'user'), ('conflict', 'user'), ('skipped', 'user'),
            ('vendor', 'vendor'), ('vendor', 'system'), ('vendor', 'plugin_cache'),
            ('duplicate', 'user'), ('already_in_locker', 'user')]):
            items.append({'id': f'item-{index}', 'agent': 'codex', 'name': 'fixture',
                          'kind': 'rule', 'status': status, 'ownership': ownership,
                          'flags': ['needs-manual'] if status == 'skipped' else []})
        report = {'counts': {'codex': {'rules': len(items)}}, 'items': items}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            scanner.human(report)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 6)
        self.assertEqual(lines[0], 'codex: rules 9')
        for item in items[:4]:
            self.assertIn(item['id'], output.getvalue())
        self.assertIn('[reason: needs-manual]', output.getvalue())
        self.assertIn('vendor 2, plugin_cache 1, duplicate 1, already_in_locker 1', lines[-1])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            scanner.human(report, all_items=True)
        self.assertEqual(len(output.getvalue().splitlines()), 10)
        for item in items:
            self.assertIn(item['id'], output.getvalue())
        self.assertNotIn('Collapsed:', output.getvalue())

    def test_noninteractive_import_requires_yes_and_prints_summary(self):
        before = snapshot(self.root)
        code, output, error = self.command('--import')
        self.assertEqual(code, 1)
        self.assertIn('claude:', output)
        self.assertIn('--yes', error)
        self.assertEqual(before, snapshot(self.root))

    def test_bare_import_confirms_all_and_refusal_writes_nothing(self):
        before = snapshot(self.root)
        code, output, error = self.command('--import', tty=True, answer='no')
        self.assertEqual(code, 1)
        self.assertEqual(before, snapshot(self.root))
        code, output, error = self.command('--import', tty=True)
        self.assertEqual(code, 0, error + output)
        self.assertIn('"rejected": 0', output)
        self.assertNotEqual(before, snapshot(self.root))
        self.assertNotIn(python_command() + ' hub.py connect', output)

    def test_rejected_item_prints_its_real_reason(self):
        elsewhere = Path(self.temp.name) / 'elsewhere'
        elsewhere.mkdir()
        catalog = self.root / '.hub-store/catalog'
        catalog.mkdir(parents=True)
        (catalog / 'rules').symlink_to(elsewhere, target_is_directory=True)
        _, data, _ = self.command('--json')
        item = next(i for i in json.loads(data)['items'] if i['status'] == 'new' and i['kind'] == 'rule')
        code, output, error = self.command('--import', '--yes', '--only', item['id'])
        self.assertEqual(code, 1)
        self.assertIn(f"Rejected {item['id']}: store symlinks are forbidden", error)
        self.assertNotIn('item could not be imported', error)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_only_selects_one_and_yes_skips_confirmation(self):
        _, output, _ = self.command('--json')
        item = next(i for i in json.loads(output)['items'] if i['status'] == 'new' and i['kind'] == 'rule')
        code, output, error = self.command('--import', '--only', item['id'], '--yes')
        self.assertEqual(code, 0, output + error)
        self.assertEqual(json.loads(output.splitlines()[-1])['imported'], [item['id']])
        self.assertNotIn(CANARY, output + error)

    def test_agent_filter_updates_counts_and_selection(self):
        code, output, _ = self.command('--agent', 'opencode', '--json')
        self.assertEqual(code, 0)
        report = json.loads(output)
        self.assertEqual(set(report['agents']), {'opencode'})
        self.assertTrue(all(i['agent'] == 'opencode' for i in report['items']))
        self.assertEqual(sum(report['totals']['statuses'].values()), len(report['items']))
        self.assertEqual(report['totals']['mcp_servers'], 2)

    def test_next_step_printed_when_connect_exists(self):
        (self.root / 'connect.py').write_text('', encoding='utf-8')
        code, output, _ = self.command('--import', '--yes')
        self.assertEqual(code, 0)
        self.assertTrue(output.rstrip().endswith(python_command() + ' hub.py connect'))

    def test_next_step_uses_the_running_interpreter_on_windows(self):
        (self.root / 'connect.py').write_text('', encoding='utf-8')
        with patch('sys.platform', 'win32'), patch('sys.executable', 'C:\\Python312\\python.exe'):
            code, output, _ = self.command('--import', '--yes')
        self.assertEqual(code, 0)
        self.assertEqual(output.rstrip().splitlines()[-1], 'C:\\Python312\\python.exe hub.py connect')

    def test_cli_invalid_input_exits_one_without_exception_details(self):
        process = subprocess.run([sys.executable, str(ROOT / 'hub.py'), 'scan', '--home', str(self.home / 'missing'), '--json'], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(process.returncode, 1)
        self.assertNotIn('Traceback', process.stderr)
        self.assertNotIn(str(self.home), process.stderr)

    def test_unknown_agent_exits_nonzero_naming_valid_agents(self):
        process = subprocess.run([sys.executable, str(ROOT / 'hub.py'), 'scan', '--home', str(self.home), '--agent', 'nonesuch'], capture_output=True, text=True, encoding='utf-8')
        self.assertNotEqual(process.returncode, 0)
        for agent in ('claude', 'codex', 'cursor', 'opencode', 'agents', 'grok', 't3', 'system'):
            self.assertIn(agent, process.stderr)

    def test_supported_agent_with_nothing_found_says_where_it_looked(self):
        empty = Path(self.temp.name) / 'empty'
        empty.mkdir()
        self.home = empty
        for agent, looked in (('opencode', '~/.config/opencode'), ('grok', '~/.grok')):
            code, output, _ = self.command('--agent', agent)
            self.assertEqual(code, 0)
            self.assertIn(f'{agent}: nothing found', output)
            self.assertIn(str(Path(looked)), output)
            self.assertNotIn(str(empty), output)

    def test_nothing_found_is_not_added_to_json(self):
        empty = Path(self.temp.name) / 'empty'
        empty.mkdir()
        self.home = empty
        _, output, _ = self.command('--agent', 'opencode', '--json')
        self.assertEqual(json.loads(output)['counts'], {})

    def test_default_scan_hints_project_option_only_when_none_given(self):
        _, output, _ = self.command()
        self.assertIn('--project <dir>', output)
        project = Path(self.temp.name) / 'project'
        project.mkdir()
        _, output, _ = self.command('--project', str(project))
        self.assertNotIn('--project <dir>', output)
        _, output, _ = self.command('--json')
        self.assertNotIn('--project <dir>', output)

    def test_write_options_without_import_rejected(self):
        with self.assertRaises(ValueError):
            self.command('--yes')
        with self.assertRaises(ValueError):
            self.command('--only', 'sc-000000000000')


if __name__ == '__main__':
    unittest.main()
