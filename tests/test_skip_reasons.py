import contextlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import scanner
from test_scanner import ROOT

TAILNET = '.'.join(str(octet) for octet in range(100, 104))
HOME_PATH = '/' + 'home' + '/someone/project'
CANARY = 'canary-secret-value-0002'


class SkipReasonTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.home = base / 'home'
        self.root = base / 'locker'
        self.root.mkdir()
        for name in ('manifest.json', 'LICENSE', '.gitignore'):
            shutil.copyfile(ROOT / name, self.root / name)
        for folder in ('skills', 'rules', 'routines', 'mcp'):
            shutil.copytree(ROOT / folder, self.root / folder)
        self.build()
        self.report = scanner.Inventory(self.home, (), 'linux', {}, lambda command: '', self.root).discover()

    def write(self, relative, text):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def build(self):
        self.write('.claude/skills/big/SKILL.md', '---\nname: big\ndescription: Large package\n---\nBody\n')
        self.write('.claude/skills/big/data.txt', 'x' * (scanner.LIMIT + 1))
        self.write('.claude/CLAUDE.md', f'# Rules\nBe brief.\nHost {TAILNET} is private.\n')
        self.write('.codex/AGENTS.md', f'# Rules\nWork in {HOME_PATH}.\n')
        self.write('.claude.json', json.dumps({'mcpServers': {'indirect': {'command': 'tool', 'env': {'KEY': '${SOME_KEY}'}}}}))
        self.write('.config/systemd/user/weekly.timer', '[Timer]\nOnCalendar=Mon *-*-1 09:00\n')
        self.write('.config/systemd/user/weekly.service', '[Service]\nExecStart=/bin/true\n')
        self.write('.config/systemd/user/orphan.timer', '[Timer]\nOnCalendar=daily\n')
        self.write('.config/systemd/user/leaky.timer', '[Timer]\nOnCalendar=daily\n')
        self.write('.config/systemd/user/leaky.service', f'[Service]\nExecStart=/bin/curl http://{TAILNET}:9222\n')

    def skipped(self, name):
        return next(i for i in self.report['items'] if i['name'] == name and i['status'] == 'skipped')

    def test_every_skipped_item_has_reason_and_next_step(self):
        skipped = [i for i in self.report['items'] if i['status'] == 'skipped']
        self.assertGreaterEqual(len(skipped), 6)
        for item in skipped:
            self.assertTrue(item.get('reason'), item)
            self.assertTrue(item.get('next_step'), item)
            self.assertIn('needs-manual', item['flags'])

    def test_reasons_name_the_concrete_cause(self):
        self.assertIn('package too large', self.skipped('big')['reason'])
        claude = self.skipped('claude')['reason']
        self.assertIn('tailnet-ip at line 3', claude)
        self.assertIn('home-path at line 2', self.skipped('agents')['reason'])
        self.assertIn('environment indirection', self.skipped('indirect')['reason'])
        self.assertIn('unsupported OnCalendar', self.skipped('weekly')['reason'])
        self.assertIn('service file missing', self.skipped('orphan')['reason'])
        self.assertIn('tailnet-ip at line 2', self.skipped('leaky')['reason'])

    def test_reasons_never_carry_values(self):
        text = json.dumps(self.report)
        self.assertNotIn(TAILNET, text)
        self.assertNotIn(HOME_PATH, text)

    def test_human_view_prints_reason_and_next_step(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            scanner.human(self.report)
        lines = output.getvalue().splitlines()
        for name in ('big', 'claude', 'orphan'):
            item = self.skipped(name)
            line = next(l for l in lines if item['id'] in l)
            self.assertIn(item['reason'], line)
            self.assertIn(item['next_step'], '\n'.join(lines[lines.index(line):lines.index(line) + 3]))

    def test_codex_execpolicy_rules_are_reported_as_skipped(self):
        self.write('.codex/rules/default.rules', 'prefix_rule(pattern=["git", "status"], decision="allow")\n')
        self.write('.codex/rules/extra.rules', 'prefix_rule(pattern=["ls"], decision="allow")\n')
        self.write('.codex/rules/notes.txt', 'not a rules file\n')
        report = scanner.Inventory(self.home, (), 'linux', {}, lambda command: '', self.root).discover()
        rows = [i for i in report['items'] if i['kind'] == 'rule' and i['agent'] == 'codex' and i['source'].startswith('~/.codex/rules/')]
        self.assertEqual(sorted(i['source'] for i in rows), ['~/.codex/rules/default.rules', '~/.codex/rules/extra.rules'])
        for item in rows:
            self.assertEqual(item['status'], 'skipped')
            self.assertFalse(item['importable'])
            self.assertEqual(item['reason'], 'Codex command-approval rules are not imported')
            self.assertTrue(item['next_step'])
            self.assertIn('needs-manual', item['flags'])
        self.assertNotIn('prefix_rule', json.dumps(report))


if __name__ == '__main__':
    unittest.main()
