from pathlib import Path
import subprocess
import sys
import unittest

import orient


ROOT = Path(__file__).resolve().parents[1]


class OrientTests(unittest.TestCase):
    def test_command_prints_state_rules_prs_and_next_step(self):
        done = subprocess.run([sys.executable, str(ROOT / "hub.py"), "orient"], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertRegex(done.stdout, r"(?m)^repo: .+ branch: .+ head: \S+ +dirty: \d+$")
        for rule in sorted((ROOT / "rules").glob("*.md")):
            self.assertIn(f"  {rule.stem}: ", done.stdout)
        self.assertRegex(done.stdout, r"(?m)^open prs: (\d+|unavailable \(gh\))$")
        self.assertRegex(done.stdout, r"(?m)^next: .+$")
        self.assertLess(len(done.stdout), 4000)

    def test_next_step_follows_state(self):
        prs = [{"number": 7, "headRefName": "feature", "title": "t", "isDraft": False}]
        self.assertEqual(orient.next_step("feature", 2, prs), "commit the working changes")
        self.assertIn("PR #7", orient.next_step("feature", 0, prs))
        self.assertEqual(orient.next_step("other", 0, prs), "open a pull request for this branch")
        self.assertIn("not a git checkout", orient.next_step("?", 0, None))
        self.assertIn("worktrees", orient.next_step("main", 0, None))


if __name__ == "__main__":
    unittest.main()
