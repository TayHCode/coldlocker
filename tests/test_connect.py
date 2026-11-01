import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import connect
import hub


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/connect'
UTF8 = {**os.environ, 'PYTHONUTF8': '1'}


def escaped(path):
    return json.dumps(str(path))[1:-1]


class CheckoutFilesystem(SimpleNamespace):
    def __truediv__(self, name):
        return self.path / name


class ConnectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / 'home'
        shutil.copytree(FIXTURES / 'linux', self.home)
        self.gateway = {'status': 'stopped', 'snippets': {}}
        self.output = io.StringIO()

    def run_connect(self, **kwargs):
        args = SimpleNamespace(home=str(self.home), platform='linux', agent=None, write=False, yes=False)
        for key, value in kwargs.items():
            setattr(args, key, value)
        with patch('connect.gateway_connection', return_value=self.gateway):
            return connect.run(args, ROOT, env={}, stdin=io.StringIO(), output=self.output)

    def test_explicit_home_ignores_ambient_appdata(self):
        path = self.home / 'AppData/Roaming/Claude/claude_desktop_config.json'
        path.parent.mkdir(parents=True)
        path.write_text('{}', encoding='utf-8')
        ambient = self.home / 'outside'
        (ambient / 'Claude').mkdir(parents=True)
        outside = ambient / 'Claude/claude_desktop_config.json'
        outside.write_text('{}', encoding='utf-8')
        with patch.dict(os.environ, {'APPDATA': str(ambient)}):
            args = SimpleNamespace(home=str(self.home), platform='win32', agent=['claude_desktop'], write=True, yes=True)
            with patch('connect.gateway_connection', return_value=self.gateway):
                self.assertEqual(connect.run(args, ROOT, stdin=io.StringIO(), output=self.output), 0)
        self.assertIn('agentlocker', json.loads(path.read_text())['mcpServers'])
        self.assertEqual(outside.read_text(), '{}')

    def test_written_vscode_matches_printed_entry(self):
        path = self.home / '.config/Code/User/mcp.json'
        path.parent.mkdir(parents=True)
        path.write_text('{}', encoding='utf-8')
        self.assertEqual(self.run_connect(agent=['vscode'], write=True, yes=True), 0)
        written = json.loads(path.read_text())['servers']['agentlocker']
        self.assertEqual(written['type'], 'stdio')
        self.assertEqual(written, json.loads(connect.render(ROOT, 'vscode'))['servers']['agentlocker'])

    def test_desktop_only_emits_documented_stdio_config(self):
        self.assertNotIn('agentlocker-gateway', connect.entries(ROOT, 'claude_desktop', self.live_gateway()))

    def test_detection_per_os(self):
        for platform in connect.PATHS:
            home = FIXTURES / platform
            found = connect.detect(home, platform, {})
            self.assertEqual(set(found), {'claude', 'codex', 'cursor', 'opencode'})
            self.assertEqual(found['claude'], home.absolute() / '.claude.json')
            self.assertEqual(found['opencode'], home.absolute() / '.config/opencode/opencode.json')
        self.assertEqual(connect.detect(self.home / 'missing', 'linux', {}), {})
        with self.assertRaises(ValueError):
            connect.detect(self.home, 'other', {})

    def test_new_client_os_locations_and_appdata(self):
        for platform, relative in (
            ('linux', ('.config/Claude/claude_desktop_config.json', '.gemini/settings.json', '.codeium/windsurf/mcp_config.json', '.config/Code/User/mcp.json')),
            ('darwin', ('Library/Application Support/Claude/claude_desktop_config.json', '.gemini/settings.json', '.codeium/windsurf/mcp_config.json', 'Library/Application Support/Code/User/mcp.json')),
        ):
            home = Path(self.temporary.name) / platform
            for part in relative:
                path = home / part
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{}', encoding='utf-8')
            (home / '.hermes').mkdir(parents=True, exist_ok=True)
            (home / '.hermes/config.yaml').write_text('mcp_servers: {}\n', encoding='utf-8')
            found = connect.detect(home, platform, {})
            self.assertEqual(set(found), {'claude_desktop', 'gemini', 'windsurf', 'vscode', 'hermes'})
        home = Path(self.temporary.name) / 'windows'
        roaming = home / 'roaming'
        (roaming / 'Claude').mkdir(parents=True)
        (roaming / 'Code/User').mkdir(parents=True)
        with patch.dict(os.environ, {'HOME': str(home), 'APPDATA': str(roaming)}):
            found = connect.detect(home, 'win32', os.environ)
        self.assertEqual(found['claude_desktop'], roaming / 'Claude/claude_desktop_config.json')
        self.assertEqual(found['vscode'], roaming / 'Code/User/mcp.json')

    def test_platform_cursor_app_markers(self):
        for platform, marker in (('linux', '.config/Cursor'), ('darwin', 'Library/Application Support/Cursor'),
                                 ('win32', 'AppData/Roaming/Cursor')):
            home = Path(self.temporary.name) / platform
            (home / marker).mkdir(parents=True)
            self.assertEqual(set(connect.detect(home, platform, {})), {'cursor'})

    def test_injected_config_locations(self):
        directory = self.home / 'custom'
        directory.mkdir()
        (directory / 'opencode').mkdir()
        found = connect.detect(self.home, 'linux', {'CODEX_HOME': str(directory),
                              'CLAUDE_CONFIG_DIR': str(directory), 'XDG_CONFIG_HOME': str(directory)})
        self.assertEqual(found['codex'], directory / 'config.toml')
        self.assertEqual(found['claude'], directory / '.claude.json')
        self.assertEqual(found['opencode'], directory / 'opencode/opencode.json')

    @unittest.skipUnless(sys.platform == 'linux', 'snap packages exist only on Linux')
    def test_snap_codex_connection_lands_where_codex_reads_it(self):
        revision = self.home / 'snap/codex/34'
        revision.mkdir(parents=True)
        (self.home / 'snap/codex/current').symlink_to('34')
        bin_dir = Path(self.temporary.name) / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'snap').write_text('', encoding='utf-8')
        (bin_dir / 'snap').chmod(0o755)
        (bin_dir / 'codex').symlink_to(bin_dir / 'snap')
        env = {'PATH': str(bin_dir)}
        ordinary = (self.home / '.codex/config.toml').read_bytes()
        args = SimpleNamespace(home=str(self.home), platform='linux', agent=['codex'], write=True, yes=True)
        with patch('connect.gateway_connection', return_value=self.gateway):
            self.assertEqual(connect.run(args, ROOT, env=env, stdin=io.StringIO(), output=self.output), 0)
            self.assertEqual(connect.run(args, ROOT, env=env, stdin=io.StringIO(), output=self.output), 0)
        written = connect.toml((revision / 'config.toml').read_text(encoding='utf-8'))
        self.assertIn('agentlocker', written['mcp_servers'])
        self.assertEqual((self.home / '.codex/config.toml').read_bytes(), ordinary)
        self.assertIn('already connected', self.output.getvalue())
        self.assertEqual(connect.detect(self.home, 'win32', env)['codex'], self.home / '.codex/config.toml')
        self.assertEqual(connect.detect(self.home, 'darwin', env)['codex'], self.home / '.codex/config.toml')

    def test_exact_shapes_parse_back(self):
        endpoint = str(ROOT / 'mcp_endpoint.py')
        for agent in connect.AGENTS:
            rendered = connect.render(ROOT, agent)
            if agent == 'hermes':
                self.assertIn('mcp_servers:', rendered)
                continue
            value = connect.toml(rendered) if agent == 'codex' else json.loads(rendered)
            key = 'mcp_servers' if agent == 'codex' else 'mcp' if agent == 'opencode' else 'servers' if agent == 'vscode' else 'mcpServers'
            expected = {'type': 'local', 'command': [connect.interpreter(), endpoint]} if agent == 'opencode' else {'command': connect.interpreter(), 'args': [endpoint]}
            if agent == 'vscode':
                expected = {'type': 'stdio', **expected}
            self.assertEqual(value[key], {'agentlocker': expected})

    def live_gateway(self):
        url = 'http://127.0.0.1:43871/mcp'
        return {'status': 'running', 'snippets': {
            'Claude': json.dumps({'mcpServers': {'agentlocker-gateway': {'type': 'http', 'url': url}}}),
            'Cursor': json.dumps({'mcpServers': {'agentlocker-gateway': {'url': url}}}),
            'Codex': '[mcp_servers.agentlocker-gateway]\nurl = ' + json.dumps(url) + '\n'}}

    def test_gateway_shapes(self):
        gateway = self.live_gateway()
        for agent in connect.AGENTS:
            rendered = connect.render(ROOT, agent, gateway)
            if agent == 'hermes':
                self.assertIn('http://127.0.0.1:43871/mcp', rendered)
                continue
            value = connect.toml(rendered) if agent == 'codex' else json.loads(rendered)
            key = 'mcp_servers' if agent == 'codex' else 'mcp' if agent == 'opencode' else 'servers' if agent == 'vscode' else 'mcpServers'
            if agent == 'claude_desktop':
                self.assertNotIn('agentlocker-gateway', value[key])
                continue
            remote = value[key]['agentlocker-gateway']
            self.assertIn('http://127.0.0.1:43871/mcp', remote.values())
            if agent in ('claude', 'opencode'):
                self.assertEqual(remote['type'], 'remote' if agent == 'opencode' else 'http')
        gateway['snippets']['Cursor'] = json.dumps({'mcpServers': {'agentlocker-gateway': {
            'url': 'http://127.0.0.1:43871/mcp', 'headers': {'Authorization': '${AGENTLOCKER_EXECUTOR_AUTHORIZATION}'}}}})
        remote = connect.entries(ROOT, 'opencode', gateway)['agentlocker-gateway']
        self.assertEqual(remote['headers']['Authorization'], '{env:AGENTLOCKER_EXECUTOR_AUTHORIZATION}')

    def test_unsafe_gateway_refused(self):
        gateway = self.live_gateway()
        gateway['snippets']['Cursor'] = json.dumps({'mcpServers': {'agentlocker-gateway': {'url': 'https://example.com/?token=canary'}}})
        with self.assertRaises(ValueError):
            connect.render(ROOT, 'cursor', gateway)

    def test_print_is_read_only_and_does_not_leak(self):
        canary = 'synthetic-private-value'
        path = self.home / '.claude.json'
        path.write_text(json.dumps({'account': canary, 'mcpServers': {'other': {'env': {'PRIVATE': canary}}}}), encoding='utf-8')
        before = {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        self.assertEqual(self.run_connect(), 0)
        self.assertNotIn(canary, self.output.getvalue())
        self.assertIn('Grok Bot:', self.output.getvalue())
        self.assertEqual(before, {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()})

    def test_write_does_not_print_existing_credentials(self):
        path = self.home / '.claude.json'
        canary = 'fixture-private-credential'
        path.write_text(json.dumps({'session': canary, 'mcpServers': {'other': {'env': {'PRIVATE': canary}}}}), encoding='utf-8')
        self.assertEqual(self.run_connect(write=True, yes=True), 0)
        self.assertNotIn(canary, self.output.getvalue())
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['session'], canary)

    def test_jsonc_only_opencode_is_skipped_with_manual_note(self):
        path = self.home / '.config/opencode/opencode.json'
        path.unlink()
        alternative = path.with_suffix('.jsonc')
        alternative.write_text('{ // fixture\n "mcp": {}\n}', encoding='utf-8')
        jsonc = alternative.read_bytes()
        self.assertEqual(self.run_connect(write=True, yes=True), 0)
        self.assertIn('OpenCode: manual merge (jsonc) - paste this snippet', self.output.getvalue())
        self.assertFalse(path.exists())
        self.assertEqual(alternative.read_bytes(), jsonc)
        for name in ('.claude.json', '.cursor/mcp.json'):
            self.assertIn('agentlocker', json.loads((self.home / name).read_text(encoding='utf-8'))['mcpServers'])
        self.assertIn('agentlocker', connect.toml((self.home / '.codex/config.toml').read_text(encoding='utf-8'))['mcp_servers'])
        self.assertEqual(self.output.getvalue().count('Connected:'), 3)
        self.assertEqual(len(list(self.home.rglob('*.bak'))), 3)

    def test_jsonc_only_home_connects_through_cli(self):
        path = self.home / '.config/opencode/opencode.json'
        path.unlink()
        path.with_suffix('.jsonc').write_text('{}', encoding='utf-8')
        result = subprocess.run([sys.executable, 'hub.py', 'connect', '--home', str(self.home), '--write', '--yes'],
                                cwd=ROOT, capture_output=True, text=True, env=UTF8, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('OpenCode: manual merge (jsonc)', result.stdout)
        self.assertTrue((self.home / '.claude.json').read_text(encoding='utf-8').count('agentlocker'))
        self.assertFalse(path.exists())

    def test_cli_refusal_prints_specific_reason(self):
        result = subprocess.run([sys.executable, 'hub.py', 'link', '--target', str(self.home), '--dry-run'],
                                cwd=ROOT, capture_output=True, text=True,
                                env={**UTF8, 'HOME': str(self.home), 'USERPROFILE': str(self.home)}, encoding='utf-8')
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith('FAIL: refusing home directory target'))
        self.assert_explains_skill_reach(result.stderr)

    def assert_explains_skill_reach(self, text):
        for fragment in ('hub_list_skills', 'hub_get_item', 'any connected agent', 'link --target', 'project folder', 'native skills'):
            self.assertIn(fragment, text)

    def test_connect_skills_line_explains_mcp_reach_and_project_link(self):
        spelled = Path(self.temporary.name) / 'spelled'
        spelled.symlink_to(ROOT, target_is_directory=True)
        with patch.dict(os.environ, {'PWD': str(spelled)}):
            self.assertEqual(self.run_connect(), 0)
        text = self.output.getvalue()
        self.assertNotIn('<PROJECT>', text)
        skills = [line for line in text.splitlines() if line.startswith('Skills:')]
        self.assertEqual(len(skills), 3)
        for line in skills:
            self.assert_explains_skill_reach(line)
            self.assertIn(str(spelled / 'hub.py'), line)

    def test_cli_unexpected_failure_stays_generic(self):
        with patch('sys.argv', ['hub.py', 'check']), patch('hub.checker.run', side_effect=OSError('/private/detail')), \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(hub.main(), 1)
        self.assertNotIn('/private/detail', errors.getvalue())
        self.assertIn('invalid input, unsafe destination, or filesystem operation failed', errors.getvalue())

    def test_write_merge_backup_idempotent(self):
        before = {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        self.assertEqual(self.run_connect(write=True, yes=True), 0)
        for path, original in before.items():
            if path.suffix == '.toml':
                current = connect.toml(path.read_text(encoding='utf-8'))
                self.assertEqual(current['model'], 'fixture')
                self.assertIn('# keep this comment', path.read_text(encoding='utf-8'))
                self.assertEqual(current['mcp_servers']['other']['command'], 'fixture-tool')
            else:
                current = json.loads(path.read_text(encoding='utf-8'))
                old = json.loads(original)
                key = 'mcp' if 'opencode' in str(path) else 'mcpServers'
                self.assertEqual(current[key]['other'], old[key]['other'])
                self.assertEqual(current['unrelated'], old['unrelated'])
                self.assertTrue(path.read_text(encoding='utf-8').startswith('{\n    '))
            backups = list(path.parent.glob(path.name + '.*.bak'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)
        after = {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        self.output = io.StringIO()
        self.assertEqual(self.run_connect(write=True, yes=True), 0)
        self.assertEqual(self.output.getvalue().count('already connected'), 4)
        self.assertEqual(after, {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()})

    def test_added_json_clients_merge_backup_and_rerun(self):
        originals = {}
        for agent in ('claude_desktop', 'gemini', 'vscode'):
            path = self.home / connect.PATHS['linux'][connect.AGENTS.index(agent)]
            path.parent.mkdir(parents=True, exist_ok=True)
            group = 'servers' if agent == 'vscode' else 'mcpServers'
            original = json.dumps({'unrelated': {'keep': True}, group: {'other': {'command': 'keep'}}}, indent=2).encode()
            originals[agent] = original
            path.write_bytes(original)
        with patch('connect.gateway_connection', return_value=self.live_gateway()):
            args = SimpleNamespace(home=str(self.home), platform='linux', agent=['claude_desktop', 'gemini', 'vscode'], write=True, yes=True)
            self.assertEqual(connect.run(args, ROOT, env={}, stdin=io.StringIO(), output=self.output), 0)
            for agent in args.agent:
                path = self.home / connect.PATHS['linux'][connect.AGENTS.index(agent)]
                value = json.loads(path.read_text(encoding='utf-8'))
                group = 'servers' if agent == 'vscode' else 'mcpServers'
                self.assertEqual(value['unrelated'], {'keep': True})
                self.assertEqual(value[group]['other'], {'command': 'keep'})
                self.assertIn('agentlocker', value[group])
                if agent == 'claude_desktop':
                    self.assertNotIn('agentlocker-gateway', value[group])
                else:
                    self.assertIn('agentlocker-gateway', value[group])
                self.assertEqual(list(path.parent.glob(path.name + '.*.bak'))[0].read_bytes(), originals[agent])
            self.output = io.StringIO()
            self.assertEqual(connect.run(args, ROOT, env={}, stdin=io.StringIO(), output=self.output), 0)
            self.assertEqual(self.output.getvalue().count('already connected'), 3)

    def test_hermes_is_print_only_and_refuses_write(self):
        path = self.home / '.hermes/config.yaml'
        path.parent.mkdir()
        original = b'mcp_servers:\n  other: {}\n'
        path.write_bytes(original)
        self.assertEqual(self.run_connect(agent=['hermes']), 0)
        self.assertIn('mcp_servers:', self.output.getvalue())
        self.assertEqual(path.read_bytes(), original)
        self.output = io.StringIO()
        self.assertEqual(self.run_connect(agent=['hermes'], write=True, yes=True), 1)
        self.assertIn('YAML cannot be merged safely', self.output.getvalue())
        self.assertEqual(path.read_bytes(), original)

    def test_windsurf_is_print_only_and_refuses_write(self):
        path = self.home / '.codeium/windsurf/mcp_config.json'
        path.parent.mkdir(parents=True)
        original = b'{"mcpServers":{"other":{"command":"keep"}}}\n'
        path.write_bytes(original)
        self.assertEqual(self.run_connect(agent=['windsurf']), 0)
        self.assertIn('Windsurf is print-only', self.output.getvalue())
        self.output = io.StringIO()
        self.assertEqual(self.run_connect(agent=['windsurf'], write=True, yes=True), 1)
        self.assertEqual(path.read_bytes(), original)

    def test_added_client_symlink_destination_refused(self):
        path = self.home / '.gemini/settings.json'
        path.parent.mkdir(parents=True)
        target = self.home / 'other-config'
        target.write_text('{}', encoding='utf-8')
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            connect.plan(path, 'gemini', connect.entries(ROOT, 'gemini'))

    def test_noninteractive_refusal(self):
        before = (self.home / '.codex/config.toml').read_bytes()
        self.assertEqual(self.run_connect(write=True), 1)
        self.assertIn('Refusing --write', self.output.getvalue())
        self.assertEqual(before, (self.home / '.codex/config.toml').read_bytes())
        self.assertEqual(list(self.home.rglob('*.bak')), [])

    def test_interactive_confirmation(self):
        class Input(io.StringIO):
            def isatty(self):
                return True
        args = SimpleNamespace(home=str(self.home), platform='linux', agent=['codex'], write=True, yes=False)
        with patch('connect.gateway_connection', return_value=self.gateway):
            self.assertEqual(connect.run(args, ROOT, env={}, stdin=Input('no\n'), output=self.output), 1)
            self.assertEqual(connect.run(args, ROOT, env={}, stdin=Input('yes\n'), output=self.output), 0)

    def test_symlinks_and_special_destinations_refused(self):
        path = self.home / '.codex/config.toml'
        path.unlink()
        target = self.home / 'unrelated'
        target.write_text('preserve', encoding='utf-8')
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.run_connect(write=True, yes=True)
        self.assertEqual(target.read_text(encoding='utf-8'), 'preserve')
        self.assertEqual(list(self.home.rglob('*.bak')), [])
        path.unlink()
        path.mkdir()
        with self.assertRaises(ValueError):
            connect.plan(path, 'codex', connect.entries(ROOT, 'codex'))
        path.rmdir()
        if hasattr(os, 'mkfifo'):
            os.mkfifo(path)
            with self.assertRaises(ValueError):
                connect.plan(path, 'codex', connect.entries(ROOT, 'codex'))
            path.unlink()
        (self.home / '.codex').rmdir()
        (self.home / '.codex').symlink_to(self.home / '.cursor', target_is_directory=True)
        with self.assertRaises(ValueError):
            connect.plan(path, 'codex', connect.entries(ROOT, 'codex'))

    def test_toml_edge_cases(self):
        servers = connect.entries(ROOT, 'codex')
        cases = ['', '# keep\nmodel = "fixture"\n',
                 '# keep\n[mcp_servers.agentlocker]\ncommand = "old"\n[mcp_servers.agentlocker.env]\nOLD = "value"\n[mcp_servers.other]\ncommand = "keep"\n',
                 '[mcp_servers."agentlocker"]\ncommand = "old"\n',
                 'message = """\n[mcp_servers.agentlocker]\nfake = 1\n"""\n',
                 'model = "fixture"\r\n# keep\r\n[mcp_servers.agentlocker]\r\ncommand = "old"\r\n[mcp_servers.other]\r\ncommand = "keep"\r\n']
        for text in cases:
            with self.subTest(text=text):
                result = connect.merge_toml(text, servers)
                expected = connect.toml(text)
                expected.setdefault('mcp_servers', {}).update(servers)
                self.assertEqual(connect.toml(result), expected)
                self.assertEqual(connect.merge_toml(result, servers), result)
                if '# keep' in text:
                    self.assertIn('# keep', result)
                if '\r\n' in text:
                    self.assertNotIn('\n', result.replace('\r\n', ''))
        for text in ('mcp_servers = {agentlocker = {command = "old"}}\n', '[mcp_servers]\nagentlocker.command = "old"\n'):
            with self.assertRaises(ValueError):
                connect.merge_toml(text, servers)

    def test_replacement_rechecks_original(self):
        path = self.home / '.cursor/mcp.json'
        change = connect.plan(path, 'cursor', connect.entries(ROOT, 'cursor'))
        path.write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            connect.save(change)
        self.assertEqual(path.read_text(encoding='utf-8'), '{}')

    def test_save_rechecks_destination_after_temp_flush(self):
        path = self.home / '.claude.json'
        change = connect.plan(path, 'claude', connect.entries(ROOT, 'claude'))
        concurrent = json.loads(path.read_text(encoding='utf-8'))
        concurrent['unrelated'] = 'external-update'
        external = json.dumps(concurrent, indent=4).encode('utf-8') + b'\n'
        real_fsync = connect.os.fsync

        def flush_then_update(descriptor):
            real_fsync(descriptor)
            path.write_bytes(external)

        with patch('connect.os.fsync', side_effect=flush_then_update):
            with self.assertRaisesRegex(ValueError, 'destination changed'):
                connect.save(change)
        self.assertEqual(path.read_bytes(), external)
        self.assertEqual(list(path.parent.glob(path.name + '.*.bak')), [])

    def test_save_rechecks_destination_identity_after_temp_flush(self):
        path = self.home / '.claude.json'
        change = connect.plan(path, 'claude', connect.entries(ROOT, 'claude'))
        original = path.read_bytes()
        real_fsync = connect.os.fsync

        def flush_then_replace(descriptor):
            real_fsync(descriptor)
            replacement = path.with_name(path.name + '.external')
            replacement.write_bytes(original)
            os.replace(replacement, path)

        with patch('connect.os.fsync', side_effect=flush_then_replace):
            with self.assertRaisesRegex(ValueError, 'destination changed'):
                connect.save(change)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(path.parent.glob(path.name + '.*.bak')), [])

    def test_agent_filter_and_cli(self):
        self.assertEqual(self.run_connect(agent=['cursor']), 0)
        self.assertIn('Cursor —', self.output.getvalue())
        self.assertNotIn('Codex —', self.output.getvalue())
        args = hub.parser().parse_args(['connect', '--agent', 'claude', 'cursor', '--agent', 'codex'])
        self.assertEqual(args.agent, ['claude', 'cursor', 'codex'])
        result = subprocess.run([sys.executable, 'hub.py', 'connect', '--home', str(self.home), '--write'],
                                cwd=ROOT, capture_output=True, text=True, env=UTF8, encoding='utf-8', input='')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Refusing --write', result.stdout)

    def test_missing_tomllib_has_clean_cli_error(self):
        args = SimpleNamespace(home=str(self.home), platform='linux', agent=['codex'], write=True, yes=True)
        errors = io.StringIO()
        with patch.dict('sys.modules', {'tomllib': None}), patch('connect.gateway_connection', return_value=self.gateway):
            with contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(errors):
                self.assertEqual(connect.cli(args, ROOT), 1)
        self.assertIn('requires Python 3.11', errors.getvalue())
        self.assertEqual(list(self.home.rglob('*.bak')), [])

    def test_invalid_config_skips_only_that_client(self):
        codex = self.home / '.codex/config.toml'
        codex.write_text('[projects."/x"]\ntrust = 1\n[projects."/x"]\ntrust = 2\n', encoding='utf-8')
        original = codex.read_bytes()
        claude = self.home / '.claude.json'
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(self.run_connect(write=True, yes=True), 1)
        self.assertIn('agentlocker', claude.read_text(encoding='utf-8'))
        self.assertEqual(codex.read_bytes(), original)
        self.assertEqual(list(codex.parent.glob('*.bak')), [])
        self.assertIn('codex: ~/.codex/config.toml is not valid TOML (', errors.getvalue())
        self.assertIn('); fix it and rerun', errors.getvalue())
        self.assertNotIn(str(self.home), errors.getvalue())
        for path in (self.home / '.cursor/mcp.json', self.home / '.config/opencode/opencode.json'):
            self.assertIn('agentlocker', path.read_text(encoding='utf-8'))
        self.assertNotIn('Connected: ' + str(codex), self.output.getvalue())

    def test_invalid_json_skips_only_that_client_in_print_mode(self):
        (self.home / '.cursor/mcp.json').write_text('{bad', encoding='utf-8')
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(self.run_connect(), 1)
        self.assertIn('cursor: ~/.cursor/mcp.json is not valid JSON (', errors.getvalue())
        self.assertIn('Claude —', self.output.getvalue())
        self.assertIn('Codex —', self.output.getvalue())
        self.assertNotIn('Cursor —', self.output.getvalue())

    def test_invalid_config_cli_exit_names_client_and_connects_others(self):
        (self.home / '.codex/config.toml').write_text('[a]\n[a]\n', encoding='utf-8')
        result = subprocess.run([sys.executable, 'hub.py', 'connect', '--home', str(self.home), '--write', '--yes'],
                                cwd=ROOT, capture_output=True, text=True, env=UTF8, encoding='utf-8')
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith('codex: ~/.codex/config.toml is not valid TOML ('), result.stderr)
        self.assertNotIn('FAIL', result.stderr)
        self.assertIn('agentlocker', (self.home / '.claude.json').read_text(encoding='utf-8'))

    def test_python310_skips_only_codex(self):
        codex = self.home / '.codex/config.toml'
        original = codex.read_bytes()
        errors = io.StringIO()
        with patch.dict('sys.modules', {'tomllib': None}), contextlib.redirect_stderr(errors):
            self.assertEqual(self.run_connect(write=True, yes=True), 1)
        text = self.output.getvalue()
        self.assertIn('Cursor —', text)
        self.assertIn('OpenCode —', text)
        self.assertIn('Codex — add by hand', text)
        self.assertIn('[mcp_servers.agentlocker]', text)
        self.assertTrue(errors.getvalue().startswith('codex: '), errors.getvalue())
        self.assertIn('requires Python 3.11', errors.getvalue())
        self.assertEqual(codex.read_bytes(), original)
        self.assertEqual(list(codex.parent.glob('*.bak')), [])
        for name in ('.claude.json', '.cursor/mcp.json', '.config/opencode/opencode.json'):
            self.assertIn('agentlocker', (self.home / name).read_text(encoding='utf-8'))

    def test_python310_manual_codex_block_includes_running_gateway(self):
        self.gateway = self.live_gateway()
        errors = io.StringIO()
        with patch.dict('sys.modules', {'tomllib': None}), contextlib.redirect_stderr(errors):
            self.assertEqual(self.run_connect(), 1)
        self.assertIn('[mcp_servers.agentlocker-gateway]', self.output.getvalue())
        self.assertIn('Cursor —', self.output.getvalue())
        self.assertTrue(errors.getvalue().startswith('codex: '), errors.getvalue())

    @unittest.skipIf(sys.platform == 'win32', 'directory permission bits do not block writes on Windows')
    def test_write_failure_names_client_and_continues(self):
        directory = self.home / '.codex'
        original = (directory / 'config.toml').read_bytes()
        directory.chmod(0o500)
        self.addCleanup(directory.chmod, 0o700)
        if os.access(directory, os.W_OK):
            self.skipTest('directory permissions are not enforced for this user')
        result = subprocess.run([sys.executable, 'hub.py', 'connect', '--home', str(self.home), '--write', '--yes'],
                                cwd=ROOT, capture_output=True, text=True, env=UTF8, encoding='utf-8')
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith('codex: could not write ~/.codex/config.toml ('), result.stderr)
        self.assertNotIn('FAIL', result.stderr)
        self.assertNotIn(str(self.home), result.stderr)
        self.assertEqual((directory / 'config.toml').read_bytes(), original)
        for name in ('.claude.json', '.cursor/mcp.json', '.config/opencode/opencode.json'):
            self.assertIn('agentlocker', (self.home / name).read_text(encoding='utf-8'))
        self.assertIn('Connected: ' + str(self.home / '.cursor/mcp.json'), result.stdout)
        self.assertIn('Connected: ' + str(self.home / '.config/opencode/opencode.json'), result.stdout)

    @unittest.skipIf(sys.platform == 'win32', 'directory permission bits do not block writes on Windows')
    def test_write_failure_flushes_stdout_before_stderr(self):
        directory = self.home / '.codex'
        directory.chmod(0o500)
        self.addCleanup(directory.chmod, 0o700)
        if os.access(directory, os.W_OK):
            self.skipTest('directory permissions are not enforced for this user')
        result = subprocess.run([sys.executable, 'hub.py', 'connect', '--home', str(self.home), '--write', '--yes'],
                                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=UTF8, encoding='utf-8')
        text = result.stdout
        self.assertLess(text.index('Connected: ' + str(self.home / '.claude.json')), text.index('codex: could not write'))

    def test_write_prints_backup_path_and_working_undo(self):
        path = self.home / '.claude.json'
        original = path.read_bytes()
        self.assertEqual(self.run_connect(write=True, yes=True, agent=['claude']), 0)
        backups = list(self.home.glob('.claude.json.*.bak'))
        self.assertEqual(len(backups), 1)
        lines = [line for line in self.output.getvalue().splitlines() if line.startswith('Undo: ')]
        self.assertEqual(len(lines), 1)
        self.assertIn('Backup: ' + str(backups[0]), self.output.getvalue())
        if sys.platform.startswith('win'):
            expected = 'Undo: cmd /c copy /Y "' + str(backups[0]) + '" "' + str(path) + '"'
        else:
            expected = 'Undo: cp ' + shlex.quote(str(backups[0])) + ' ' + shlex.quote(str(path))
        self.assertEqual(lines[0], expected)
        path.write_bytes(b'changed after connect\n')
        subprocess.run(lines[0][len('Undo: '):], shell=True, check=True)
        self.assertEqual(path.read_bytes(), original)

    def test_write_prints_cmd_compatible_undo_on_windows(self):
        path = self.home / '.claude.json'
        with patch('connect.sys.platform', 'win32'):
            self.assertEqual(self.run_connect(write=True, yes=True, agent=['claude']), 0)
        backup = next(self.home.glob('.claude.json.*.bak'))
        lines = [line for line in self.output.getvalue().splitlines() if line.startswith('Undo: ')]
        self.assertEqual(lines, ['Undo: cmd /c copy /Y "' + str(backup) + '" "' + str(path) + '"'])

    def test_write_preserves_missing_trailing_newline_for_round_trip(self):
        path = self.home / '.claude.json'
        path.write_bytes(path.read_bytes().rstrip(b'\n'))
        original = path.read_bytes()
        self.assertEqual(self.run_connect(write=True, yes=True, agent=['claude']), 0)
        written = path.read_bytes()
        self.assertFalse(written.endswith(b'\n'))
        self.assertIn('agentlocker', json.loads(written)['mcpServers'])
        document = json.loads(written)
        del document['mcpServers']['agentlocker']
        self.assertEqual(json.dumps(document, indent=4).encode(), original)

    def test_write_keeps_trailing_newline_when_present(self):
        path = self.home / '.claude.json'
        self.assertTrue(path.read_bytes().endswith(b'\n'))
        self.assertEqual(self.run_connect(write=True, yes=True, agent=['claude']), 0)
        self.assertTrue(path.read_bytes().endswith(b'}\n'))

    def test_scratch_part_detects_explicit_ancestors(self):
        cases = (
            ('/ordinary/agentlocker/.scratch/clone', '.scratch'),
            ('/ordinary/agentlocker/.worktrees/clone', '.worktrees'),
            ('/ordinary/agentlocker/.scratch/.worktrees/clone', '.scratch'),
            ('/ordinary/agentlocker/clone', None),
        )
        for resolved, expected in cases:
            with self.subTest(resolved=resolved), patch.object(connect.Path, 'resolve', return_value=Path(resolved)):
                self.assertEqual(connect.scratch_part(Path('/fixture/clone')), expected)

    def test_scratch_warning_format_is_independent_of_ancestry(self):
        for part in ('.scratch', '.worktrees'):
            checkout = Path(self.temporary.name) / 'clone'
            output = io.StringIO()
            args = SimpleNamespace(home=str(self.home), platform='linux', agent=['claude'], write=False, yes=False)
            with patch('connect.gateway_connection', return_value=self.gateway), \
                    patch('connect.scratch_part', return_value=part):
                connect.run(args, checkout, env={}, stdin=io.StringIO(), output=output)
            self.assertEqual(output.getvalue().splitlines()[0],
                             f'WARNING: {checkout} is under {part}; deleting it breaks every connected client. '
                             'Clone agentlocker somewhere permanent and rerun.')

    def test_linked_worktree_preview_warns_for_t3_and_arbitrary_locations(self):
        for relative in ('.t3/worktrees/project/branch', 'disposable/checkout'):
            with self.subTest(relative=relative):
                checkout = Path(self.temporary.name) / relative
                checkout.mkdir(parents=True)
                (checkout / '.git').write_text('gitdir: ../../repository/.git/worktrees/branch\n', encoding='utf-8')
                args = SimpleNamespace(home=str(self.home), platform='linux', agent=['claude'], write=False, yes=False)
                output = io.StringIO()
                before = {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
                with patch('connect.gateway_connection', return_value=self.gateway):
                    self.assertEqual(connect.run(args, checkout, env={}, stdin=io.StringIO(), output=output), 0)
                ancestor = next((part for part in checkout.resolve().parts if part in ('.scratch', '.worktrees')), None)
                location = f'is under {ancestor}' if ancestor else 'uses Git directory indirection (.git file)'
                self.assertEqual(output.getvalue().splitlines()[0],
                                 f'WARNING: {checkout} {location}; '
                                 'deleting it breaks every connected client. Clone agentlocker somewhere permanent and rerun.')
                self.assertIn(escaped(checkout / 'mcp_endpoint.py'), output.getvalue())
                self.assertEqual(before, {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()})

    def test_git_file_detection_does_not_read_private_git_values(self):
        checkout = Path(self.temporary.name) / 'linked'
        checkout.mkdir()
        (checkout / '.git').write_text('gitdir: fixture-private-location\n', encoding='utf-8')
        resolved = CheckoutFilesystem(path=checkout, parts=('ordinary', 'linked'))
        with patch.object(Path, 'resolve', return_value=resolved), \
                patch.object(Path, 'read_text', side_effect=PermissionError('fixture-private-value')) as read_text, \
                patch.object(Path, 'read_bytes', side_effect=PermissionError('fixture-private-value')) as read_bytes:
            self.assertEqual(connect.scratch_part(checkout), '.git')
        read_text.assert_not_called()
        read_bytes.assert_not_called()

    def test_malformed_git_file_still_warns_without_reading_contents(self):
        checkout = Path(self.temporary.name) / 'linked'
        checkout.mkdir()
        resolved = CheckoutFilesystem(path=checkout, parts=('ordinary', 'linked'))
        for content in (b'', b'fixture-private-value\xff\x00'):
            with self.subTest(content=content):
                (checkout / '.git').write_bytes(content)
                args = SimpleNamespace(home=str(self.home), platform='linux', agent=['claude'], write=False, yes=False)
                output = io.StringIO()
                with patch('connect.gateway_connection', return_value=self.gateway), \
                        patch.object(Path, 'resolve', return_value=resolved):
                    self.assertEqual(connect.run(args, checkout, env={}, stdin=io.StringIO(), output=output), 0)
                self.assertIn(f'WARNING: {checkout} uses Git directory indirection (.git file);', output.getvalue())
                self.assertNotIn('fixture-private-value', output.getvalue())

    def test_inaccessible_git_marker_does_not_expose_stat_error(self):
        checkout = Path(self.temporary.name) / 'linked'
        resolved = CheckoutFilesystem(path=checkout, parts=('ordinary', 'linked'))
        with patch.object(Path, 'resolve', return_value=resolved), \
                patch.object(Path, 'is_file', side_effect=PermissionError('fixture-private-location')) as is_file:
            self.assertIsNone(connect.scratch_part(checkout))
        is_file.assert_called_once_with()

    def test_plain_worktrees_directory_does_not_imply_linked_checkout(self):
        checkout = Path(self.temporary.name) / 'worktrees/agentlocker'
        (checkout / '.git').mkdir(parents=True)
        resolved = CheckoutFilesystem(path=checkout, parts=('ordinary', 'worktrees', 'agentlocker'))
        with patch.object(Path, 'resolve', return_value=resolved):
            self.assertIsNone(connect.scratch_part(checkout))

    def test_explicit_scratch_warning_takes_precedence_over_git_file(self):
        checkout = Path(self.temporary.name) / '.scratch/linked'
        checkout.mkdir(parents=True)
        (checkout / '.git').write_text('gitdir: fixture-private-location\n', encoding='utf-8')
        resolved = CheckoutFilesystem(path=checkout, parts=('ordinary', '.scratch', 'linked'))
        with patch.object(Path, 'resolve', return_value=resolved):
            self.assertEqual(connect.scratch_part(checkout), '.scratch')

    def test_ordinary_checkout_only_warns_for_explicit_temp_ancestors(self):
        checkout = Path(self.temporary.name) / 'agentlocker'
        (checkout / '.git').mkdir(parents=True)
        args = SimpleNamespace(home=str(self.home), platform='linux', agent=None, write=False, yes=False)
        with patch('connect.gateway_connection', return_value=self.gateway):
            connect.run(args, checkout, env={}, stdin=io.StringIO(), output=self.output)
        ancestor = next((part for part in checkout.resolve().parts if part in ('.scratch', '.worktrees')), None)
        if ancestor:
            self.assertEqual(self.output.getvalue().splitlines()[0],
                             f'WARNING: {checkout} is under {ancestor}; deleting it breaks every connected client. '
                             'Clone agentlocker somewhere permanent and rerun.')
        else:
            self.assertNotIn('WARNING', self.output.getvalue())

    def test_both_opencode_files_refuse_naming_both_and_connect_others(self):
        path = self.home / '.config/opencode/opencode.json'
        alternative = path.with_suffix('.jsonc')
        alternative.write_text('{}', encoding='utf-8')
        opencode = path.read_bytes()
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(self.run_connect(write=True, yes=True), 1)
        self.assertIn(str(path), errors.getvalue())
        self.assertIn(str(alternative), errors.getvalue())
        self.assertEqual(path.read_bytes(), opencode)
        self.assertEqual(alternative.read_text(encoding='utf-8'), '{}')
        self.assertEqual(list(path.parent.glob('*.bak')), [])
        self.assertIn('agentlocker', json.loads((self.home / '.claude.json').read_text(encoding='utf-8'))['mcpServers'])

    def test_new_file_has_no_backup_and_malformed_preservation(self):
        path = self.home / '.codex/config.toml'
        path.unlink()
        self.run_connect(write=True, yes=True, agent=['codex'])
        self.assertTrue(path.exists())
        self.assertEqual(list(path.parent.glob('*.bak')), [])
        path = self.home / '.cursor/mcp.json'
        path.write_text('{"mcpServers":1}', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.run_connect(write=True, yes=True)
        self.assertEqual(path.read_text(encoding='utf-8'), '{"mcpServers":1}')
        for text in ('{bad', '{"mcpServers":{},"mcpServers":{}}'):
            path.write_text(text, encoding='utf-8')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(self.run_connect(write=True, yes=True), 1)
            self.assertEqual(path.read_text(encoding='utf-8'), text)


class ConnectOutputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.home = self.base / 'home'
        shutil.copytree(FIXTURES / 'linux', self.home)
        self.gateway = {'status': 'stopped', 'snippets': {}}
        self.output = io.StringIO()

    def run_connect(self, **kwargs):
        args = SimpleNamespace(home=str(self.home), platform='linux', agent=None, write=False, yes=False)
        for key, value in kwargs.items():
            setattr(args, key, value)
        with patch('connect.gateway_connection', return_value=self.gateway):
            return connect.run(args, ROOT, env={}, stdin=io.StringIO(), output=self.output)

    def stub(self, name, body):
        directory = self.base / 'bin'
        directory.mkdir(exist_ok=True)
        path = directory / name
        path.write_text(body, encoding='utf-8')
        path.chmod(0o755)
        return directory

    def test_checkout_path_keeps_the_spelling_the_user_is_in(self):
        link = self.base / 'Checkout'
        link.symlink_to(ROOT, target_is_directory=True)
        with patch.dict(os.environ, {'PWD': str(link)}):
            self.assertEqual(self.run_connect(), 0)
        self.assertIn(escaped(link / 'mcp_endpoint.py'), self.output.getvalue())
        self.assertIn(str(link / 'hub.py'), self.output.getvalue())
        self.assertNotIn(escaped(ROOT / 'mcp_endpoint.py'), self.output.getvalue())
        self.assertNotIn(str(ROOT / 'hub.py'), self.output.getvalue())

    def test_cli_keeps_the_invoked_checkout_path(self):
        link = self.base / 'Checkout'
        link.symlink_to(ROOT, target_is_directory=True)
        result = subprocess.run([sys.executable, str(link / 'hub.py'), 'connect', '--home', str(self.home)],
                                cwd=self.base, capture_output=True, text=True, env={**UTF8, 'PWD': str(self.base)}, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(escaped(link / 'mcp_endpoint.py'), result.stdout)
        self.assertNotIn(escaped(ROOT / 'mcp_endpoint.py'), result.stdout)
        self.assertNotIn(str(ROOT / 'hub.py'), result.stdout)

    def test_unrelated_pwd_does_not_change_the_path(self):
        with patch.dict(os.environ, {'PWD': str(self.base)}):
            self.assertEqual(self.run_connect(), 0)
        self.assertIn(escaped(ROOT / 'mcp_endpoint.py'), self.output.getvalue())

    @unittest.skipIf(os.name == 'nt', 'a shell-script python3 stub is POSIX-only; test_windows_never_uses_the_store_stub_name covers the Windows rule')
    def test_interpreter_prefers_a_working_python3_on_path(self):
        directory = self.stub('python3', '#!/bin/sh\nexec ' + sys.executable + ' "$@"\n')
        with patch.dict(os.environ, {'PATH': str(directory)}), patch('sys.platform', 'linux'):
            self.assertEqual(connect.interpreter(), str(directory / 'python3'))
            value = json.loads(connect.render(ROOT, 'claude'))
        self.assertEqual(value['mcpServers']['agentlocker']['command'], str(directory / 'python3'))

    def test_interpreter_skips_a_broken_or_old_python3(self):
        for body in ('#!/bin/sh\nexit 9\n', '#!/bin/sh\nexit 1\n'):
            directory = self.stub('python3', body)
            with patch.dict(os.environ, {'PATH': str(directory)}), patch('sys.platform', 'linux'):
                self.assertEqual(connect.interpreter(), sys.executable)

    def test_interpreter_without_python3_on_path(self):
        with patch.dict(os.environ, {'PATH': str(self.base / 'empty')}), patch('sys.platform', 'linux'):
            self.assertEqual(connect.interpreter(), sys.executable)

    def test_windows_never_uses_the_store_stub_name(self):
        directory = self.stub('python3', '#!/bin/sh\nexec ' + sys.executable + ' "$@"\n')
        with patch.dict(os.environ, {'PATH': str(directory)}), patch('sys.platform', 'win32'), \
                patch('sys.executable', 'C:\\Python312\\python.exe'):
            self.assertEqual(connect.interpreter(), 'C:\\Python312\\python.exe')
            for agent in connect.AGENTS:
                rendered = connect.render(ROOT, agent, absolute=False)
                self.assertNotIn('python3', rendered)
                self.assertIn('Python312', rendered)

    def test_printed_commands_use_the_running_interpreter_on_windows(self):
        with patch('sys.platform', 'win32'), patch('sys.executable', 'C:\\Python312\\python.exe'), \
                patch.dict(os.environ, {'PWD': str(ROOT)}):
            self.assertEqual(self.run_connect(), 0)
            self.assertEqual(connect.python_command(), 'C:\\Python312\\python.exe')
        skills = [line for line in self.output.getvalue().splitlines() if line.startswith('Skills: ')]
        self.assertTrue(skills)
        for line in skills:
            self.assertIn('. Run: C:\\Python312\\python.exe ', line)
            self.assertIn(' "' + str(ROOT / 'hub.py') + '" link ', line)
            self.assertNotIn("'", line)
        with patch('sys.platform', 'linux'):
            self.assertEqual(connect.python_command(), 'python3')

    def test_printed_command_quotes_an_interpreter_path_with_spaces(self):
        with patch('sys.platform', 'win32'), patch('sys.executable', 'C:\\Program Files\\Python\\python.exe'):
            self.assertEqual(connect.python_command(), '"C:\\Program Files\\Python\\python.exe"')

    def test_missing_cursor_and_opencode_configs_are_not_detected(self):
        (self.home / '.cursor/mcp.json').unlink()
        (self.home / '.config/opencode/opencode.json').unlink()
        self.assertEqual(self.run_connect(write=True, yes=True), 0)
        text = self.output.getvalue()
        self.assertIn('Cursor — not detected; to add it, ', text)
        self.assertIn('OpenCode — not detected; to add it, ', text)
        self.assertIn(str(self.home / '.cursor/mcp.json'), text)
        self.assertNotIn('Cursor — ' + str(self.home), text)
        self.assertEqual(text.count('Connected:'), 2)
        self.assertFalse((self.home / '.cursor/mcp.json').exists())
        self.assertFalse((self.home / '.config/opencode/opencode.json').exists())

    def test_present_configs_are_still_detected(self):
        self.assertEqual(self.run_connect(), 0)
        self.assertNotIn('not detected', self.output.getvalue())

    def test_only_missing_clients_report_no_detection(self):
        for name in ('.cursor/mcp.json', '.config/opencode/opencode.json'):
            (self.home / name).unlink()
        self.assertEqual(self.run_connect(agent=['cursor', 'opencode']), 0)
        self.assertIn('No matching configured agents detected.', self.output.getvalue())


class JsonLayoutTests(unittest.TestCase):
    def servers(self, agent='cursor'):
        return connect.entries(ROOT, agent)

    def test_untouched_entries_keep_their_layout(self):
        text = ('{\n  "unrelated": [1,   2],\n  "mcpServers": {\n'
                '    "one": {"command":"a","args":["b"]},\n'
                '    "two": {\n      "command": "c"\n    }\n  },\n  "tail": {"x":1}\n}\n')
        result = connect.merge_json(text, 'cursor', self.servers())
        self.assertIn('  "unrelated": [1,   2],\n', result)
        self.assertIn('    "one": {"command":"a","args":["b"]},\n', result)
        self.assertIn('    "two": {\n      "command": "c"\n    }', result)
        self.assertIn('  "tail": {"x":1}\n}\n', result)
        parsed = json.loads(result)
        self.assertEqual(parsed['mcpServers']['agentlocker'], self.servers()['agentlocker'])
        self.assertEqual(parsed['mcpServers']['one'], {'command': 'a', 'args': ['b']})
        self.assertEqual(connect.merge_json(result, 'cursor', self.servers()), result)

    def test_changed_entry_is_replaced_in_place(self):
        text = ('{\n  "mcpServers": {\n    "agentlocker": {"command": "old"},\n'
                '    "other": {"command":"keep","args":[]}\n  }\n}')
        result = connect.merge_json(text, 'cursor', self.servers())
        self.assertIn('    "other": {"command":"keep","args":[]}\n  }\n}', result)
        self.assertFalse(result.endswith('\n'))
        self.assertEqual(json.loads(result)['mcpServers']['agentlocker'], self.servers()['agentlocker'])
        self.assertEqual(list(json.loads(result)['mcpServers']), ['agentlocker', 'other'])

    def test_new_entry_follows_one_line_siblings(self):
        text = '{\n    "mcpServers": {\n        "other": {"command": "keep"}\n    }\n}\n'
        result = connect.merge_json(text, 'cursor', self.servers())
        line = [part for part in result.splitlines() if '"agentlocker"' in part][0]
        self.assertTrue(line.startswith('        "agentlocker": {"command": '), line)
        self.assertIn('        "other": {"command": "keep"},\n', result)

    def test_missing_group_is_appended(self):
        text = '{\n\t"unrelated": {"x": [1,2]}\n}\n'
        result = connect.merge_json(text, 'opencode', self.servers('opencode'))
        self.assertTrue(result.startswith('{\n\t"unrelated": {"x": [1,2]},\n\t"mcp": {\n\t\t"agentlocker"'), result)
        self.assertEqual(json.loads(result)['mcp'], self.servers('opencode'))

    def test_empty_and_inline_objects(self):
        for text in ('', '{}', '{\n}\n', '{"mcpServers": {}}', '{"a": 1, "mcpServers": {"o": {"c": 1}}}',
                     '{\n  "mcpServers": {}\n}\n'):
            with self.subTest(text=text):
                result = connect.merge_json(text, 'cursor', self.servers())
                expected = json.loads(text) if text.strip() else {}
                expected.setdefault('mcpServers', {}).update(self.servers())
                self.assertEqual(json.loads(result), expected)
                self.assertEqual(connect.merge_json(result, 'cursor', self.servers()), result)

    def test_crlf_is_kept(self):
        text = '{\r\n  "mcpServers": {\r\n    "o": {"c":1}\r\n  }\r\n}\r\n'
        result = connect.merge_json(text, 'cursor', self.servers())
        self.assertNotIn('\n', result.replace('\r\n', ''))
        self.assertIn('    "o": {"c":1},\r\n', result)
        self.assertEqual(json.loads(result)['mcpServers']['o'], {'c': 1})

    def test_strings_with_structural_characters(self):
        text = '{\n  "a,}": "{",\n  "mcpServers": {\n    "o": {"c": "}, \\"{"}\n  }\n}\n'
        result = connect.merge_json(text, 'cursor', self.servers())
        self.assertEqual(json.loads(result)['mcpServers']['o'], {'c': '}, "{'})
        self.assertIn('  "a,}": "{",\n', result)


if __name__ == '__main__':
    unittest.main()
