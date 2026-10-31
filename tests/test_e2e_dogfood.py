"""Replay documented user workflows through real CLI and stdio subprocesses."""
import json
import shutil
import unittest

from test_local_catalog_proof import proof


class DogfoodTests(unittest.TestCase):
    def test_missing_routine_prompt_is_rejected_without_catalog_changes(self):
        with proof.Fixture() as fixture:
            fixture.cli('import-skill', fixture.skill)
            catalog = fixture.root / '.hub-store/catalog'
            before = {p.relative_to(catalog): p.read_bytes()
                      for p in catalog.rglob('*') if p.is_file()}
            prompt = fixture.routine.parent / 'prompt.md'
            original = prompt.read_bytes()
            prompt.unlink()
            result = fixture.cli('import-routine', fixture.routine, expected=1)
            self.assertIn('prompt must be an ordinary file', result.stderr)
            self.assertEqual(before, {p.relative_to(catalog): p.read_bytes()
                                      for p in catalog.rglob('*') if p.is_file()})
            self.assertFalse((fixture.root / '.hub-store/history').exists())
            prompt.write_bytes(original)
            fixture.cli('import-routine', fixture.routine)
            responses = fixture.rpc([
                fixture.initialize(), {'method': 'notifications/initialized'},
                {'id': 2, 'method': 'tools/call', 'params': {
                    'name': 'hub_get_item', 'arguments': {
                        'kind': 'routine', 'name': 'catalog-sample-routine'}}}])
            item = responses[1]
            self.assertFalse(item['isError'])
            self.assertIn('Read the fixture guide without changes.\n',
                          json.loads(item['content'][0]['text'])['files'].values())

    def test_import_connect_fetch_update_remove(self):
        with proof.Fixture() as fixture:
            result = fixture.prove()
            self.assertEqual(result['status'], 'passed')
            self.assertTrue(result['commands'])
            self.assertEqual({step['exit_code'] for step in result['commands']}, {0, 1})

    def test_scan_import_update_doctor_and_links(self):
        with proof.Fixture() as fixture:
            for name in ('LICENSE', '.gitignore'):
                shutil.copyfile(proof.ROOT / name, fixture.root / name)
            source = fixture.home / '.claude/skills/catalog-sample'
            shutil.copytree(fixture.skill, source)
            scan = ('scan', '--home', fixture.home, '--agent', 'claude')
            before = {p.relative_to(fixture.home): p.read_bytes()
                      for p in fixture.home.rglob('*') if p.is_file()}
            inventory = json.loads(fixture.cli(*scan, '--json').stdout)
            self.assertIn('catalog-sample', json.dumps(inventory))
            self.assertFalse((fixture.root / '.hub-store').exists())
            self.assertEqual(before, {p.relative_to(fixture.home): p.read_bytes()
                                     for p in fixture.home.rglob('*') if p.is_file()})
            fixture.cli(*scan, '--import', '--yes')
            manifest_path = fixture.root / '.hub-store/catalog/manifest.json'
            manifest = json.loads(manifest_path.read_text())
            entry = next(e for e in manifest['skills'] if e['name'] == 'catalog-sample')
            self.assertEqual(entry['provenance']['origin'], 'scan-claude')
            fixture.cli(*scan, '--import', '--yes')
            self.assertEqual(json.loads(manifest_path.read_text()), manifest)
            guide = source / 'references/guide.md'
            guide.write_text('Updated scanned guide.\n', encoding='utf-8')
            fixture.cli('import-skill', source, expected=1)
            fixture.cli('import-skill', source, '--origin', 'scan-claude')
            requests = [fixture.initialize(), {'method': 'notifications/initialized'},
                        {'id': 2, 'method': 'tools/call', 'params': {
                            'name': 'hub_get_item', 'arguments': {
                                'kind': 'skill', 'name': 'catalog-sample'}}}]
            item = fixture.rpc(requests)[1]
            self.assertFalse(item['isError'])
            content = json.loads(item['content'][0]['text'])
            self.assertIn('Updated scanned guide.\n', content['files'].values())
            guard = fixture.env.pop('PYTHONPATH')
            try:
                fixture.cli('doctor')
            finally:
                fixture.env['PYTHONPATH'] = guard
            project = fixture.base / 'target-project'
            (project / '.git').mkdir(parents=True)
            fixture.cli('link', '--target', project, '--dry-run')
            self.assertFalse((project / '.agents').exists())
            fixture.cli('link', '--target', project)
            for client in ('.claude', '.agents'):
                link = project / client / 'skills/catalog-sample'
                self.assertTrue(link.is_symlink())
                self.assertEqual((link / 'references/guide.md').read_text(), 'Updated scanned guide.\n')
            self.assertIn('Nothing to do', fixture.cli('link', '--target', project).stdout)


if __name__ == '__main__':
    unittest.main()
