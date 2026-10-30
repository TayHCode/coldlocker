import argparse
from pathlib import Path
import re
import shlex
import unittest

import hub
from checks.scan import findings


ROOT = Path(__file__).resolve().parents[1]


class ImportPromptTests(unittest.TestCase):
    def setUp(self):
        self.document = (ROOT / 'docs/IMPORT-PROMPT.md').read_text(encoding='utf-8')
        blocks = re.findall(r'```text\n(.*?)\n```', self.document, re.S)
        self.assertEqual(len(blocks), 1)
        self.block = blocks[0]

    def test_size_and_portability(self):
        self.assertLess(len(self.block.splitlines()), 40)
        self.assertLess(len(self.block), 4000)
        self.assertIn('<HUB_DIR>', self.block)
        self.assertEqual(findings(self.document), [])
        self.assertNotRegex(self.document, r'https?://|\b(?:password|api_key|token)\s*=\s*\S+')

    def test_commands_and_flags_exist_in_real_parser(self):
        parser = hub.parser()
        commands = next(a.choices for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        command = None
        seen = set()
        for match in re.finditer(r'python3 hub\.py ([a-z-]+)|(--[a-z-]+)', self.block):
            if match[1]:
                command = match[1]
                self.assertIn(command, commands)
                seen.add(command)
            else:
                self.assertIsNotNone(command)
                self.assertIn(match[2], commands[command]._option_string_actions)
                self.assertIn(match[2], commands[command].format_help())
        self.assertEqual(seen, {'scan', 'check', 'connect', 'import-skill', 'import-rule',
                               'import-mcp', 'import-routine', 'import-plugin', 'import-connection'})
        for match in re.finditer(r'python3 hub\.py ([a-z-]+)((?: (?:--[a-z-]+|<[A-Z_]+>))*)', self.block):
            argv = shlex.split(re.sub(r'<[A-Z_]+>', 'fixture', match[1] + match[2]))
            with self.subTest(argv=argv):
                parser.parse_args(argv)

    def test_safety_and_workflow_are_in_the_prompt(self):
        for text in ('Never ask for, print, paste or log secret values', 'private store',
                     'secret://', 'approved private auth flow in docs/mcp-auth.md',
                     'enabled=false with one owner and one runner', 'Vendor/plugin_cache',
                     'plugin-cache/system', 'Never write real ~/.claude*', '~/.codex',
                     '~/.cursor', '~/.config/opencode', 'crontab', 'systemd units or T3 state',
                     'No destructive ops, publishing or schedules without approval',
                     'Only explicit user approval permits python3 hub.py connect --write',
                     'wait for approval', 'Without shell access', 'skipped items + why',
                     'what you could not verify', 'Task Scheduler', 'T3 schedules',
                     'hosted-agent exports', 'Executor/MCPHub/Composio/amtiYo-agents'):
            self.assertIn(text, self.block)
        self.assertLess(self.block.index('python3 hub.py scan (dry-run'), self.block.index('wait for approval'))
        self.assertLess(self.block.index('wait for approval'), self.block.index('python3 hub.py scan --import'))
        self.assertLess(self.block.index('python3 hub.py check'), self.block.index('python3 hub.py connect'))

    def test_sandbox_gate_is_documented(self):
        sentence = ('In an agent sandbox python3 hub.py check is the gate; if the sandbox denies '
                    'localhost sockets, ps or crontab, run the full unittest suite outside the sandbox '
                    'and say so in this report')
        normalized = re.sub(r'\s+', ' ', self.block)
        self.assertIn(sentence, normalized)
        self.assertGreater(normalized.index(sentence), normalized.index('5. Report'))
        agents = re.sub(r'\s+', ' ', (ROOT / 'AGENTS.md').read_text(encoding='utf-8'))
        self.assertIn('Inside an agent sandbox `hub.py check` is the gate', agents)
        self.assertIn('outside the sandbox', agents)

    def test_codex_run_notes_are_documented(self):
        self.assertIn('## Running under Codex\n', self.document)
        section = re.sub(r'\s+', ' ', self.document.split('## Running under Codex\n', 1)[1].split('\n## ', 1)[0])
        for phrase in ('scan --import', '.hub-store/', 'writable workspace', '--ignore-user-config',
                       '-s danger-full-access', 'approval_policy=never', 'drops sandbox confinement',
                       'CODEX_HOME', 'scratch'):
            self.assertIn(phrase, section)
        self.assertLess(section.index('writable workspace'), section.index('danger-full-access'))
        self.assertLess(section.index('danger-full-access'), section.index('CODEX_HOME'))

    def test_no_repo_access_section_documents_bundle_clone(self):
        section = self.document.split('## No repo access on this machine\n', 1)[1]
        self.assertIn('gh repo clone <OWNER>/agentlocker', self.document)
        self.assertNotIn('git@', self.document)
        self.assertLess(section.index('git bundle create'), section.index('git clone agentlocker.bundle'))
        self.assertLess(section.index('git clone agentlocker.bundle'), section.index('remote set-url origin <HTTPS_URL>'))

    def test_readme_links_resolve_and_entrypoints_link_prompt(self):
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)', readme):
            if '://' not in target and not target.startswith('#'):
                self.assertTrue((ROOT / target.split('#')[0]).exists(), target)
        for relative in ('README.md', 'AGENTS.md', 'docs/scan.md'):
            path = ROOT / relative
            targets = re.findall(r'\[[^\]]+\]\(([^)]+)\)', path.read_text(encoding='utf-8'))
            self.assertTrue(any((path.parent / t).resolve() == ROOT / 'docs/IMPORT-PROMPT.md'
                                for t in targets if '://' not in t))


if __name__ == '__main__':
    unittest.main()
