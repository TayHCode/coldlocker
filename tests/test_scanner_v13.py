import json
import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import connect
import mcp_endpoint
import scanner
from store import FileStore

ROOT = Path(__file__).resolve().parents[1]


class ScannerV13Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.root = self.base / 'locker'
        self.root.mkdir()
        for name in ('manifest.json', 'LICENSE', '.gitignore'):
            shutil.copyfile(ROOT / name, self.root / name)
        for folder in ('skills', 'rules', 'routines', 'mcp', 'config'):
            shutil.copytree(ROOT / folder, self.root / folder)
        self.store = FileStore(self.root)

    def inventory(self, home=None, platform='linux', env=None):
        return scanner.Inventory(home or self.home, (), platform, env, lambda command: '', self.root, live=False)

    def test_claude_desktop_mcp_is_imported_listed_and_source_is_unchanged(self):
        for platform in ('darwin', 'win32'):
            with self.subTest(platform=platform):
                home = self.base / f'home-{platform}'
                home.mkdir()
                if platform == 'darwin':
                    config = home / 'Library/Application Support/Claude/claude_desktop_config.json'
                    env = {}
                else:
                    appdata = self.base / 'appdata'
                    config = appdata / 'Claude/claude_desktop_config.json'
                    env = {'APPDATA': str(appdata)}
                config.parent.mkdir(parents=True)
                config.write_text(json.dumps({'mcpServers': {
                    f'desktop-{platform}': {'command': 'python3', 'args': ['-c', 'print(1)']}
                }}) + '\n', encoding='utf-8')
                before = config.read_bytes()
                inventory = self.inventory(home, platform, env)
                report = inventory.discover()
                [item] = [i for i in report['items'] if i['kind'] == 'mcp_server' and i['name'] == f'desktop-{platform}']
                self.assertEqual(item['status'], 'new')
                result = scanner.apply(self.root, report, [item['id']], self.store, home,
                                       platform=platform, env=env, runner=lambda command: '', live=False)
                self.assertEqual(result['counts'], {'imported': 1, 'rejected': 0})
                listed = mcp_endpoint.inventory(self.root, 'mcp_servers')
                self.assertIn(f'desktop-{platform}', json.dumps(listed))
                self.assertEqual(config.read_bytes(), before)

    def test_codex_home_override_scans_config_skills_and_automations(self):
        alternate = self.base / 'alternate-codex'
        skill = alternate / 'skills/alternate-skill/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('---\nname: alternate-skill\ndescription: Active Codex skill\n---\nRead this.\n', encoding='utf-8')
        config = alternate / 'config.toml'
        config.write_text('[mcp_servers.alternate-server]\ncommand = "node"\nargs = ["fixture.js"]\n', encoding='utf-8')
        automation = alternate / 'automations/alternate-task/automation.toml'
        automation.parent.mkdir(parents=True)
        automation.write_text('prompt = "Check the fixture."\nrrule = "RRULE:FREQ=DAILY;BYHOUR=9"\n', encoding='utf-8')
        env = {'CODEX_HOME': str(alternate)}

        report = self.inventory(env=env).discover()
        found = {(item['kind'], item['name']) for item in report['items'] if item['agent'] == 'codex'}
        self.assertTrue({('skill', 'alternate-skill'), ('mcp_server', 'alternate-server'),
                         ('routine', 'alternate-task')} <= found)
        self.assertEqual(connect.detect(self.home, 'linux', env)['codex'], alternate / 'config.toml')
        self.assertNotIn(str(alternate), json.dumps(report))

    def test_explicit_fixture_home_ignores_ambient_codex_home(self):
        alternate = self.base / 'ambient-codex'
        skill = alternate / 'skills/ambient-leak/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('---\nname: ambient-leak\ndescription: Ambient Codex skill\n---\nDo not scan.\n', encoding='utf-8')
        with patch.dict(os.environ, {'CODEX_HOME': str(alternate)}):
            report = self.inventory(env=None).discover()
        self.assertFalse(any(item['name'] == 'ambient-leak' for item in report['items']))

    def test_codex_home_symlink_is_not_scanned(self):
        target = self.base / 'real-codex'
        skill = target / 'skills/symlinked-skill/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('---\nname: symlinked-skill\ndescription: Symlink fixture\n---\nSkip.\n', encoding='utf-8')
        link = self.base / 'codex-link'
        link.symlink_to(target, target_is_directory=True)
        report = self.inventory(env={'CODEX_HOME': str(link)}).discover()
        self.assertFalse(any(item['name'] == 'symlinked-skill' for item in report['items']))

    def test_large_codex_skill_library_reports_all_1001_packages(self):
        base = self.home / '.codex/skills'
        for index in range(1001):
            letters = ''.join(chr(97 + (index // (26 ** power)) % 26) for power in (2, 1, 0))
            name = f'skill-{letters}'
            package = base / name
            package.mkdir(parents=True)
            (package / 'SKILL.md').write_text(
                f'---\nname: {name}\ndescription: Fixture package {index}\n---\nBody.\n', encoding='utf-8')
        report = self.inventory().discover()
        names = {item['name'] for item in report['items'] if item['agent'] == 'codex' and item['kind'] == 'skill'}
        self.assertEqual(len(names), 1001)

    def test_inventory_names_when_global_walk_limit_is_reached(self):
        base = self.home / '.claude/skills'
        for index in range(5001):
            (base / f'entry-{index:04}').mkdir(parents=True)
        report = self.inventory().discover()
        self.assertEqual(report['incomplete_inventory'], [{'source': '~/.claude/skills', 'limit': 5000}])
        self.assertNotIn(str(self.home), json.dumps(report['incomplete_inventory']))
        output = io.StringIO()
        with patch('sys.stdout', output):
            scanner.human(report)
        self.assertIn('Incomplete inventory: ~/.claude/skills reached the limit of 5000 entries.', output.getvalue())


if __name__ == '__main__':
    unittest.main()
