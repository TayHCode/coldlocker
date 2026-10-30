import json
from pathlib import Path
import shutil
import tempfile
import unittest

import portable
import scanner
from test_scanner import ROOT
from store import FileStore

SKILL = '---\nname: nightwatch\ndescription: Check the nightly jobs\n---\nLook at the nightly jobs and report failures.\n'


class DesktopTaskTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.home = base / 'home'
        self.home.mkdir()
        self.base = base
        self.root = base / 'locker'
        self.root.mkdir()
        for name in ('manifest.json', 'LICENSE', '.gitignore'):
            shutil.copyfile(ROOT / name, self.root / name)
        for folder in ('skills', 'rules', 'routines', 'mcp'):
            shutil.copytree(ROOT / folder, self.root / folder)
        self.store = FileStore(self.root)

    def write(self, relative, text):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('utf-8'))

    def routines(self, agent):
        report = scanner.Inventory(self.home, (), 'linux', {}, lambda command: '', self.root).discover()
        return [i for i in report['items'] if i['kind'] == 'routine' and i['agent'] == agent]

    def test_claude_scheduled_task_skill_is_listed_as_needs_manual(self):
        self.write('.claude/scheduled-tasks/daemon-nightwatch/SKILL.md', SKILL)
        [item] = self.routines('claude')
        self.assertEqual((item['name'], item['status']), ('nightwatch', 'skipped'))
        self.assertIn('needs-manual', item['flags'])
        self.assertIn('Claude Desktop app', item['reason'])
        self.assertIn(f'{portable.python_command()} hub.py import-routine', item['next_step'])

    def test_claude_scheduled_task_skill_with_crlf_line_endings_keeps_its_frontmatter(self):
        path = self.home / '.claude/scheduled-tasks/daemon-nightwatch/SKILL.md'
        path.parent.mkdir(parents=True)
        path.write_bytes(SKILL.replace('\n', '\r\n').encode('utf-8'))
        inventory = scanner.Inventory(self.home, (), 'linux', {}, lambda command: '', self.root)
        [item] = [i for i in inventory.discover()['items'] if i['kind'] == 'routine' and i['agent'] == 'claude']
        prompt = inventory.payloads[item['id']][0]['prompt']
        self.assertEqual(item['name'], 'nightwatch')
        self.assertIn('Look at the nightly jobs', prompt)
        self.assertNotIn('description:', prompt)
        self.assertNotIn('\r', prompt)

    def test_claude_scheduled_task_name_falls_back_to_folder(self):
        self.write('.claude/scheduled-tasks/daemon-nightwatch/SKILL.md', '---\ndescription: Check\n---\nBody.\n')
        [item] = self.routines('claude')
        self.assertEqual(item['name'], 'daemon-nightwatch')

    def test_claude_scheduled_task_with_schedule_file_keeps_importing(self):
        self.write('.claude/scheduled-tasks/daemon-nightwatch/SKILL.md', SKILL)
        self.write('.claude/scheduled-tasks/daemon-nightwatch/task.json',
                   '{"name": "nightwatch", "schedule": {"type": "interval", "every_seconds": 3600}, "prompt": "Check."}')
        [item] = self.routines('claude')
        self.assertEqual((item['name'], item['status']), ('nightwatch', 'new'))

    def test_codex_automation_without_name_uses_folder_name(self):
        self.write('.codex/automations/weekly-report/automation.toml',
                   'prompt = "Summarize the week."\nrrule = "RRULE:FREQ=DAILY;BYHOUR=9;BYMINUTE=0"\n')
        self.write('.codex/automations/odd-recurrence/automation.toml',
                   'prompt = "Run often."\nrrule = "RRULE:FREQ=MONTHLY;BYMONTHDAY=3"\n')
        rows = {i['name']: i['status'] for i in self.routines('codex')}
        self.assertEqual(rows, {'weekly-report': 'new', 'odd-recurrence': 'skipped'})

    def test_codex_minutely_rrule_imports_as_disabled_900_second_interval_on_each_platform(self):
        for platform in ('darwin', 'win32'):
            with self.subTest(platform=platform):
                home = self.base / f'home-{platform}'
                automation = home / '.codex/automations' / f'minute-task-{platform}' / 'automation.toml'
                automation.parent.mkdir(parents=True)
                automation.write_text(
                    'prompt = "Check the fixture."\nrrule = "RRULE:FREQ=MINUTELY;INTERVAL=15"\n',
                    encoding='utf-8')
                runner = lambda command: ''
                inventory = scanner.Inventory(home, (), platform, {}, runner, self.root)
                report = inventory.discover()
                [item] = [i for i in report['items'] if i['agent'] == 'codex' and i['kind'] == 'routine']
                self.assertEqual(item['status'], 'new')
                self.assertEqual(item['schedule'], {'type': 'interval', 'every_seconds': 900})
                result = scanner.apply(self.root, report, [item['id']], self.store, home,
                                       platform=platform, env={}, runner=runner)
                self.assertEqual(result['counts'], {'imported': 1, 'rejected': 0})
                routine = self.root / '.hub-store/catalog/routines' / item['name'] / 'routine.json'
                value = json.loads(routine.read_text(encoding='utf-8'))
                self.assertEqual(value['schedule'], {'type': 'interval', 'every_seconds': 900})
                self.assertIs(value['enabled'], False)

    def test_finite_minutely_rrule_stays_skipped(self):
        self.write('.codex/automations/finite/automation.toml',
                   'prompt = "Run a finite fixture."\nrrule = "RRULE:FREQ=MINUTELY;INTERVAL=15;COUNT=5"\n')
        [item] = self.routines('codex')
        self.assertEqual(item['status'], 'skipped')


if __name__ == '__main__':
    unittest.main()
