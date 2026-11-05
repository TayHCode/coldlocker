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
        text = ' '.join(json.loads(result['content'][0]['text'])['files']['rules/merge-when-proven.md'].split())
        for phrase in ('Verification alone does not grant merge permission.',
                       'The verify loop passes at that exact head',
                       'The independent code review of that head is clean, or it found only P2/P3 issues',
                       'fixes pass verification at the updated head',
                       'Record the reviewed head and the verified fix commits',
                       'P0/P1 fixes and changes beyond the reviewed work require another review.',
                       'Verified P2/P3 fixes do not require another review.',
                       'Failed verification or an incomplete review does not permit merging.',
                       'Never bypass required checks or branch protection.', 'gh pr ready <N>',
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

    def test_agents_receive_review_severity_policy(self):
        request = {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                   'params': {'name': 'hub_get_item', 'arguments': {'kind': 'rule', 'name': 'shared-policy'}}}
        result = dispatch(request, ROOT)['result']
        self.assertFalse(result.get('isError'), result)
        text = ' '.join(json.loads(result['content'][0]['text'])['files']['rules/shared-policy.md'].split())
        for phrase in ('Judge findings by severity, not the reviewer\'s overall PASS or FAIL label.',
                       'If any P0/P1 findings appear, fix them and request another review.',
                       'If only P2/P3 findings appear, fix them and run the relevant tests and verification at the updated head.',
                       'Merge when authorized without another review for those fixes.',
                       'Changes beyond the reviewed work still need review.',
                       'Preserve required checks and branch protection.'):
            self.assertIn(phrase, text)
