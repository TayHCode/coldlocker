"""Keep the always-read instructions connected to the shipped routing policy."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ModelPolicyTests(unittest.TestCase):
    def test_every_agent_entrypoint_loads_registered_policy(self):
        self.assertIn("@AGENTS.md", (ROOT / 'CLAUDE.md').read_text())
        self.assertIn('(rules/model-routing.md)', (ROOT / 'AGENTS.md').read_text())
        entry = next(e for e in json.loads((ROOT / 'manifest.json').read_text())['rules']
                     if e['name'] == 'model-routing')
        self.assertEqual(entry['path'], 'rules/model-routing.md')
        self.assertEqual(entry['sha256'], hashlib.sha256((ROOT / entry['path']).read_bytes()).hexdigest())

    def test_shipped_policy_preserves_owner_child_allowlist(self):
        text = (ROOT / 'rules/model-routing.md').read_text()
        table = text.split('| Parent | Allowed subagent models |\n', 1)[1].split('\n\n', 1)[0]
        rows = [tuple(cell.strip() for cell in line.strip('|').split('|'))
                for line in table.splitlines()[1:]]
        self.assertEqual(rows, [
            ('Astra or Opus', 'Sol, Terra, Sonnet, Haiku, Luna'),
            ('Sol', 'Sonnet, Terra, Luna, Haiku'),
            ('Sonnet or Terra', 'Haiku, Luna'),
            ('Haiku or Luna', 'None'),
        ])
        for required in ('they do not override the installing user\'s policy or authorize routing edits',
                         'report the dispatch\nINCOMPLETE rather than silently substituting',
                         'A fallback does not override an\nexplicit delegation restriction',
                         'obtain authorization before opening a separate\ntop-level thread',
                         'not permission to\nchange routing or bypass a host restriction',
                         'Use the installing user\'s configured roles and limits.'):
            self.assertIn(required, text)
