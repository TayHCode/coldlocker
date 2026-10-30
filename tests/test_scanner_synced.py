from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import scanner

ROOT = Path(__file__).resolve().parents[1]


def skill(path, name, body='Body'):
    path.mkdir(parents=True)
    (path / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: {name} skill\n---\n{body}\n')


class SyncedSkillTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / 'home'
        skills = self.home / '.claude/skills'
        skill(skills / 'synced/acct_org/pdf', 'pdf', 'hosted pdf')
        skill(skills / 'synced/acct_org/docx', 'docx')
        skill(skills / 'mine', 'mine')
        skill(self.home / '.codex/skills/pdf', 'pdf', 'user pdf')
        self.root = Path(self.temporary.name) / 'locker'
        self.root.mkdir()
        for name in ('manifest.json', 'LICENSE', '.gitignore'):
            shutil.copyfile(ROOT / name, self.root / name)
        for folder in ('skills', 'rules', 'routines', 'mcp', 'config'):
            shutil.copytree(ROOT / folder, self.root / folder)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)

    def items(self):
        report = scanner.Inventory(self.home, (), 'linux', {}, lambda command: '', self.root).discover()
        return {(i['agent'], i['name']): i for i in report['items'] if i['kind'] == 'skill'}

    def test_synced_hosted_library_is_vendor(self):
        items = self.items()
        for name in ('pdf', 'docx'):
            item = items[('claude', name)]
            self.assertEqual((item['status'], item['ownership'], item['importable']), ('vendor', 'vendor', False), name)

    def test_synced_pdf_does_not_conflict_with_user_pdf(self):
        items = self.items()
        self.assertEqual(items[('codex', 'pdf')]['status'], 'new')
        self.assertNotIn('conflict', {i['status'] for i in items.values()})

    def test_own_claude_skill_stays_new(self):
        self.assertEqual(self.items()[('claude', 'mine')]['status'], 'new')


if __name__ == '__main__':
    unittest.main()
