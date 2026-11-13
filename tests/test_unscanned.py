import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import hub
import scanner

ROOT = Path(__file__).resolve().parents[1]
UNSUPPORTED = ('antigravity', 'opencodex', 'pi', 'copilot', 'cagent')


class UnscannedAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.home.mkdir()

    def skill(self, relative, name='demo'):
        path = self.home / relative / name
        path.mkdir(parents=True)
        (path / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: Demo\n---\nBody\n', encoding='utf-8')
        (path / 'notes.md').write_text('support', encoding='utf-8')

    def report(self, platform='linux'):
        return scanner.scan(self.home, platform=platform, env={}, runner=lambda command: '', root=ROOT)

    def command(self, *argv):
        args = hub.parser().parse_args(['scan', '--home', str(self.home), '--platform', 'linux', *argv])
        stdout = io.StringIO()
        with patch.object(sys.stdin, 'isatty', return_value=False), patch.object(scanner, 'default_runner', return_value=''), contextlib.redirect_stdout(stdout):
            code = args.action(args)
        return code, stdout.getvalue()

    def test_unsupported_agent_homes_are_listed_by_name_and_tilde_path_only(self):
        for name in UNSUPPORTED:
            (self.home / ('.' + name)).mkdir()
            (self.home / ('.' + name) / 'secret.txt').write_text('PRIVATE-CONTENT', encoding='utf-8')
        listed = self.report()['not_scanned']['agents']
        self.assertEqual({entry['name']: entry['path'] for entry in listed if entry['name'] in UNSUPPORTED},
                         {name: '~/.' + name for name in UNSUPPORTED})
        for entry in listed:
            self.assertEqual(set(entry), {'name', 'path'})
        self.assertNotIn(str(self.home), json.dumps(listed))
        self.assertNotIn('PRIVATE-CONTENT', json.dumps(listed))

    def test_absent_homes_are_not_listed(self):
        self.assertEqual(self.report()['not_scanned']['agents'], [])

    def test_json_and_human_output_name_the_manual_import_commands(self):
        (self.home / '.pi').mkdir()
        _, data = self.command('--json')
        section = json.loads(data)['not_scanned']
        self.assertEqual(section['agents'], [{'name': 'pi', 'path': '~/.pi'}])
        self.assertIn('import-skill', section['next_step'])
        self.assertIn('import-rule', section['next_step'])
        _, text = self.command()
        self.assertIn('Not scanned', text)
        self.assertIn('pi ~/.pi', text)
        self.assertIn(section['next_step'], text)

    def test_human_output_omits_the_section_when_nothing_is_unscanned(self):
        _, text = self.command()
        self.assertNotIn('Not scanned', text)

    def test_cursor_and_grok_skill_packages_are_scanned_as_skills(self):
        self.skill('.grok/skills', 'grok-skill')
        self.skill('.cursor/skills-cursor', 'cursor-skill')
        report = self.report()
        found = {(i['agent'], i['name']): i for i in report['items'] if i['kind'] == 'skill'}
        self.assertEqual(set(found), {('grok', 'grok-skill'), ('cursor', 'cursor-skill')})
        self.assertEqual(found['grok', 'grok-skill']['status'], 'new')
        self.assertEqual(found['grok', 'grok-skill']['source'], '~/.grok/skills/grok-skill')
        self.assertEqual(found['cursor', 'cursor-skill']['source'], '~/.cursor/skills-cursor/cursor-skill')
        self.assertFalse(found['cursor', 'cursor-skill']['importable'])
        self.assertEqual(found['cursor', 'cursor-skill']['ownership'], 'vendor')


if __name__ == '__main__':
    unittest.main()
