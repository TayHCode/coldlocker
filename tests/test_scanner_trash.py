from pathlib import Path
import shutil
import tempfile
import unittest

import scanner
from test_scanner import ROOT

SKILL = '---\nname: {name}\ndescription: A sample skill.\n---\nBody.\n'


class TrashedSkillTests(unittest.TestCase):
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
        self.write(self.home / '.claude/skills/live-skill/SKILL.md', SKILL.format(name='live-skill'))
        self.write(self.home / '.claude/skills/.trash/1760000000000-x/trashed-skill/SKILL.md', SKILL.format(name='trashed-skill'))
        self.write(self.home / '.claude/skills/.trash/1760000000001-y/trashed-skill/SKILL.md', SKILL.format(name='trashed-skill'))
        self.report = scanner.Inventory(self.home, (), 'linux', {}, lambda command: '', self.root).discover()

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def skills(self, name):
        return [i for i in self.report['items'] if i['kind'] == 'skill' and i['name'] == name]

    def test_trashed_skills_produce_no_row(self):
        self.assertEqual(self.skills('trashed-skill'), [])

    def test_live_skill_stays_importable(self):
        self.assertEqual([r['status'] for r in self.skills('live-skill')], ['new'])


if __name__ == '__main__':
    unittest.main()
