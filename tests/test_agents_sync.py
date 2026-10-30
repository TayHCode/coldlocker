import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import agents_sync as connector
import hub
from checks import validators
from gateway_render import placeholder
from windows_text import windows_text_writes


ROOT = Path(__file__).resolve().parents[1]


class AgentsSyncTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.scratch/agents-sync-tests'
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'locker'
        self.root.mkdir()
        self.local = self.root / '.hub-store/catalog'
        self.target = self.base / 'project'
        self.target.mkdir()
        self.home = self.root / '.scratch/home'
        self.manifest = {'schema_version': 1, 'name': 'agentlocker', 'license': 'MIT',
                         'skills': [], 'rules': [], 'mcp_servers': [], 'routines': []}
        hub.write_json(self.root / 'manifest.json', self.manifest)
        self.server = {'transport': 'stdio', 'command': 'example', 'args': [],
                       'env': {'MODE': 'safe', 'API_TOKEN': '${FIXTURE_TOKEN}'}}

    def seed(self, servers=None):
        value = connector.config(self.target)
        value['mcp']['servers'] = servers if servers is not None else {'sample': self.server}
        path = self.target / '.agents/agents.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        hub.write_json(path, value)
        return path

    def skill(self, base, name='sample'):
        path = base / name
        path.mkdir(parents=True)
        (path / 'SKILL.md').write_text('---\nname: ' + name + '\ndescription: Fixture skill\n---\nUse fixture.\n', encoding='utf-8')
        (path / 'resources').mkdir()
        (path / 'resources/data.txt').write_text('supporting file\n', encoding='utf-8')
        return path

    def imported(self):
        self.seed()
        self.skill(self.target / '.agents/skills')
        connector.import_project(self.target, self.root)

    def fake(self, code=0):
        body = ('import json, os, pathlib, sys\n'
                'pathlib.Path("invocation.json").write_text(json.dumps({"args":sys.argv[1:],'
                '"env":dict(os.environ),"cwd":os.getcwd()}))\n'
                'print("suppressed upstream diagnostic")\nsys.exit(' + str(code) + ')\n')
        if os.name == 'nt':
            (self.base / 'agents.py').write_text(body, encoding='utf-8')
            binary = self.base / 'agents.cmd'
            binary.write_text('@"' + sys.executable + '" "%~dp0agents.py" %*\r\n', encoding='utf-8')
            return str(binary)
        binary = self.base / 'agents'
        binary.write_text('#!' + sys.executable + '\n' + body, encoding='utf-8')
        binary.chmod(0o700)
        return str(binary)

    def test_parse_transports_and_placeholder_references(self):
        server = connector.parse_server('sample', self.server)
        self.assertEqual(server['env']['MODE'], 'safe')
        self.assertTrue(server['env']['API_TOKEN'].startswith('secret://agents-'))
        source = {'transport': 'http', 'url': 'https://example.invalid/mcp',
                  'headers': {'Authorization': placeholder('secret://sample-token')}}
        self.assertEqual(connector.parse_server('http', source)['headers']['Authorization'], 'secret://sample-token')

    def test_whole_skill_import_and_provenance_idempotence(self):
        self.imported()
        before = (self.local / 'manifest.json').read_bytes()
        connector.import_project(self.target, self.root)
        self.assertEqual(before, (self.local / 'manifest.json').read_bytes())
        value = hub.read_json(self.local / 'manifest.json')
        for entry in value['skills'] + value['mcp_servers']:
            self.assertEqual(entry['provenance']['origin'], 'agents-sync')
        self.assertEqual((self.local / 'skills/sample/resources/data.txt').read_text(encoding='utf-8'), 'supporting file\n')

    def test_import_writes_server_definition_without_translating_newlines(self):
        self.seed()
        with windows_text_writes():
            connector.import_project(self.target, self.root)
        self.assertNotIn(b'\r', (self.local / 'mcp/servers/sample.json').read_bytes())

    def test_nested_skills_keep_package_support(self):
        self.seed({})
        self.skill(self.target / '.agents/skills/group')
        connector.import_project(self.target, self.root)
        self.assertTrue((self.local / 'skills/sample/resources/data.txt').is_file())

    def test_duplicate_nested_names_rejected_before_import(self):
        self.seed({})
        for name in ('one', 'two'):
            self.skill(self.target / '.agents/skills' / name)
        with self.assertRaises(ValueError):
            connector.import_project(self.target, self.root)
        self.assertEqual(validators.read_local(self.root, hub.read_json)['skills'], [])

    def test_literals_refused_without_writes_or_echo(self):
        secret = 'sk' + '-' + 'a' * 30
        self.server['env']['API_TOKEN'] = secret
        self.seed()
        args = argparse.Namespace(command='agents-import', target=self.target, locker_root=self.root, owner='agentlocker')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(connector.run(args), 1)
        self.assertNotIn(secret, output.getvalue())
        self.assertIn('${VAR}', output.getvalue())
        self.assertFalse((self.local / 'mcp').exists())

    def test_hidden_literals_templates_and_advanced_fields_refused(self):
        for change in ({'env': {'MODE': 'x' * 25}}, {'headers': {'Authorization': 'Bearer ${TOKEN}'}},
                       {'env': {'API_TOKEN': '${TOKEN:-fallback}'}}, {'oauth': {}}, {'enabled': False},
                       {'args': ['--token', 'value']}, {'url': 'https://example.invalid/mcp?key=value'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                connector.parse_server('sample', {**self.server, **change})

    def test_duplicate_json_refused(self):
        path = self.seed()
        path.write_text('{"schemaVersion":4,"schemaVersion":4}', encoding='utf-8')
        with self.assertRaises(ValueError):
            connector.import_project(self.target, self.root)

    def test_export_merge_gateway_idempotence_and_no_store(self):
        self.imported()
        output = self.base / 'output'
        output.mkdir()
        path = output / '.agents/agents.json'
        value = connector.config(output)
        value.update(custom={'preserved': True}, activeProfile='ci', profiles={'ci': {'servers': ['unfamiliar']}})
        value['mcp']['servers']['unfamiliar'] = {'transport': 'stdio', 'command': 'other'}
        path.parent.mkdir()
        hub.write_json(path, value)
        url = 'http://' + '.'.join(('127', '0', '0', '1')) + ':43871/mcp'
        with patch('store.FileStore.resolve', side_effect=AssertionError('must never resolve')):
            connector.export_project(output, self.root, url)
            before = path.read_bytes(), path.stat().st_mtime_ns
            connector.export_project(output, self.root, url)
        self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))
        result = hub.read_json(path)
        self.assertEqual(result['custom'], value['custom'])
        self.assertEqual(result['profiles'], value['profiles'])
        self.assertEqual(result['integrations'], value['integrations'])
        self.assertEqual(result['mcp']['servers']['agentlocker-gateway'], {'transport': 'http', 'url': url})
        self.assertIn('${AGENTLOCKER_SECRET_', path.read_text(encoding='utf-8'))
        self.assertTrue((output / '.agents/skills/sample/resources/data.txt').is_file())

    def test_export_conflict_preflight_preserves_config_and_skills(self):
        self.imported()
        output = self.base / 'output'
        self.skill(output / '.agents/skills')
        (output / '.agents/skills/sample/resources/data.txt').write_text('unfamiliar', encoding='utf-8')
        with self.assertRaises(ValueError):
            connector.export_project(output, self.root)
        self.assertFalse((output / '.agents/agents.json').exists())
        self.assertEqual((output / '.agents/skills/sample/resources/data.txt').read_text(encoding='utf-8'), 'unfamiliar')

    def test_export_server_conflict_preserves_upstream(self):
        self.imported()
        self.server['command'] = 'different'
        path = self.seed()
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            connector.export_project(self.target, self.root)
        self.assertEqual(before, path.read_bytes())

    def test_import_conflict_preflight_does_not_copy_skills(self):
        self.imported()
        shutil.rmtree(self.local / 'skills/sample')
        self.server['command'] = 'different'
        self.seed()
        with self.assertRaises(ValueError):
            connector.import_project(self.target, self.root)
        self.assertFalse((self.local / 'skills/sample').exists())

    def test_source_and_destination_symlinks_refused(self):
        self.seed({})
        source = self.skill(self.target / '.agents/skills')
        (source / 'escape').symlink_to(self.base)
        with self.assertRaises(ValueError):
            connector.import_project(self.target, self.root)
        output = self.base / 'output'
        output.mkdir()
        (output / '.agents').symlink_to(self.target / '.agents')
        with self.assertRaises(ValueError):
            connector.export_project(output, self.root)

    def test_protected_targets_and_ancestors_under_changed_home(self):
        fake_home = self.base / 'fake-home'
        with patch('pathlib.Path.home', return_value=fake_home):
            for relative in ('.claude', '.claude.json', '.claude-backup/a', '.codex/a', '.cursor', '.agents', '.config/opencode/a', '.'):
                with self.subTest(relative=relative), self.assertRaises(ValueError):
                    connector.project(fake_home / relative)
            self.assertEqual(connector.project(fake_home / 'project'), fake_home / 'project')

    @unittest.skipUnless(connector.pwd, 'the account database home exists only on POSIX')
    def test_protected_targets_include_account_database_home_under_changed_home(self):
        fake_home = self.base / 'fake-home'
        real_home = Path(connector.pwd.getpwuid(os.getuid()).pw_dir)
        with patch('pathlib.Path.home', return_value=fake_home), self.assertRaises(ValueError):
            connector.project(real_home / '.codex')

    def test_fake_sync_isolates_home_env_and_passes_target(self):
        self.seed({})
        binary = self.fake()
        with patch.dict(os.environ, {'FIXTURE_TOKEN': 'should-not-propagate', 'AGENTS_OPENCODE_CONFIG_PATH': 'unsafe'}):
            self.assertEqual(connector.drive(self.target, self.root, binary, self.home), 0)
        call = hub.read_json(self.target / 'invocation.json')
        self.assertEqual(call['args'], ['--no-update-check', 'sync', '--path', str(self.target)])
        self.assertEqual(call['env']['HOME'], str(self.home))
        for key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'TMPDIR'):
            self.assertTrue(Path(call['env'][key]).is_relative_to(self.home))
        self.assertNotIn('FIXTURE_TOKEN', call['env'])
        self.assertNotIn('AGENTS_OPENCODE_CONFIG_PATH', call['env'])

    def test_check_passes_through_exit_two_and_does_not_export(self):
        self.seed({})
        before = (self.target / '.agents/agents.json').read_bytes()
        self.assertEqual(connector.drive(self.target, self.root, self.fake(2), self.home, check=True), 2)
        self.assertIn('--check', hub.read_json(self.target / 'invocation.json')['args'])
        self.assertEqual(before, (self.target / '.agents/agents.json').read_bytes())
        self.assertFalse(self.home.exists())

    def test_check_reports_missing_export_without_invoking(self):
        self.imported()
        output = self.base / 'output'
        output.mkdir()
        self.assertEqual(connector.drive(output, self.root, self.fake(), self.home, check=True), 2)
        self.assertFalse((output / '.agents').exists())
        self.assertFalse((output / 'invocation.json').exists())

    def test_local_override_and_escaping_links_refused(self):
        self.seed({})
        local = self.target / '.agents/local.json'
        local.write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            connector.drive(self.target, self.root, self.fake(), self.home)
        local.unlink()
        (self.target / '.cursor').symlink_to(self.base)
        with self.assertRaises(ValueError):
            connector.drive(self.target, self.root, self.fake(), self.home)

    def test_invalid_throwaway_and_missing_binary_refused(self):
        self.seed({})
        with self.assertRaises(ValueError):
            connector.drive(self.target, self.root, self.fake(), self.base / 'home')
        with self.assertRaises(ValueError):
            connector.drive(self.target, self.root, str(self.base / 'absent'), self.home)

    def test_hash_mismatch_export_refused(self):
        self.imported()
        (self.local / 'skills/sample/resources/data.txt').write_text('drift', encoding='utf-8')
        with self.assertRaises(ValueError):
            connector.export_project(self.base / 'output', self.root)

    def test_cli_registration_requires_target(self):
        for operation in ('import', 'export', 'sync'):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                hub.parser().parse_args(['agents-' + operation])

    def test_export_back_preserves_original_metadata_and_variable(self):
        self.server.update(description='Upstream description', targets=['claude', 'codex'], enabled=True)
        self.imported()
        before = (self.target / '.agents/agents.json').read_bytes()
        connector.export_project(self.target, self.root)
        self.assertEqual(before, (self.target / '.agents/agents.json').read_bytes())

    def test_import_refuses_unindexed_existing_content(self):
        self.seed({})
        source = self.skill(self.target / '.agents/skills')
        shutil.copytree(source, self.local / 'skills/sample')
        with self.assertRaises(ValueError):
            connector.import_project(self.target, self.root)
        self.assertEqual(validators.read_local(self.root, hub.read_json)['skills'], [])

    def test_public_sync_refuses_literal_credentials_and_url_auth(self):
        for source in ({'env': {'MODE': 'x' * 25}},
                       {'headers': {'Authorization': 'unsafe'}},
                       {'url': 'https://example.invalid/mcp?key=unsafe'},
                       {'oauth': {'clientSecret': 'unsafe'}},
                       {'env': {'MODE': '${VAR:-unsafe}'}}):
            self.seed({'sample': {'transport': 'http', **source}})
            with self.subTest(source=source), self.assertRaises(ValueError):
                connector.drive(self.target, self.root, self.fake(), self.home)
            self.assertFalse((self.target / 'invocation.json').exists())

    def test_sync_keeps_confined_skill_bridges_and_check_zero(self):
        self.seed({})
        source = self.skill(self.target / '.agents/skills')
        bridge = self.target / '.claude/skills'
        bridge.parent.mkdir()
        bridge.symlink_to(source.parent)
        self.assertEqual(connector.drive(self.target, self.root, self.fake(), self.home, check=True), 0)

    def test_export_null_server_is_a_conflict(self):
        self.imported()
        output = self.base / 'output'
        output.mkdir()
        value = connector.config(output)
        value['mcp']['servers']['sample'] = None
        path = output / '.agents/agents.json'
        path.parent.mkdir()
        hub.write_json(path, value)
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            connector.export_project(output, self.root)
        self.assertEqual(before, path.read_bytes())

    def test_export_refuses_existing_literal_credentials_before_rewrite(self):
        path = self.seed({'unfamiliar': {'transport': 'stdio', 'command': 'example',
                                         'env': {'API_TOKEN': 'unsafe'}}})
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            connector.export_project(self.target, self.root)
        self.assertEqual(before, path.read_bytes())

    def test_relative_binary_resolves_before_changing_child_directory(self):
        self.seed({})
        relative = os.path.relpath(self.fake(), Path.cwd())
        self.assertEqual(connector.drive(self.target, self.root, relative, self.home), 0)
        self.assertTrue((self.target / 'invocation.json').is_file())
