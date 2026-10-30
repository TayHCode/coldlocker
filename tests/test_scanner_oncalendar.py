import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import scanner
from store import FileStore

ROOT = Path(__file__).resolve().parents[1]

CONVERTED = [
    ('hourly', {'type': 'interval', 'every_seconds': 3600}, 'UTC'),
    ('daily', {'type': 'fixed_time', 'time_of_day': '00:00'}, 'UTC'),
    ('weekly', {'type': 'cron', 'expression': '0 0 * * 1'}, 'UTC'),
    ('*-*-* 08:00:00', {'type': 'cron', 'expression': '0 8 * * *'}, 'UTC'),
    ('*-*-* 07:30:00 UTC', {'type': 'cron', 'expression': '30 7 * * *'}, 'UTC'),
    ('*-*-* 05:50:00 America/New_York', {'type': 'cron', 'expression': '50 5 * * *'}, 'America/New_York'),
    ('08:30:00', {'type': 'cron', 'expression': '30 8 * * *'}, 'UTC'),
    ('08:30', {'type': 'cron', 'expression': '30 8 * * *'}, 'UTC'),
    ('*:0/3', {'type': 'cron', 'expression': '*/3 * * * *'}, 'UTC'),
    ('*:0/5', {'type': 'cron', 'expression': '*/5 * * * *'}, 'UTC'),
    ('*:0/5:00', {'type': 'cron', 'expression': '*/5 * * * *'}, 'UTC'),
    ('*:0/45:00', {'type': 'cron', 'expression': '*/45 * * * *'}, 'UTC'),
    ('*:10/20', {'type': 'cron', 'expression': '10,30,50 * * * *'}, 'UTC'),
    ('*-*-* *:0/5:00', {'type': 'cron', 'expression': '*/5 * * * *'}, 'UTC'),
    ('*:05,25,45:00', {'type': 'cron', 'expression': '5,25,45 * * * *'}, 'UTC'),
    ('*:00,20,40:00', {'type': 'cron', 'expression': '0,20,40 * * * *'}, 'UTC'),
    ('*:00,10,20,30,40,50:00', {'type': 'cron', 'expression': '0,10,20,30,40,50 * * * *'}, 'UTC'),
    ('*-*-* *:07,37:00', {'type': 'cron', 'expression': '7,37 * * * *'}, 'UTC'),
    ('*-*-* *:17:00', {'type': 'cron', 'expression': '17 * * * *'}, 'UTC'),
    ('*-*-* *:*:00', {'type': 'cron', 'expression': '* * * * *'}, 'UTC'),
    ('*-*-* 0/6:00:00', {'type': 'cron', 'expression': '0 */6 * * *'}, 'UTC'),
    ('Sun *-*-* 20:00:00 America/New_York', {'type': 'cron', 'expression': '0 20 * * 0'}, 'America/New_York'),
    ('Mon,Wed,Fri *-*-* 09:00:00', {'type': 'cron', 'expression': '0 9 * * 1,3,5'}, 'UTC'),
    ('Mon..Fri *-*-* 09:00:00', {'type': 'cron', 'expression': '0 9 * * 1,2,3,4,5'}, 'UTC'),
    ('*-*-1,3,5 06:00:00', {'type': 'cron', 'expression': '0 6 1,3,5 * *'}, 'UTC'),
    ('*-*-1,3,5,7,9,11,13,15,17,19,21,23,25,27,29,31 06:00:00',
     {'type': 'cron', 'expression': '0 6 1,3,5,7,9,11,13,15,17,19,21,23,25,27,29,31 * *'}, 'UTC'),
]

SKIPPED = [
    '*-*-* *:*:*',
    '*-*-* 09:00:30',
    '*:0/5:30',
    '*-*-* 02:21:05 UTC',
    'Mon *-*-* 06:41:05 UTC',
    '*-*-* 08:00:00 Not/A Zone',
    '2026-01-01 08:00:00',
    '*-1-* 08:00:00',
    'Mon *-*-1 08:00:00',
    '*:60:00',
    '*:0/0',
    '25:00:00',
    '*:0/5:0/30',
    'Funday *-*-* 08:00:00',
    'minutely',
    'Mon',
    '',
]


class OnCalendarTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.home = base / 'home'
        self.units = self.home / '.config/systemd/user'
        self.units.mkdir(parents=True)
        self.root = base / 'locker'
        self.root.mkdir()
        for name in ('manifest.json', 'LICENSE', '.gitignore'):
            shutil.copyfile(ROOT / name, self.root / name)
        for folder in ('skills', 'rules', 'routines', 'mcp', 'config'):
            shutil.copytree(ROOT / folder, self.root / folder)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.store = FileStore(self.root)
        self.local = self.root / '.hub-store/catalog'

    def timer(self, name, timer_lines):
        (self.units / f'{name}.service').write_text('[Service]\nExecStart=example job\n', encoding='utf-8')
        (self.units / f'{name}.timer').write_text('[Timer]\n' + '\n'.join(timer_lines) + '\n', encoding='utf-8')

    def scan_and_apply(self):
        runner = lambda command: ''
        report = scanner.Inventory(self.home, (), 'linux', {}, runner, self.root).discover()
        routines = [i for i in report['items'] if i['source'].endswith('.timer')]
        selected = [i['id'] for i in routines if i['importable']]
        scanner.apply(self.root, report, selected, self.store, self.home, platform='linux', env={}, runner=runner)
        return routines

    def routine(self, name):
        return json.loads((self.local / f'routines/{name}/routine.json').read_text(encoding='utf-8'))

    def test_calendar_forms_convert_to_schedules(self):
        for index, (calendar, schedule, timezone) in enumerate(CONVERTED):
            self.timer(f'conv-{index}', [f'OnCalendar={calendar}'])
        routines = self.scan_and_apply()
        self.assertEqual(len(routines), len(CONVERTED))
        for index, (calendar, schedule, timezone) in enumerate(CONVERTED):
            with self.subTest(calendar=calendar):
                value = self.routine(f'conv-{index}')
                self.assertEqual(value['schedule'], schedule)
                self.assertEqual(value['timezone'], timezone)
                self.assertFalse(value['enabled'])
                self.assertEqual(value['runner'], 'systemd-timer')

    def test_unconvertible_calendars_stay_skipped(self):
        for index, calendar in enumerate(SKIPPED):
            self.timer(f'skip-{index}', [f'OnCalendar={calendar}'])
        routines = self.scan_and_apply()
        self.assertEqual(len(routines), len(SKIPPED))
        for item, calendar in zip(sorted(routines, key=lambda i: int(i['source'].rsplit('-', 1)[1].split('.')[0])), SKIPPED):
            with self.subTest(calendar=calendar):
                self.assertEqual(item['status'], 'skipped')
                self.assertFalse(item['importable'])

    def test_nonzero_seconds_are_skipped_with_a_manual_conversion_reason(self):
        self.timer('seconds', ['OnCalendar=*-*-* 09:00:30'])
        [item] = self.scan_and_apply()
        self.assertEqual(item['status'], 'skipped')
        self.assertFalse(item['importable'])
        self.assertIn('seconds', item['reason'])
        self.assertIn('manual conversion', item['reason'])

    def test_interval_timer_with_boot_delay_uses_interval(self):
        self.timer('interval', ['OnBootSec=2min', 'OnUnitActiveSec=10min'])
        self.scan_and_apply()
        self.assertEqual(self.routine('interval')['schedule'], {'type': 'interval', 'every_seconds': 600})
        self.assertEqual(self.routine('interval')['timezone'], 'UTC')

    def test_boot_only_timer_stays_skipped(self):
        self.timer('boot', ['OnBootSec=2min'])
        routines = self.scan_and_apply()
        self.assertEqual([i['status'] for i in routines], ['skipped'])


if __name__ == '__main__':
    unittest.main()
