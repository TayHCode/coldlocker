import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

import scanner


class CrontabReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.stub = self.base / 'bin'
        self.stub.mkdir()
        self.root = self.base / 'locker'
        self.root.mkdir()
        (self.root / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'name': 'fixture'}), encoding='utf-8')

    def fake_crontab(self, body):
        script = self.stub / 'crontab'
        script.write_text('#!/bin/sh\n' + body + '\n', encoding='utf-8')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

    def scan(self):
        saved = os.environ['PATH']
        os.environ['PATH'] = f'{self.stub}{os.pathsep}{saved}'
        try:
            return scanner.scan(self.home, platform='linux', env={}, root=self.root)
        finally:
            os.environ['PATH'] = saved

    def crontab_items(self, report):
        return [i for i in report['items'] if i['source'] == '<system:crontab>']

    @unittest.skipIf(os.name == 'nt', 'the crontab stub is a POSIX shell script')
    def test_denied_crontab_read_is_reported_not_silent(self):
        self.fake_crontab('echo "crontab: Operation not permitted" >&2\nexit 1')
        items = self.crontab_items(self.scan())
        self.assertEqual([(i['status'], i['reason']) for i in items], [('skipped', 'file unreadable')])

    @unittest.skipIf(os.name == 'nt', 'the crontab stub is a POSIX shell script')
    def test_timed_out_crontab_read_is_reported(self):
        self.fake_crontab('exec sleep 30')
        items = self.crontab_items(self.scan())
        self.assertEqual([(i['status'], i['reason']) for i in items], [('skipped', 'file unreadable')])

    @unittest.skipIf(os.name == 'nt', 'the crontab stub is a POSIX shell script')
    def test_missing_crontab_binary_is_reported(self):
        saved = os.environ['PATH']
        os.environ['PATH'] = str(self.stub)
        try:
            report = scanner.scan(self.home, platform='linux', env={}, root=self.root)
        finally:
            os.environ['PATH'] = saved
        self.assertEqual([i['status'] for i in self.crontab_items(report)], ['skipped'])

    @unittest.skipIf(os.name == 'nt', 'the crontab stub is a POSIX shell script')
    def test_no_crontab_for_user_stays_silent(self):
        self.fake_crontab('echo "no crontab for someone" >&2\nexit 1')
        self.assertEqual(self.crontab_items(self.scan()), [])

    @unittest.skipIf(os.name == 'nt', 'the crontab stub is a POSIX shell script')
    def test_readable_crontab_is_imported(self):
        self.fake_crontab('echo "0 9 * * 1 /usr/local/bin/weekly-report"')
        items = self.crontab_items(self.scan())
        self.assertEqual([(i['name'], i['status']) for i in items], [('cron-1', 'new')])


if __name__ == '__main__':
    unittest.main()
