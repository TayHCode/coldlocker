import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mcp_endpoint import dispatch

ROOT = Path(__file__).resolve().parents[1]


class MergeRuleTests(unittest.TestCase):
    def test_agents_receive_merge_rule(self):
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                   'params': {'name': 'hub_get_item', 'arguments': {'kind': 'rule', 'name': 'merge-when-proven'}}}
        result = dispatch(request, ROOT)['result']
        self.assertFalse(result.get('isError'), result)
        text = json.dumps(result)
        for phrase in ('same head SHA', 'Codex review', 'gh pr ready <N>',
                       'gh pr merge <N> --squash --match-head-commit <SHA>'):
            self.assertIn(phrase, text)
        with tempfile.TemporaryDirectory() as target:
            subprocess.run(['git', 'init', '-q', target], check=True)
            done = subprocess.run([sys.executable, str(ROOT / 'hub.py'), 'deliver-rules', '--target', target,
                                   '--rule', 'merge-when-proven'], capture_output=True, text=True, cwd=ROOT)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            delivered = Path(target) / '.coldlocker/rules/merge-when-proven.md'
            self.assertEqual(delivered.read_text(encoding='utf-8'), (ROOT / 'rules/merge-when-proven.md').read_text(encoding='utf-8'))
            self.assertIn('Read `.coldlocker/rules/merge-when-proven.md`', (Path(target) / 'AGENTS.md').read_text(encoding='utf-8'))
