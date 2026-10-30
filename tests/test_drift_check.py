import json
import os
from pathlib import Path
import shlex
import runpy
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "drift_check.py"
REVIEWER = """import sys
from pathlib import Path
root = Path(sys.argv[1])
with (root / "calls.log").open("a", encoding="utf-8") as log:
    log.write(sys.stdin.read() + "\\n<<END>>\\n")
print((root / "verdict.txt").read_text(encoding="utf-8"))
"""


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   check=True, capture_output=True)


class DriftCheckHookTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        (self.repo / "app.py").write_text("print('hi')\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")
        (self.root / "reviewer.py").write_text(REVIEWER, encoding="utf-8")
        self.verdict("OK")
        self.env = {**os.environ, "TMPDIR": str(self.root),
                    "AGENTLOCKER_DRIFT_CMD": shlex.join([sys.executable, str(self.root / "reviewer.py"), str(self.root)])}
        fleet = self.root / ".local" / "bin" / "cold"
        fleet.parent.mkdir(parents=True)
        fleet.write_text("""#!/usr/bin/env python3
import json, sys
from pathlib import Path
root = Path(__file__).resolve().parents[2]
with (root / 'routes.log').open('a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
print((root / 'route.json').read_text())
""", encoding="utf-8")
        fleet.chmod(0o755)
        if os.name == "nt":
            fleet.with_suffix(".cmd").write_text(f'@"{sys.executable}" "{fleet}" %*\n', encoding="utf-8")
        self.env["PATH"] = str(fleet.parent) + os.pathsep + (self.env["PATH"] if os.name == "nt" else os.defpath)
        self.route = self.root / "route.json"
        self.route.write_text(json.dumps(dict(provider="codex", model="review-model", effort="high")))
        self.env.update(HOME=str(self.root), USERPROFILE=str(self.root),
                        AGENTLOCKER_DRIFT_BUILDER_MODELS="builder-model")
        self.env.pop("AGENTLOCKER_DRIFT_ACTIVE", None)
        self.transcript("Fix the greeting typo")

    def verdict(self, text):
        (self.root / "verdict.txt").write_text(text, encoding="utf-8")

    def transcript(self, prompt):
        rows = [{"type": "user", "message": {"role": "user", "content": prompt}},
                {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit"}]}},
                {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}}]
        (self.root / "t.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    def calls(self):
        log = self.root / "calls.log"
        return log.read_text(encoding="utf-8").split("\n<<END>>\n")[:-1] if log.exists() else []

    def hook(self, env=None):
        payload = {"hook_event_name": "Stop", "session_id": "s1", "cwd": str(self.repo),
                   "transcript_path": str(self.root / "t.jsonl"), "last_assistant_message": "Done."}
        result = subprocess.run([sys.executable, "-I", str(HOOK)], input=json.dumps(payload), text=True,
                                capture_output=True, encoding="utf-8", env=env or self.env, check=True)
        return json.loads(result.stdout)

    def change(self, text="print('hello')\n"):
        (self.repo / "app.py").write_text(text, encoding="utf-8")

    def test_allows_without_review_when_nothing_changed(self):
        self.assertEqual(self.hook(), {})
        self.assertEqual(self.calls(), [])

    def test_blocks_with_reviewer_cuts_and_sends_goal_and_diff(self):
        self.change()
        self.verdict("DRIFT\n- app.py: drop the plugin registry")
        out = self.hook()
        self.assertEqual(out["decision"], "block")
        self.assertIn("round 1", out["reason"])
        self.assertIn("- app.py: drop the plugin registry", out["reason"])
        prompt = self.calls()[0]
        self.assertIn("Fix the greeting typo", prompt)
        self.assertIn("+print('hello')", prompt)
        self.assertIn("over-engineered", prompt)

    def test_reviews_untracked_files_and_committed_branch_work(self):
        base = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
        git(self.repo, "update-ref", "refs/remotes/origin/main", base)
        git(self.repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        self.change()
        git(self.repo, "commit", "-qam", "work")
        (self.repo / "framework.py").write_text("class PluginRegistry: pass\n", encoding="utf-8")
        self.hook()
        prompt = self.calls()[0]
        self.assertIn("+print('hello')", prompt)
        self.assertIn("new untracked file framework.py", prompt)
        self.assertIn("class PluginRegistry", prompt)

    def test_approved_diff_is_reused_but_route_is_resolved_each_time(self):
        self.change()
        self.assertEqual(self.hook(), {})
        self.assertEqual(self.hook(), {})
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(len((self.root / "routes.log").read_text().splitlines()), 2)
        self.change("print('hello!')\n")
        self.hook()
        self.assertEqual(len(self.calls()), 2)

    def test_unresolved_drift_keeps_blocking(self):
        self.verdict("DRIFT\n- cut the cache layer")
        for round_number in (1, 2, 3):
            self.change(f"print({round_number})\n")
            out = self.hook()
            self.assertEqual(out["decision"], "block")
            self.assertIn(f"round {round_number}", out["reason"])
            self.assertIn("cut the cache layer", out["reason"])
        self.assertEqual(len(self.calls()), 3)

    def test_new_user_goal_resets_rounds(self):
        self.verdict("DRIFT\n- cut it")
        for round_number in (1, 2):
            self.change(f"print({round_number})\n")
            self.hook()
        self.transcript("Now add a --verbose flag")
        self.assertIn("round 1", self.hook()["reason"])

    def test_reviewer_failures_block_without_leaking_output_and_can_retry(self):
        self.change()
        for command, verdict, message in (
            (self.env["AGENTLOCKER_DRIFT_CMD"], "garbled private-output", "invalid verdict"),
            (self.env["AGENTLOCKER_DRIFT_CMD"], "DRIFTING private-output", "invalid verdict"),
            ("no-such-drift-reviewer", "OK", "could not start"),
            ("'", "OK", "invalid command"),
            ("   ", "OK", "empty command"),
            (shlex.join([sys.executable, "-c", "import sys; print('private-output'); sys.exit(7)"]),
             "OK", "exit code 7"),
        ):
            with self.subTest(message=message):
                self.verdict(verdict)
                out = self.hook({**self.env, "AGENTLOCKER_DRIFT_CMD": command})
                self.assertEqual(out["decision"], "block")
                self.assertIn("INCOMPLETE", out["reason"])
                self.assertIn(message, out["reason"])
                self.assertNotIn("private-output", json.dumps(out))
        self.verdict("DRIFT\n- cut it")
        self.assertIn("round 1", self.hook()["reason"])
        self.verdict("OK")
        self.change("print('fixed')\n")
        self.assertEqual(self.hook(), {})

    @unittest.skipIf(os.name == "nt", "POSIX process-group integration test")
    def test_reviewer_timeout_stops_wrapper_and_child(self):
        hook = runpy.run_path(str(HOOK))
        marker = self.root / "child-finished"
        started = self.root / "child-started"
        child = (f"import time; from pathlib import Path; Path({str(started)!r}).touch(); "
                 f"time.sleep(1); Path({str(marker)!r}).touch()")
        wrapper = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(10)"
        command = shlex.join([sys.executable, "-c", wrapper])
        with patch.dict(os.environ, {**self.env, "AGENTLOCKER_DRIFT_CMD": command}), \
             patch.dict(hook["stop_decision"].__globals__, goal=lambda _: "goal",
                        changes=lambda _: "diff", REVIEW_TIMEOUT=0.3):
            out = hook["stop_decision"]({"cwd": str(self.repo), "session_id": "timeout"})
        self.assertEqual(out["decision"], "block")
        self.assertIn("INCOMPLETE", out["reason"])
        self.assertIn("timed out", out["reason"])
        self.assertNotIn(command, json.dumps(out))
        self.assertTrue(started.exists(), "reviewer child did not start")
        time.sleep(1)
        self.assertFalse(marker.exists(), "reviewer child survived the timeout")

    def test_windows_timeout_requests_process_tree_termination(self):
        hook = runpy.run_path(str(HOOK))
        with patch.dict(os.environ, self.env), patch("os.name", "nt"), \
             patch("subprocess.Popen") as popen, patch("subprocess.run") as run:
            process = popen.return_value.__enter__.return_value
            process.pid = 123
            process.communicate.side_effect = subprocess.TimeoutExpired("private-command", 120)
            with self.assertRaisesRegex(RuntimeError, "reviewer timed out"):
                hook["review"]("goal", "diff", dict(provider="codex", model="review-model", effort="high"), ["builder-model"])
            self.assertEqual(run.call_args.args[0], ["taskkill", "/PID", "123", "/T", "/F"])
            self.assertEqual(run.call_args.kwargs["timeout"], 10)
            process.kill.assert_called_once()

    def test_route_failure_blocks_even_an_approved_diff(self):
        self.change()
        self.assertEqual(self.hook(), {})
        for route in ("private-output", "[]", "{}",
                      json.dumps(dict(provider="codex", model="builder-model", effort="high")),
                      json.dumps(dict(provider="other", model="review-model", effort="high")),
                      json.dumps(dict(provider="codex", model="review-model", effort=None))):
            with self.subTest(route=route):
                self.route.write_text(route)
                out = self.hook()
                self.assertEqual(out["decision"], "block")
                self.assertIn("INCOMPLETE", out["reason"])
                self.assertNotIn("private-output", out["reason"])
        self.assertEqual(len(self.calls()), 1)

    def test_missing_builder_is_incomplete(self):
        self.change()
        env = {k: v for k, v in self.env.items() if k != "AGENTLOCKER_DRIFT_BUILDER_MODELS"}
        out = self.hook(env)
        self.assertEqual(out["decision"], "block")
        self.assertIn("builder", out["reason"])
        self.assertEqual(self.calls(), [])

    def test_resolves_all_builders_and_live_route_changes(self):
        self.change()
        env = {**self.env, "AGENTLOCKER_DRIFT_BUILDER_MODELS": "builder-model second-builder"}
        self.assertEqual(self.hook(env), {})
        self.route.write_text(json.dumps(dict(provider="codex", model="next-reviewer", effort="low")))
        self.assertEqual(self.hook(env), {})
        self.assertEqual(len(self.calls()), 2)
        routes = [json.loads(line) for line in (self.root / "routes.log").read_text().splitlines()]
        self.assertEqual(routes, [["model", "get", "reviewer", "--builder-model", "builder-model",
                                  "--builder-model", "second-builder"]] * 2)

    def test_unchanged_findings_do_not_spend_another_round(self):
        self.change()
        self.verdict("DRIFT\n- remove the cache")
        self.assertIn("round 1", self.hook()["reason"])
        self.assertIn("round 1", self.hook()["reason"])
        self.assertEqual(len(self.calls()), 1)
        self.change("print('fixed')\n")
        self.verdict("OK")
        self.assertEqual(self.hook(), {})
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(self.hook(), {})
        self.assertEqual(len(self.calls()), 2)

    def test_empty_findings_are_incomplete(self):
        self.change()
        self.verdict("DRIFT")
        out = self.hook()
        self.assertEqual(out["decision"], "block")
        self.assertIn("INCOMPLETE", out["reason"])

    def test_builder_models_are_collected_from_both_transcript_formats(self):
        self.change()
        rows = [
            {"type": "user", "message": {"role": "user", "content": "Fix it"}},
            {"type": "assistant", "message": {"model": "first-builder"}},
            {"type": "turn_context", "payload": {"model": "second-builder"}},
        ]
        (self.root / "t.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
        env = {k: v for k, v in self.env.items() if k != "AGENTLOCKER_DRIFT_BUILDER_MODELS"}
        self.assertEqual(self.hook(env), {})
        args = json.loads((self.root / "routes.log").read_text())
        self.assertEqual(args, ["model", "get", "reviewer", "--builder-model", "first-builder",
                                "--builder-model", "second-builder"])

    def test_resolver_missing_or_nonzero_blocks_without_output(self):
        self.change()
        fleet = self.root / ".local" / "bin" / "cold"
        fleet.write_text("import sys; print('private-output'); sys.exit(7)")
        for missing in (False, True):
            if missing:
                fleet.unlink()
            out = self.hook()
            self.assertEqual(out["decision"], "block")
            self.assertIn("INCOMPLETE", out["reason"])
            self.assertNotIn("private-output", out["reason"])
        self.assertEqual(self.calls(), [])

    def test_resolver_timeout_is_incomplete(self):
        hook = runpy.run_path(str(HOOK))
        with patch.dict(os.environ, self.env), patch("subprocess.run") as run:
            run.side_effect = subprocess.TimeoutExpired("private-command", 120)
            with self.assertRaisesRegex(RuntimeError, "live reviewer route"):
                hook["reviewer_route"]({})

    def test_default_codex_command_uses_routed_model_effort_and_final_message(self):
        hook = runpy.run_path(str(HOOK))
        env = {k: v for k, v in self.env.items() if k != "AGENTLOCKER_DRIFT_CMD"}
        with patch.dict(os.environ, env, clear=True), patch("subprocess.Popen") as popen:
            process = popen.return_value.__enter__.return_value
            process.communicate.return_value = ("provider startup logs", "")
            process.returncode = 0
            def launch(command, **kwargs):
                Path(command[command.index("--output-last-message") + 1]).write_text("OK")
                return popen.return_value
            popen.side_effect = launch
            self.assertEqual(hook["review"]("goal", "diff",
                             dict(provider="codex", model="routed-reviewer", effort="low"), ["builder"]), "")
            command = popen.call_args.args[0]
            self.assertEqual(command[command.index("--model") + 1], "routed-reviewer")
            self.assertIn('model_reasoning_effort="low"', command)
            self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
            self.assertIn("fleet-builder-model: builder", process.communicate.call_args.args[0])
            self.assertEqual(json.loads(popen.call_args.kwargs["env"]["AGENTLOCKER_DRIFT_ROUTE"]),
                             dict(provider="codex", model="routed-reviewer", effort="low"))
            popen.side_effect = None
            with self.assertRaisesRegex(RuntimeError, "did not write a verdict"):
                hook["review"]("goal", "diff",
                               dict(provider="codex", model="routed-reviewer", effort="default"), ["builder"])

    def test_changed_command_requires_review(self):
        self.change()
        self.assertEqual(self.hook({**self.env, "AGENTLOCKER_DRIFT_MODEL": "unused"}), {})
        out = self.hook({**self.env, "AGENTLOCKER_DRIFT_CMD": "missing-reviewer"})
        self.assertEqual(out["decision"], "block")

    def test_reviewer_run_does_not_trigger_itself(self):
        self.change()
        self.verdict("DRIFT\n- cut it")
        self.assertEqual(self.hook({**self.env, "AGENTLOCKER_DRIFT_ACTIVE": "1"}), {})
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
