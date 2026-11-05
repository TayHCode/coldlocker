import json
from pathlib import Path
import tempfile
import unittest

import importers
from checks import validators


FIXTURES = Path(__file__).parent / 'fixtures' / 'ui'


class ImporterTests(unittest.TestCase):
    def parse(self, relative):
        path = FIXTURES / relative
        return importers.parse_mcp(path.read_text(encoding='utf-8'), path.name)

    def test_claude_project_stdio_masks_every_env_value(self):
        servers, credentials = self.parse('claude/.mcp.json')
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]['transport'], 'stdio')
        self.assertEqual(set(credentials.values()), {'synthetic-local-value', 'fixture-mode-value'})
        for value in servers[0]['env'].values():
            self.assertTrue(value.startswith('secret://'))
        validators.mcp(servers[0])

    def test_claude_global_and_project_servers(self):
        servers, credentials = self.parse('claude/.claude.json')
        self.assertEqual([server['name'] for server in servers], ['fixture-global', 'fixture-project'])
        self.assertEqual(servers[1]['transport'], 'http')
        self.assertEqual(list(credentials.values()), ['synthetic-project-value'])

    def test_codex_toml_both_transports(self):
        servers, credentials = self.parse('codex/config.toml')
        self.assertEqual([server['transport'] for server in servers], ['stdio', 'http'])
        self.assertEqual(len(credentials), 2)
        self.assertIn('headers', servers[1])

    def test_codex_toml_underscored_names_are_slugged(self):
        path = FIXTURES / 'codex/underscored-config.toml'
        servers, _ = importers.parse_mcp(path.read_text(encoding='utf-8'), path.name.replace('underscored-', ''))
        self.assertEqual([server['name'] for server in servers], ['codex-task-local', 'vendor-docs'])

    def test_cursor_http_and_custom_headers(self):
        servers, credentials = self.parse('cursor/mcp.json')
        self.assertEqual(servers[0]['transport'], 'http')
        self.assertEqual(len(credentials), 2)
        self.assertNotIn('synthetic-context-value', json.dumps(servers))

    def test_reject_invalid_and_duplicate_names_or_fields(self):
        for text in ('{"mcpServers":{"Bad":{}}}', '{"mcpServers":{},"mcpServers":{}}',
                     '{"mcpServers":[]}', '[]', '{"mcpServers":{"sample":{"type":"ftp"}}}'):
            with self.subTest(text=text), self.assertRaises((ValueError, AttributeError)):
                importers.parse_mcp(text, '.mcp.json')

    def test_reject_credentials_in_url_and_sensitive_command(self):
        for change in ({'url': 'https://example.invalid/mcp?credential=unsafe'},
                       {'command': '/' + 'home' + '/fixture/tool'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                importers.parse_mcp(json.dumps({'mcpServers': {'sample': change}}), '.mcp.json')

    def test_reject_credential_repeated_in_public_fields(self):
        for value in ('synthetic-private-value', 'synthetic-\"private-value', 'synthetic-\nprivate-value'):
            source = {'mcpServers': {'sample': {'command': 'example', 'args': [value],
                                              'env': {'TOKEN': value}}}}
            with self.subTest(value=value), self.assertRaises(ValueError):
                importers.parse_mcp(json.dumps(source), '.mcp.json')

    def test_reject_environment_indirection(self):
        for field in ('bearer_token_env_var', 'env_http_headers', 'env_vars'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                importers.parse_mcp(json.dumps({'mcpServers': {'sample': {'command': 'example', field: 'UNRESOLVED'}}}), '.mcp.json')

    def test_routine_forced_disabled_and_validated(self):
        value = importers.parse_routine((FIXTURES / 'routines/routine.json').read_text(encoding='utf-8'))
        self.assertIs(value['enabled'], False)
        value['owner'] = ['one', 'two']
        with self.assertRaises(ValueError):
            importers.parse_routine(json.dumps(value))

    def test_discovery_only_root_and_known_locations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ('skills', '.agents/skills', '.claude/skills', '.codex/skills', '.cursor/skills', 'unrelated/deeper'):
                package = root / relative / 'sample-skill'
                package.mkdir(parents=True)
                (package / 'SKILL.md').write_bytes((FIXTURES / 'skills/sample-skill/SKILL.md').read_bytes())
            found = importers.discover_skills(root)
            self.assertEqual(len(found), 5)
            self.assertFalse(any('unrelated' in item['path'] for item in found))

    def test_path_escape_symlink_unexpected_and_large_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / '.mcp.json'
            target.write_text('{}', encoding='utf-8')
            with self.assertRaises(ValueError):
                importers.selected_path(root, '../outside')
            (root / 'link').symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                importers.read_file(root, 'link/.mcp.json', importers.CONFIG_NAMES)
            with self.assertRaises(ValueError):
                importers.read_file(root, '.mcp.json', {'mcp.json'})
            target.write_bytes(b' ' * (importers.LIMIT + 1))
            with self.assertRaises(ValueError):
                importers.read_file(root, '.mcp.json', importers.CONFIG_NAMES)

    def test_skill_private_content_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'sample-skill'
            root.mkdir()
            (root / 'SKILL.md').write_bytes((FIXTURES / 'skills/sample-skill/SKILL.md').read_bytes())
            link = root / 'link'
            link.symlink_to('SKILL.md')
            with self.assertRaises(ValueError):
                importers.skill_package(root)
            link.unlink()
            (root / 'data').write_text('sk' + '-' + 'a' * 30, encoding='utf-8')
            with self.assertRaises(ValueError):
                importers.skill_package(root)
