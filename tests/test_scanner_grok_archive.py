import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

import scanner

NAMES = ('daily-notes', 'inbox-triage', 'weekly-review', 'release-check')


def skill_text(name):
    return f'---\nname: {name}\ndescription: Box workflow {name}.\n---\nDo {name}.\n'


class GrokArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.root = self.base / 'locker'
        self.root.mkdir()
        (self.root / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'name': 'fixture'}), encoding='utf-8')
        self.export = self.base / 'export'
        self.export.mkdir()
        (self.export / 'routines.json').write_text(json.dumps([
            {'name': 'nightly', 'owner_bot': 'fixture-bot', 'trigger': {'type': 'webhook'}, 'prompt': 'Report.'}]), encoding='utf-8')
        with tarfile.open(self.export / 'skills.tgz', 'w:gz') as archive:
            for name in NAMES:
                data = skill_text(name).encode()
                member = tarfile.TarInfo(f'workflows/{name}/SKILL.md')
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))

    def scan(self):
        inventory = scanner.Inventory(self.home, (), 'linux', {'GROK_EXPORT_DIR': str(self.export)}, lambda command: '', self.root)
        return [i for i in inventory.discover()['items'] if i['agent'] == 'grok']

    def test_unextracted_skills_archive_is_reported_with_extract_step(self):
        items = self.scan()
        archive = [i for i in items if i['status'] == 'skipped']
        self.assertEqual(len(archive), 1, items)
        self.assertEqual(archive[0]['kind'], 'skill')
        self.assertIn('skills.tgz', archive[0]['reason'])
        self.assertIn('Extract skills.tgz into the export folder', archive[0]['next_step'])
        self.assertIn('scan again', archive[0]['next_step'])
        self.assertFalse(archive[0]['importable'])

    def test_extracted_workflows_scan_as_new_and_archive_row_goes_away(self):
        with tarfile.open(self.export / 'skills.tgz') as archive:
            archive.extractall(self.export, filter='data')
        items = self.scan()
        skills = {i['name']: i for i in items if i['kind'] == 'skill'}
        self.assertEqual(set(skills), set(NAMES))
        self.assertEqual({i['status'] for i in skills.values()}, {'new'})
        self.assertEqual([i for i in items if i['status'] == 'skipped'], [])


if __name__ == '__main__':
    unittest.main()
