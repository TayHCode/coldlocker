"""Shipped skills are opt-in packs: a new install lists only hub-discover and the user's imports."""
import json
import shutil
import subprocess
import sys
import unittest

from checks import validators
from test_local_catalog_proof import proof


SHIPPED = sorted(entry['name'] for entry in validators.shipped(proof.ROOT)['skills'])
PSTACK = len(SHIPPED) - 1
STATE = '.hub-store/packs.json'


def list_skills(fixture):
    result = fixture.rpc([fixture.initialize(), {'method': 'notifications/initialized'},
                          {'id': 2, 'method': 'tools/call', 'params': {'name': 'hub_list_skills', 'arguments': {}}}])[1]
    size = len(json.dumps({'jsonrpc': '2.0', 'id': 2, 'result': result}).encode('utf-8'))
    return sorted(item['name'] for item in json.loads(result['content'][0]['text'])), size


def list_error(fixture):
    requests = [fixture.initialize(), {'method': 'notifications/initialized'},
                {'id': 2, 'method': 'tools/call', 'params': {'name': 'hub_list_skills', 'arguments': {}}}]
    lines = ''.join(json.dumps({'jsonrpc': '2.0', **request}) + '\n' for request in requests)
    output = subprocess.run([sys.executable, 'mcp_endpoint.py'], cwd=fixture.root, env=fixture.env, input=lines,
                            text=True, encoding='utf-8', capture_output=True, timeout=45, check=True).stdout
    return json.loads(output.splitlines()[1])['error']['message']


def state(fixture):
    return json.loads((fixture.root / STATE).read_text(encoding='utf-8'))


