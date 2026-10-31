import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import runtime_proofs as proofs


class Workspace(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".scratch/test-root"
        scratch.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.state = self.dir / "state.json"
        self.ledger = self.dir / "ledger.jsonl"
        self.runs = []


class CheckpointResumeTests(Workspace):
    def steps(self, effect):
        def make(name):
            def step(ledger):
                self.runs.append(name)
                return effect(name)
            return (name, step)
        return [make("fetch"), make("write"), make("notify")]

    def test_resume_after_completed_step_does_not_rerun_it(self):
        steps = self.steps(lambda name: "ok")
        with self.assertRaises(proofs.Interrupted):
            proofs.run_steps(steps, self.state, interrupt_after=("write", "after-receipt"))
        self.assertEqual(self.runs, ["fetch", "write"])
        done = proofs.run_steps(steps, self.state)
        self.assertEqual(self.runs, ["fetch", "write", "notify"])
        self.assertEqual(set(done), {"fetch", "write", "notify"})

    def test_side_effect_before_receipt_with_key_applies_once(self):
        steps = self.steps(lambda name: proofs.keyed_effect(name, self.ledger, "x"))
        with self.assertRaises(proofs.Interrupted):
            proofs.run_steps(steps, self.state, interrupt_after=("write", "before-receipt"))
        done = proofs.run_steps(steps, self.state)
        applied = [json.loads(line)["key"] for line in self.ledger.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sorted(applied), ["fetch", "notify", "write"])
        self.assertEqual(done["write"], "already-applied")

    def test_side_effect_before_receipt_without_key_duplicates(self):
        steps = self.steps(lambda name: proofs.unkeyed_effect(self.ledger, name))
        with self.assertRaises(proofs.Interrupted):
            proofs.run_steps(steps, self.state, interrupt_after=("write", "before-receipt"))
        proofs.run_steps(steps, self.state)
        applied = [json.loads(line)["payload"] for line in self.ledger.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(applied.count("write"), 2)


class EvalBeforeAfterTests(unittest.TestCase):
    def test_eval_fails_on_planted_bug_and_passes_when_fixed(self):
        before = proofs.run_eval(proofs.median_buggy)
        after = proofs.run_eval(proofs.median)
        self.assertEqual((before["passed"], before["total"]), (2, 3))
        self.assertEqual(before["failures"], [([4, 1, 3, 2], 2.5, 3)])
        self.assertEqual((after["passed"], after["total"]), (3, 3))


class TokenTraceTests(unittest.TestCase):
    def test_trace_records_metadata_only_and_totals_match_known_counts(self):
        trace = []
        proofs.record_turn(trace, 1000, 0, 200)
        proofs.record_turn(trace, 1300, 1000, 150)
        proofs.record_turn(trace, 1500, 1300, 90)
        self.assertEqual(proofs.total(trace), {"input": 3800, "cached": 2300, "output": 440})
        for turn in trace:
            self.assertEqual(set(turn), {"turn", "input", "cached", "output"})
            self.assertTrue(all(isinstance(value, int) for value in turn.values()))

    def test_cached_cannot_exceed_input(self):
        with self.assertRaises(ValueError):
            proofs.record_turn([], 10, 11, 1)



class ProcessRestartTests(Workspace):
    def test_fresh_process_resumes_without_repeating_effect(self):
        script = """
import sys
from pathlib import Path
from tools.runtime_proofs import run_steps, keyed_effect, Interrupted
root = Path(sys.argv[1])
steps = [(name, lambda _, name=name: keyed_effect(name, root / 'ledger.jsonl', name)) for name in ('fetch', 'write')]
try:
    run_steps(steps, root / 'state.json', interrupt_after=('fetch', sys.argv[2]) if len(sys.argv) > 2 else None)
except Interrupted:
    sys.exit(9)
"""
        for boundary in ('before-receipt', 'after-receipt'):
            root = self.dir / boundary
            root.mkdir()
            interrupted = subprocess.run([sys.executable, '-c', script, str(root), boundary], cwd=ROOT)
            self.assertEqual(interrupted.returncode, 9)
            subprocess.run([sys.executable, '-c', script, str(root)], cwd=ROOT, check=True)
            records = [json.loads(line) for line in (root / 'ledger.jsonl').read_text().splitlines()]
            self.assertEqual([record['key'] for record in records], ['fetch', 'write'])
            self.assertEqual(set(json.loads((root / 'state.json').read_text())), {'fetch', 'write'})


if __name__ == "__main__":
    unittest.main()
