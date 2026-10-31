import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mcp_endpoint import dispatch


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "merge-when-proven": ("gh pr ready <N>", "gh pr merge <N> --squash --match-head-commit <SHA>", "same head SHA",
                          "Codex review", "Coldfix", "Archive your own thread", "ask Taylor nothing",
                          "geometry proof", "next step"),
    "orient-once": ("Extend the repo's existing orientation command", "run that command first", "Measure before and after", "python3 hub.py orient"),
}


class RuleTests(unittest.TestCase):
    def test_shipped_and_served_to_agents_with_the_required_text(self):
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        exported = (ROOT / "public-export.txt").read_text(encoding="utf-8").splitlines()
        for name, required in REQUIRED.items():
            with self.subTest(rule=name):
                self.assertIn(name, [rule["name"] for rule in manifest["rules"]])
                self.assertIn(f"rules/{name}.md", exported)
                request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "hub_get_item", "arguments": {"kind": "rule", "name": name}}}
                result = dispatch(request, ROOT)["result"]
                self.assertFalse(result.get("isError"), result)
                text = json.dumps(result)
                for phrase in required:
                    self.assertIn(phrase, text)

    def test_deliver_rules_loads_them_into_a_repository(self):
        scratch = ROOT / ".scratch"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as target:
            subprocess.run(["git", "init", "-q", target], check=True)
            for name in REQUIRED:
                done = subprocess.run([sys.executable, str(ROOT / "hub.py"), "deliver-rules", "--target", target,
                                       "--rule", name], capture_output=True, text=True, cwd=ROOT)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            registry = json.loads((Path(target) / ".coldlocker/rules.json").read_text(encoding="utf-8"))
            instructions = (Path(target) / "AGENTS.md").read_text(encoding="utf-8")
            for name in REQUIRED:
                with self.subTest(rule=name):
                    delivered = Path(target) / ".coldlocker/rules" / f"{name}.md"
                    self.assertEqual(delivered.read_text(encoding="utf-8"),
                                     (ROOT / f"rules/{name}.md").read_text(encoding="utf-8"))
                    self.assertFalse(registry[name]["pending"])
                    self.assertIn(f"- Read `.coldlocker/rules/{name}.md`.", instructions)


if __name__ == "__main__":
    unittest.main()