class PackTests(unittest.TestCase):
    def install(self, fixture):
        for name in ('LICENSE', '.gitignore'):
            shutil.copyfile(proof.ROOT / name, fixture.root / name)

    def seed(self, fixture):
        shutil.copytree(fixture.skill, fixture.home / '.claude/skills/catalog-sample')
        fixture.cli('scan', '--home', fixture.home, '--agent', 'claude', '--import', '--yes')

    def doctor(self, fixture, expected=0):
        guard = fixture.env.pop('PYTHONPATH')
        try:
            return fixture.cli('doctor', expected=expected)
        finally:
            fixture.env['PYTHONPATH'] = guard

    def test_fresh_install_lists_hub_discover_and_the_import_until_pstack_is_added(self):
        with proof.Fixture() as fixture:
            self.install(fixture)
            self.seed(fixture)
            names, first = list_skills(fixture)
            self.assertEqual(names, ['catalog-sample', 'hub-discover'])
            self.assertLess(first, 4096)
            self.assertEqual(state(fixture), {'version': 1, 'enabled': []})
            self.assertIn(f'pstack  off  {PSTACK} skills', fixture.cli('pack', 'list').stdout)
            self.assertEqual(['catalog-sample', 'hub-discover'],
                             [row['name'] for row in json.loads(fixture.cli('list', '--kind', 'skills', '--json').stdout)])
            self.assertIn(f'Added pack pstack: {PSTACK} shipped skills', fixture.cli('pack', 'add', 'pstack').stdout)
            names, size = list_skills(fixture)
            self.assertEqual(names, sorted(SHIPPED + ['catalog-sample']))
            self.assertGreater(size, first)
            if (proof.ROOT / validators.POLICY_PACK).is_file():
                self.assertGreater(size, 20000)
            self.assertIn('nothing changed', fixture.cli('pack', 'add', 'pstack').stdout)
            self.assertIn('PASS: skill packs', self.doctor(fixture).stdout)

    def test_a_catalog_call_before_the_first_import_keeps_a_new_install_new(self):
        with proof.Fixture() as fixture:
            self.assertEqual(list_skills(fixture)[0], ['hub-discover'])
            self.assertTrue((fixture.root / '.hub-store/calls.jsonl').is_file())
            self.assertEqual(state(fixture), {'version': 1, 'enabled': []})
            self.seed(fixture)
            self.assertEqual(list_skills(fixture)[0], ['catalog-sample', 'hub-discover'])

    def test_fresh_link_creates_only_listed_skills(self):
        with proof.Fixture() as fixture:
            project = fixture.base / 'project'
            (project / '.git').mkdir(parents=True)
            self.seed(fixture)
            fixture.cli('link', '--target', project)
            self.assertEqual(sorted(path.name for path in (project / '.claude/skills').iterdir()),
                             ['catalog-sample', 'hub-discover'])
            fixture.cli('pack', 'add', 'pstack')
            fixture.cli('link', '--target', project)
            self.assertEqual(sorted(path.name for path in (project / '.claude/skills').iterdir()),
                             sorted(SHIPPED + ['catalog-sample']))

    def test_upgrade_from_an_install_made_before_packs_keeps_every_shipped_skill(self):
        earlier = {'.hub-store/catalog/manifest.json': None,
                   '.hub-store/links.json': '{"version": 1, "links": []}\n',
                   '.hub-store/calls.jsonl': ''}
        for marker, text in earlier.items():
            with self.subTest(marker=marker), proof.Fixture() as fixture:
                self.install(fixture)
                if text is None:
                    self.seed(fixture)
                    (fixture.root / STATE).unlink()
                else:
                    (fixture.root / marker).parent.mkdir(parents=True)
                    (fixture.root / marker).write_text(text, encoding='utf-8')
                expected = sorted(SHIPPED + (['catalog-sample'] if text is None else []))
                self.assertEqual(list_skills(fixture)[0], expected)
                self.assertIn(f'pstack  on  {PSTACK} skills', fixture.cli('pack', 'list').stdout)
                fixture.cli('import-skill', fixture.skill, '--origin', 'scan-claude' if text is None else 'local-import')
                self.assertEqual(state(fixture), {'version': 1, 'enabled': ['pstack']})
                self.assertEqual(list_skills(fixture)[0], sorted(SHIPPED + ['catalog-sample']))
                self.assertIn('PASS: skill packs', self.doctor(fixture).stdout)

    def test_removing_a_pack_keeps_skill_files_and_existing_links(self):
        with proof.Fixture() as fixture:
            project = fixture.base / 'project'
            (project / '.git').mkdir(parents=True)
            self.seed(fixture)
            (fixture.root / STATE).unlink()
            fixture.cli('link', '--target', project)
            link = project / '.claude/skills/i-have-adhd'
            self.assertTrue(link.is_symlink())
            removed = fixture.cli('pack', 'remove', 'pstack').stdout
            self.assertIn(f'Removed pack pstack: its {PSTACK} shipped skills are no longer listed', removed)
            self.assertIn(f'Link left in place (created by hub.py link): {link}', removed)
            self.assertTrue(link.is_symlink() and (link / 'SKILL.md').is_file())
            self.assertTrue((fixture.root / 'skills/i-have-adhd/SKILL.md').is_file())
            self.assertEqual(list_skills(fixture)[0], ['catalog-sample', 'hub-discover'])
            self.assertEqual(state(fixture), {'version': 1, 'enabled': []})

    def test_unknown_pack_and_bad_pack_files_are_refused(self):
        with proof.Fixture() as fixture:
            self.install(fixture)
            self.assertIn('unknown pack nope', fixture.cli('pack', 'add', 'nope', expected=1).stderr)
            self.assertFalse((fixture.root / STATE).exists())
            (fixture.root / '.hub-store').mkdir()
            (fixture.root / STATE).write_text('{"version": 1, "enabled": ["nope"]}\n', encoding='utf-8')
            self.assertIn('FAIL: skill packs', self.doctor(fixture, expected=1).stdout)
            self.assertEqual(list_error(fixture), 'Invalid manifest')
            (fixture.root / STATE).unlink()
            definitions = json.loads((fixture.root / 'packs.json').read_text(encoding='utf-8'))
            definitions['packs']['pstack'].remove('i-have-adhd')
            (fixture.root / 'packs.json').write_text(json.dumps(definitions), encoding='utf-8')
            self.assertIn('packs.json must list every shipped skill exactly once', self.doctor(fixture, expected=1).stdout)
            (fixture.root / 'packs.json').unlink()
            self.assertIn('SKIP: skill packs: no packs.json, so every shipped skill is listed', self.doctor(fixture).stdout)
            self.assertEqual(list_skills(fixture)[0], SHIPPED)


if __name__ == '__main__':
    unittest.main()
