import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import agents_sync
from checks import validators
import connect
import gateway
import hub
import lifecycle
from gateway_render import render_argument
from portable import product_env


ROOT = Path(__file__).resolve().parents[1]


class EnvironmentAliases(unittest.TestCase):
    def test_existing_private_catalog_manifests_still_load(self):
        manifest = validators.empty_manifest()
        manifest['name'] = 'agentlocker'
        self.assertEqual(validators.manifest(manifest), manifest)
        self.assertEqual(validators.empty_manifest()['name'], 'coldlocker')

    def test_old_environment_names_are_fallbacks_only_when_new_names_are_unset(self):
        for suffix in ('PR_BASE', 'PR_HEAD', 'DRIFT_ACTIVE', 'DRIFT_CMD', 'DRIFT_BUILDER_MODELS'):
            with self.subTest(suffix=suffix):
                old, new = 'AGENTLOCKER_' + suffix, 'COLDLOCKER_' + suffix
                self.assertEqual(product_env(new, 'default', {old: 'old'}), 'old')
                self.assertEqual(product_env(new, 'default', {old: 'old', new: 'new'}), 'new')
                self.assertEqual(product_env(new, 'default', {old: 'old', new: ''}), '')
                self.assertEqual(product_env(new, 'default', {}), 'default')

    def test_both_root_placeholders_resolve_and_reject_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'server.py'
            script.touch()
            for prefix in ('${COLDLOCKER_ROOT}/', '${AGENTLOCKER_ROOT}/'):
                self.assertEqual(render_argument(prefix + 'server.py', root), str(script))
                with self.assertRaises(ValueError):
                    render_argument(prefix + '../escape.py', root)

    def test_both_secret_placeholders_import_as_the_same_reference(self):
        for prefix in ('COLDLOCKER_SECRET_', 'AGENTLOCKER_SECRET_'):
            self.assertEqual(agents_sync.secret_reference('${' + prefix + '64656D6F' + '}'), 'secret://demo')

    def test_private_token_file_supports_new_and_old_clients(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'gateway.env'
            with patch.object(gateway, 'token_value', return_value='fixture-token'):
                gateway.write_token_file(root, path)
            self.assertEqual(dict(line.split('=', 1) for line in path.read_text().splitlines()),
                             {'COLDLOCKER_GATEWAY_TOKEN': 'fixture-token', 'AGENTLOCKER_GATEWAY_TOKEN': 'fixture-token'})
            with patch.object(gateway, 'token_value', return_value='other-token'), self.assertRaises(FileExistsError):
                gateway.write_token_file(root, path)

    def test_existing_default_owner_can_update_and_remove_without_weakening_other_owners(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            (root / 'manifest.json').write_text(json.dumps(validators.empty_manifest()))
            source = root / 'example.md'
            source.write_text('# First version\n')
            args = SimpleNamespace(command='import-rule', path=str(source), owner='agentlocker',
                                   origin='local-import', name='example', approve_risky=False)
            hub.import_item(args, root)
            source.write_text('# Second version\n')
            args.owner = None
            hub.import_item(args, root)
            imported = root / '.hub-store/catalog/rules/example.md'
            self.assertEqual(imported.read_text(), '# Second version\n')
            args.owner = 'agentlocker'
            hub.import_item(args, root)
            args.owner = 'other-owner'
            with self.assertRaisesRegex(ValueError, 'owned by'):
                hub.import_item(args, root)
            removal = SimpleNamespace(kind='rule', name='example', owner='other-owner', yes=True)
            with self.assertRaisesRegex(ValueError, 'owned by'):
                lifecycle.remove(removal, hub, root)
            removal.owner = None
            self.assertEqual(lifecycle.remove(removal, hub, root), 0)
            self.assertFalse(imported.exists())


class ServerAliases(unittest.TestCase):
    def test_reconnect_preserves_permissions_stored_outside_the_mcp_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / '.claude.json'
            path.write_text(json.dumps({'mcpServers': {'agentlocker': {'command': 'old'}}}))
            policy = home / '.claude/settings.json'
            policy.parent.mkdir()
            original = json.dumps({'permissions': {'allow': ['mcp__agentlocker'],
                                                   'deny': ['mcp__agentlocker__delete']}}).encode()
            policy.write_bytes(original)
            args = SimpleNamespace(home=str(home), platform='linux', agent=['claude'], write=True, yes=True)
            with patch('connect.gateway_connection', return_value={'status': 'stopped', 'snippets': {}}):
                self.assertEqual(connect.run(args, ROOT, env={}, output=io.StringIO()), 0)
            self.assertEqual(json.loads(path.read_text()), {'mcpServers': {
                'agentlocker': connect.entries(ROOT, 'claude')['coldlocker']}})
            self.assertEqual(policy.read_bytes(), original)

    def test_opencode_policy_references_retain_the_private_server_alias(self):
        for policy in ({'permission': {'*': 'deny', 'agentlocker_*': 'allow'}},
                       {'permission': {'*': 'allow', 'agentlocker_*': 'deny'}},
                       {'agent': {'helper': {'permission': {'agentlocker_hub_get_item': 'ask'}}}},
                       {'tools': {'agentlocker_*': False}}):
            original = {'mcp': {'agentlocker': {'type': 'local', 'command': ['old']}}, **policy}
            desired = {'coldlocker': {'type': 'local', 'command': ['new']}}
            text = connect.merge_json(json.dumps(original), 'opencode', desired)
            self.assertEqual(json.loads(text), {'mcp': {'agentlocker': desired['coldlocker']}, **policy})
            self.assertEqual(connect.merge_json(text, 'opencode', desired), text)

    def test_opencode_write_preserves_existing_tool_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / '.config/opencode/opencode.json'
            path.parent.mkdir(parents=True)
            permission = {'*': 'deny', 'agentlocker_*': 'allow'}
            path.write_text(json.dumps({'mcp': {'agentlocker': {'type': 'local', 'command': ['old']}},
                                        'permission': permission}))
            args = SimpleNamespace(home=str(home), platform='linux', agent=['opencode'], write=True, yes=True)
            with patch('connect.gateway_connection', return_value={'status': 'stopped', 'snippets': {}}):
                self.assertEqual(connect.run(args, ROOT, env={}, output=io.StringIO()), 0)
            self.assertEqual(json.loads(path.read_text()), {'mcp': {
                'agentlocker': connect.entries(ROOT, 'opencode')['coldlocker']}, 'permission': permission})

    def test_other_client_policy_references_keep_the_existing_alias(self):
        desired = {'coldlocker': {'command': 'new'}}
        for context in ({'permissions': {'allow': ['mcp__agentlocker']}},
                        {'permissions': {'allow': ['mcp__agentlocker__hub_list_skills']}},
                        {'disabledMcpServers': ['agentlocker']}):
            result = json.loads(connect.merge_json(json.dumps({'mcpServers': {
                'agentlocker': {'command': 'old'}}, **context}), 'claude', desired))
            self.assertEqual(result, {'mcpServers': {'agentlocker': {'command': 'new'}}, **context})
        text = '[mcp_servers.agentlocker]\ncommand = "old"\n[permissions]\nallowed_tools = ["mcp__agentlocker__hub_list_skills"]\n'
        result = connect.merge_toml(text, desired)
        self.assertEqual(connect.toml(result), {'mcp_servers': {'agentlocker': {'command': 'new'}},
                                               'permissions': {'allowed_tools': ['mcp__agentlocker__hub_list_skills']}})
        self.assertEqual(connect.merge_toml(result, desired), result)

    def test_conflicting_policy_bound_aliases_are_preserved_for_manual_reconciliation(self):
        original = {'mcp': {'agentlocker': {'command': 'old'}, 'coldlocker': {'command': 'new'}},
                    'permission': {'*': 'deny', 'agentlocker_*': 'allow'}}
        desired = {'coldlocker': {'command': 'new'}}
        with self.assertRaisesRegex(ValueError, 'conflicting MCP alias definitions'):
            connect.merge_json(json.dumps(original), 'opencode', desired)
        original['mcp']['agentlocker'] = {'command': 'new'}
        with self.assertRaisesRegex(ValueError, 'duplicate MCP aliases'):
            connect.merge_json(json.dumps(original), 'opencode', desired)
        text = '[mcp_servers.agentlocker]\ncommand = "old"\n[mcp_servers.coldlocker]\ncommand = "new"\n[permissions]\nallowed_tools = ["mcp__agentlocker__hub_list_skills"]\n'
        with self.assertRaisesRegex(ValueError, 'conflicting MCP alias definitions'):
            connect.merge_toml(text, desired)
        with self.assertRaisesRegex(ValueError, 'duplicate MCP aliases'):
            connect.merge_toml(text.replace('command = "old"', 'command = "new"'), desired)

    def test_executor_reconnect_preserves_header_environment_references(self):
        for reference in ('${COLDLOCKER_EXECUTOR_AUTHORIZATION}', '${env:COLDLOCKER_EXECUTOR_AUTHORIZATION}',
                          '{env:COLDLOCKER_EXECUTOR_AUTHORIZATION}'):
            legacy = reference.replace('COLDLOCKER_', 'AGENTLOCKER_')
            old = {'agentlocker-gateway': {'url': 'http://127.0.0.1:4788/mcp', 'headers': {'Authorization': legacy}}}
            desired = {'coldlocker-gateway': {'url': 'http://127.0.0.1:4788/mcp', 'headers': {'Authorization': reference}}}
            result = json.loads(connect.merge_json(json.dumps({'mcpServers': old}), 'claude', desired))
            self.assertEqual(result['mcpServers'], {'agentlocker-gateway': {
                'url': 'http://127.0.0.1:4788/mcp', 'headers': {'Authorization': legacy}}})
        desired = {'coldlocker-gateway': {'url': 'http://127.0.0.1:4788/mcp',
                                          'env_http_headers': {'Authorization': 'COLDLOCKER_EXECUTOR_AUTHORIZATION'}}}
        result = connect.toml(connect.merge_toml('[mcp_servers.agentlocker-gateway]\nurl = "http://127.0.0.1:4788/mcp"\n[mcp_servers.agentlocker-gateway.env_http_headers]\nAuthorization = "AGENTLOCKER_EXECUTOR_AUTHORIZATION"\n', desired))
        self.assertEqual(result['mcp_servers'], {'agentlocker-gateway': {
            'url': 'http://127.0.0.1:4788/mcp', 'env_http_headers': {'Authorization': 'AGENTLOCKER_EXECUTOR_AUTHORIZATION'}}})

    def test_gateway_reconnect_keeps_existing_client_token_references(self):
        for reference in ('Bearer ${COLDLOCKER_GATEWAY_TOKEN}', 'Bearer ${env:COLDLOCKER_GATEWAY_TOKEN}',
                          'Bearer {env:COLDLOCKER_GATEWAY_TOKEN}'):
            legacy = reference.replace('COLDLOCKER_', 'AGENTLOCKER_')
            servers = {'coldlocker-gateway': {'url': 'http://127.0.0.1:43871/mcp', 'headers': {'Authorization': reference}}}
            old = {'agentlocker-gateway': {'url': 'http://127.0.0.1:43871/mcp', 'headers': {'Authorization': legacy}}}
            result = json.loads(connect.merge_json(json.dumps({'mcpServers': old}), 'claude', servers))
            self.assertEqual(result['mcpServers'], {'agentlocker-gateway': {
                'url': 'http://127.0.0.1:43871/mcp', 'headers': {'Authorization': legacy}}})
            self.assertEqual(servers['coldlocker-gateway']['headers']['Authorization'], reference)
        servers = {'coldlocker-gateway': {'url': 'http://127.0.0.1:43871/mcp', 'bearer_token_env_var': 'COLDLOCKER_GATEWAY_TOKEN'}}
        result = connect.toml(connect.merge_toml('[mcp_servers.agentlocker-gateway]\nurl = "http://127.0.0.1:43871/mcp"\nbearer_token_env_var = "AGENTLOCKER_GATEWAY_TOKEN"\n', servers))
        self.assertEqual(result['mcp_servers'], {'agentlocker-gateway': {
            'url': 'http://127.0.0.1:43871/mcp', 'bearer_token_env_var': 'AGENTLOCKER_GATEWAY_TOKEN'}})

    def test_connect_write_migrates_a_legacy_registration_and_backs_up_the_original(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / '.claude.json'
            original = json.dumps({'mcpServers': {'agentlocker': {'command': 'old'}}, 'theme': 'kept'})
            path.write_text(original)
            args = SimpleNamespace(home=str(home), platform='linux', agent=['claude'], write=True, yes=True)
            with patch('connect.gateway_connection', return_value={'status': 'stopped', 'snippets': {}}):
                self.assertEqual(connect.run(args, ROOT, env={}, output=io.StringIO()), 0)
            value = json.loads(path.read_text())
            self.assertEqual(value, {'mcpServers': {'agentlocker': connect.entries(ROOT, 'claude')['coldlocker']}, 'theme': 'kept'})
            backups = list(home.glob('.claude.json.*.bak'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), original)

    def test_json_connect_migrates_both_old_entries_and_preserves_other_settings(self):
        for agent, group in (('claude', 'mcpServers'), ('cursor', 'mcpServers'), ('vscode', 'servers'), ('opencode', 'mcp')):
            for order in (('agentlocker', 'other', 'agentlocker-gateway'), ('other', 'agentlocker-gateway', 'agentlocker')):
                with self.subTest(agent=agent, order=order):
                    old = {name: {'command': name} for name in order}
                    text = json.dumps({'theme': 'kept', group: old}, indent=4)
                    servers = {'coldlocker': {'command': 'new'}}
                    merged = connect.merge_json(text, agent, servers)
                    result = json.loads(merged)
                    self.assertEqual(result, {'theme': 'kept', group: {
                        'agentlocker': {'command': 'new'}, 'other': {'command': 'other'},
                        'agentlocker-gateway': {'command': 'agentlocker-gateway'}}})
                    self.assertEqual(connect.merge_json(merged, agent, servers), merged)

    def test_json_connect_preserves_distinct_and_equivalent_duplicates_for_reconciliation(self):
        for compact in (True, False):
            for names in (('agentlocker', 'coldlocker'), ('coldlocker', 'agentlocker'),
                          ('agentlocker', 'agentlocker-gateway', 'coldlocker', 'coldlocker-gateway')):
                with self.subTest(compact=compact, names=names):
                    value = {'mcpServers': {name: {'command': name} for name in names}, 'other': True}
                    with self.assertRaisesRegex(ValueError, 'conflicting MCP alias definitions'):
                        connect.merge_json(json.dumps(value, indent=None if compact else 2),
                                           'claude', {'coldlocker': {'command': 'new'}})
                    value = {'mcpServers': {name: {'command': 'shared'} for name in names}, 'other': True}
                    with self.assertRaisesRegex(ValueError, 'duplicate MCP aliases'):
                        connect.merge_json(json.dumps(value, indent=None if compact else 2),
                                           'claude', {'coldlocker': {'command': 'new'}})

    def test_toml_connect_migrates_old_entries_and_nested_tables(self):
        text = ('model = "kept"\n[mcp_servers.agentlocker]\ncommand = "old"\n'
                '[mcp_servers.agentlocker-gateway]\nurl = "https://example.org/mcp"\n'
                '[mcp_servers.agentlocker-gateway.http_headers]\nX-Test = "preserved"\n'
                '[mcp_servers.other]\ncommand = "other"\n')
        servers = {'coldlocker': {'command': 'new'}}
        result = connect.merge_toml(text, servers)
        self.assertEqual(connect.toml(result), {'model': 'kept', 'mcp_servers': {
            'agentlocker': {'command': 'new'}, 'agentlocker-gateway': {
                'url': 'https://example.org/mcp', 'http_headers': {'X-Test': 'preserved'}},
            'other': {'command': 'other'}}})
        self.assertEqual(connect.merge_toml(result, servers), result)

    def test_toml_connect_preserves_distinct_and_equivalent_duplicates_for_reconciliation(self):
        text = ('[mcp_servers.agentlocker]\ncommand = "old"\n'
                '[mcp_servers.coldlocker]\ncommand = "new"\n')
        with self.assertRaisesRegex(ValueError, 'conflicting MCP alias definitions'):
            connect.merge_toml(text, {'coldlocker': {'command': 'new'}})
        with self.assertRaisesRegex(ValueError, 'duplicate MCP aliases'):
            connect.merge_toml(text.replace('command = "old"', 'command = "new"'),
                               {'coldlocker': {'command': 'new'}})

    def test_conflicting_gateway_aliases_survive_a_stopped_gateway_reconnect(self):
        servers = {'coldlocker': {'command': 'new'}}
        value = {'mcpServers': {'agentlocker-gateway': {'url': 'https://first.example/mcp'},
                               'coldlocker-gateway': {'url': 'https://second.example/mcp'}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.claude.json'
            original = json.dumps(value).encode()
            path.write_bytes(original)
            with self.assertRaisesRegex(ValueError, 'conflicting MCP alias definitions'):
                connect.plan(path, 'claude', servers)
            self.assertEqual(path.read_bytes(), original)
        text = ('[mcp_servers.agentlocker-gateway]\nurl = "https://first.example/mcp"\n'
                '[mcp_servers.coldlocker-gateway]\nurl = "https://second.example/mcp"\n')
        with self.assertRaisesRegex(ValueError, 'conflicting MCP alias definitions'):
            connect.merge_toml(text, servers)


if __name__ == '__main__':
    unittest.main()
