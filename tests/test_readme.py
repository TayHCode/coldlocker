import ast
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

import gateway
from hub_ui import connect_snippets, gateway_connection


ROOT = Path(__file__).resolve().parents[1]


def quickstart():
    text = (ROOT / 'README.md').read_text(encoding='utf-8')
    return text.split('## Quickstart\n', 1)[1].split('\n## ', 1)[0]


def install_doc():
    return (ROOT / 'docs/install.md').read_text(encoding='utf-8')


def blocks(language):
    return ['\n'.join(line.removeprefix('   ') for line in block.splitlines())
            for block in re.findall(r'^   ```' + language + r'\n(.*?)^   ```', quickstart(), re.M | re.S)]


def install_blocks(language):
    return re.findall(r'^```' + language + r'\n(.*?)^```', install_doc(), re.M | re.S)


class ReadmeTests(unittest.TestCase):
    def test_quickstart_has_three_numbered_steps(self):
        self.assertEqual(re.findall(r'^(\d+)\. ', quickstart(), re.M), ['1', '2', '3'])

    def test_fenced_commands_use_existing_subcommands(self):
        commands = blocks('sh')
        self.assertTrue(commands)
        gateway_tree = ast.parse((ROOT / 'gateway.py').read_text(encoding='utf-8'))
        gateway_choices = next(ast.literal_eval(keyword.value)
                               for node in ast.walk(gateway_tree)
                               if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                               and node.func.attr == 'add_argument'
                               for keyword in node.keywords if keyword.arg == 'choices')
        for block in commands:
            for line in block.splitlines():
                for command in line.strip().split(' && '):
                    argv = shlex.split(command)
                    self.assertGreaterEqual(len(argv), 3)
                    self.assertEqual(argv[0], 'python3')
                    self.assertIn(argv[1], ('hub.py', 'gateway.py'))
                    if argv[1] == 'gateway.py':
                        self.assertIn(argv[2], gateway_choices)
                    result = subprocess.run([sys.executable, *argv[1:], '--help'], cwd=ROOT,
                                            capture_output=True, text=True, encoding='utf-8')
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_clone_uses_existing_auth_with_bundle_fallback(self):
        step = install_doc()
        self.assertIn('gh repo clone <OWNER>/agentlocker', step)
        self.assertIn('git clone <HTTPS_URL> agentlocker', step)
        self.assertNotIn('git@', step)
        clone = 'git clone --branch <APPROVED_BRANCH> agentlocker.bundle agentlocker'
        self.assertLess(step.index('git bundle create'), step.index(clone))
        self.assertLess(step.index(clone), step.index('remote set-url origin <HTTPS_URL>'))

    def test_upgrade_guide_commands_exist_and_is_linked(self):
        text = (ROOT / 'docs/upgrade.md').read_text(encoding='utf-8')
        commands = re.findall(r'^   ?python3 (hub|gateway)\.py (\S+)(.*)$', text, re.M)
        self.assertTrue(commands)
        for script, subcommand, rest in commands:
            result = subprocess.run([sys.executable, script + '.py', subcommand, '--help'], cwd=ROOT,
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, subcommand + result.stderr)
            for flag in re.findall(r'(?<!\S)--[a-z][a-z-]*', rest):
                self.assertIn(flag, result.stdout, f'{subcommand} {flag}')
        self.assertIn('git diff --no-index ../agentlocker-backup/.hub-store/catalog/manifest.json .hub-store/catalog/manifest.json', text)
        for tool in ('grep', 'diff'):
            self.assertIsNone(re.search(rf'(?<![\w-]){tool} ', text.replace('git diff', '')), tool)
        for source in ('README.md', 'docs/install.md'):
            self.assertIn('upgrade.md', (ROOT / source).read_text(encoding='utf-8'))
        self.assertIn('docs/upgrade.md', (ROOT / 'public-export.txt').read_text(encoding='utf-8').splitlines())

    def test_gateway_install_precedes_prepare_and_start(self):
        commands = '\n'.join(install_blocks('sh'))
        self.assertLess(commands.index('python3 gateway.py install'),
                        commands.index('python3 gateway.py prepare'))
        self.assertLess(commands.index('python3 gateway.py prepare'),
                        commands.index('python3 gateway.py start'))

    def test_relative_links_exist(self):
        links = re.findall(r'\]\(([^)]+)\)', (ROOT / 'README.md').read_text(encoding='utf-8'))
        self.assertTrue(links)
        for link in links:
            parsed = urlsplit(link)
            if not parsed.scheme and parsed.path:
                self.assertTrue((ROOT / unquote(parsed.path)).is_file(), link)

    def test_connection_fragments_match_wizard(self):
        with patch('sys.platform', 'linux'):
            snippets = {client: text.replace('\\\\', '/')
                        for client, text in connect_snippets(Path('<HUB_DIR>')).items()}
        catalog_json, claude_json, cursor_json = map(json.loads, install_blocks('json'))
        catalog_toml, gateway_toml = (block.strip() for block in install_blocks('toml'))
        for client in ('Claude', 'Cursor'):
            self.assertEqual(catalog_json, json.loads(snippets[client]))
        self.assertEqual(catalog_toml, snippets['Codex'].strip())
        with patch('hub_ui.state', return_value={'port': 43871}):
            gateway = gateway_connection(ROOT)
        for client, fragment in (('Claude', claude_json), ('Cursor', cursor_json)):
            fragment['mcpServers']['agentlocker-gateway']['url'] = gateway['url']
            self.assertEqual(fragment, json.loads(gateway['snippets'][client]))
        self.assertEqual(gateway_toml.replace('<GATEWAY_URL>', gateway['url']),
                         gateway['snippets']['Codex'].strip())

    def test_clients_doc_covers_login_free_proof_and_try_commands(self):
        document = (ROOT / 'docs/clients.md').read_text(encoding='utf-8')
        for expected in ('403', '401', 'mcp list', 'tools/list',
                         '--mcp-config', '--strict-mcp-config', '< /dev/null',
                         '--ignore-user-config', 'OPENCODE_CONFIG', 'XDG_CONFIG_HOME',
                         'agent --version', 'cursor-agent'):
            with self.subTest(expected=expected):
                self.assertIn(expected, document)


    def test_quickstart_notes_windows_and_macos(self):
        text = install_doc()
        self.assertIn('py -3', text)
        self.assertRegex(text, r'(?is)windows.{0,80}`python3`')
        self.assertRegex(text, r'(?is)gateway.{0,200}not available on Windows')
        self.assertIn('stdio', text)
        self.assertIn('lsof', text)
        self.assertRegex(text, r'(?is)private store\s+is owner-only on Windows only with Python 3\.12\.4\+')

    def test_node_minimum_matches_gateway_enforcement(self):
        with patch('gateway.prerequisite'), patch('gateway.subprocess.run') as run:
            run.return_value.stdout = 'v20.0.0\n'
            gateway.check_node()
            run.return_value.stdout = 'v19.9.0\n'
            with self.assertRaises(gateway.SetupError):
                gateway.check_node()
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        install = install_doc()
        gateway_doc = (ROOT / 'docs/gateway.md').read_text(encoding='utf-8')
        self.assertIn('Install Node 20+', install)
        self.assertIn('Node 20 or newer', gateway_doc)
        for text in (readme, install, gateway_doc):
            self.assertNotRegex(text, r'(?i)(install|requires|using) Node 20\.19')

    def test_overview_platforms_match_gateway_support(self):
        text = (ROOT / 'docs/OVERVIEW.md').read_text(encoding='utf-8')
        with patch('sys.platform', 'darwin'):
            gateway.require_supported()
        with patch('sys.platform', 'win32'):
            with self.assertRaises(gateway.SetupError):
                gateway.require_supported()
        self.assertNotIn('Linux is the supported platform', text)
        self.assertNotIn('needs Linux `ss` and `/proc`', text)
        self.assertNotIn('verified with fixtures, not\n  real machines', text)
        self.assertRegex(text, r'(?s)\*\*Platforms\.\*\*.{0,300}Linux, macOS and Windows.{0,200}Linux and macOS')
