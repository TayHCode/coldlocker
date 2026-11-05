import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import scanner

ROOT = Path(__file__).resolve().parents[1]


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def manifest(path, name, version):
    write(path, json.dumps({'name': name, 'version': version}))


class PluginRecordTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / 'home'
        self.home.mkdir()

    def plugins(self, agent):
        report = scanner.Inventory(self.home, (), 'linux', {}, lambda command: '', ROOT).discover()
        return [i for i in report['items'] if i['kind'] == 'plugin' and i['agent'] == agent], report

    def payload(self, agent, name):
        inventory = scanner.Inventory(self.home, (), 'linux', {}, lambda command: '', ROOT)
        report = inventory.discover()
        item = next(i for i in report['items'] if i['kind'] == 'plugin' and i['agent'] == agent and i['name'] == name)
        return inventory.payloads[item['id']][0]

    def codex_fixture(self):
        codex = self.home / '.codex'
        write(codex / 'config.toml', '[plugins."visualize@openai-bundled"]\nenabled = true\n')
        manifest(codex / 'plugins/cache/openai-bundled/visualize/1.0.50/.codex-plugin/plugin.json', 'visualize', '1.0.50')
        manifest(codex / 'plugins/cache/openai-curated-remote/gmail/0.1.10/.codex-plugin/plugin.json', 'gmail', '0.1.10')

    def test_codex_cache_and_config_become_one_record_per_plugin(self):
        self.codex_fixture()
        items, _ = self.plugins('codex')
        self.assertEqual(sorted(i['name'] for i in items), ['gmail-openai-curated-remote', 'visualize-openai-bundled'])

    def test_codex_enabled_follows_config_only(self):
        self.codex_fixture()
        self.assertIs(self.payload('codex', 'visualize-openai-bundled')['enabled'], True)
        self.assertIs(self.payload('codex', 'gmail-openai-curated-remote')['enabled'], False)

    def test_marketplace_stays_in_source_and_version_is_kept(self):
        self.codex_fixture()
        bundled = self.payload('codex', 'visualize-openai-bundled')
        self.assertEqual(bundled['source'], 'visualize@openai-bundled')
        self.assertEqual(bundled['version'], '1.0.50')
        self.assertEqual(self.payload('codex', 'gmail-openai-curated-remote')['source'], 'gmail@openai-curated-remote')

    def claude_fixture(self):
        claude = self.home / '.claude'
        write(claude / 'plugins/installed_plugins.json', json.dumps(
            {'version': 2, 'plugins': {'pstack@cursor-plugins': [{'scope': 'user', 'version': '0.15.27'}]}}))
        write(claude / 'settings.json', json.dumps({'enabledPlugins': {'pstack@cursor-plugins': True}}))

    def test_claude_registry_and_settings_merge_into_one_record(self):
        self.claude_fixture()
        items, _ = self.plugins('claude')
        self.assertEqual([(i['name'], i['status']) for i in items], [('pstack-cursor-plugins', 'new')])
        payload = self.payload('claude', 'pstack-cursor-plugins')
        self.assertEqual((payload['source'], payload['version'], payload['enabled']), ('pstack@cursor-plugins', '0.15.27', True))

    def test_claude_installed_but_not_enabled_is_disabled(self):
        self.claude_fixture()
        write(self.home / '.claude/settings.json', json.dumps({'enabledPlugins': {}}))
        self.assertIs(self.payload('claude', 'pstack-cursor-plugins')['enabled'], False)

    def test_true_conflict_names_the_differing_field(self):
        self.claude_fixture()
        write(self.home / '.claude/plugins/installed_plugins.json', json.dumps(
            {'version': 2, 'plugins': {'pstack@cursor-plugins': [{'scope': 'user', 'version': '0.15.27'}]}}))
        write(self.home / '.claude/settings.json', json.dumps({'enabledPlugins': {'pstack@cursor-plugins': {'enabled': True, 'version': '0.1.0'}}}))
        items, _ = self.plugins('claude')
        self.assertEqual({i['status'] for i in items}, {'conflict'})
        self.assertEqual(len(items), 2)
        for item in items:
            self.assertIn('version', item['reason'])
            self.assertIn('0.15.27', item['reason'])
            self.assertIn('0.1.0', item['reason'])

    def test_human_output_explains_conflict(self):
        self.claude_fixture()
        write(self.home / '.claude/settings.json', json.dumps({'enabledPlugins': {'pstack@cursor-plugins': {'enabled': True, 'version': '0.1.0'}}}))
        _, report = self.plugins('claude')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            scanner.human(report)
        self.assertIn('version differs', out.getvalue())

    def test_cache_versions_without_registry_conflict_on_version(self):
        claude = self.home / '.claude'
        manifest(claude / 'plugins/cache/market/tool/1.0.0/.claude-plugin/plugin.json', 'tool', '1.0.0')
        manifest(claude / 'plugins/cache/market/tool/2.0.0/.claude-plugin/plugin.json', 'tool', '2.0.0')
        items, _ = self.plugins('claude')
        self.assertEqual({(i['name'], i['status']) for i in items}, {('tool-market', 'conflict')})
        self.assertTrue(all('version differs' in i['reason'] for i in items))

    def test_registry_version_wins_over_older_cache_versions(self):
        self.claude_fixture()
        claude = self.home / '.claude'
        manifest(claude / 'plugins/cache/cursor-plugins/pstack/0.15.26/.claude-plugin/plugin.json', 'pstack', '0.15.26')
        manifest(claude / 'plugins/cache/cursor-plugins/pstack/0.15.27/.claude-plugin/plugin.json', 'pstack', '0.15.27')
        items, _ = self.plugins('claude')
        self.assertEqual([(i['name'], i['status']) for i in items], [('pstack-cursor-plugins', 'new')])


if __name__ == '__main__':
    unittest.main()
