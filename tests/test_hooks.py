import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))
import install  # noqa: E402

DONE = "Goal: fix\nDone condition: test passes\nEvidence: 1 test OK\nRemaining: none\nStatus: DONE"
BLOCKED = "Goal: fix\nDone condition: test passes\nEvidence: no access\nRemaining: credentials\nStatus: BLOCKED"
PARTIAL = "Goal: fix\nDone condition: test passes\nEvidence: half done\nRemaining: tests\nStatus: PARTIAL"


def write_transcript(root, with_tool):
    rows = [{"type": "user", "message": {"role": "user", "content": "fix it"}}]
    if with_tool:
        rows.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}})
        rows.append({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}})
    path = root / f"t{with_tool}.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    return str(path)


def run_hook(command, payload):
    result = subprocess.run(command, shell=True, input=json.dumps(payload), text=True,
                            capture_output=True, encoding="utf-8")
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class KeepGoingHookTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def hook(self, message, with_tool=True, **extra):
        return run_hook(install.hook_command(sys.executable),
                        {"hook_event_name": "Stop", "last_assistant_message": message,
                         "transcript_path": write_transcript(self.root, with_tool), **extra})

    def test_blocks_partial_and_unreported_tool_turns(self):
        for message in (PARTIAL, "All finished.", "Status: DONE", DONE + "\nActually the deploy still fails.",
                        DONE.replace("Remaining: none", "Remaining: deploy")):
            out = self.hook(message)
            self.assertEqual(out["decision"], "block", message)
            self.assertIn("PARTIAL is never a final status", out["reason"])

    def test_allows_done_with_evidence_or_blocked(self):
        self.assertEqual(self.hook(DONE), {})
        self.assertEqual(self.hook(BLOCKED), {})
        self.assertEqual(self.hook("Summary first.\n\n" + DONE + "\n"), {})

    def test_allows_chat_turn_and_keeps_blocking_partial_on_retry(self):
        self.assertEqual(self.hook("Hello.", with_tool=False), {})
        self.assertEqual(self.hook(PARTIAL, stop_hook_active=True)["decision"], "block")

    def test_blocks_after_tool_result_larger_than_old_tail_window(self):
        path = Path(write_transcript(self.root, True))
        big = {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "x" * 1_000_000}]}}
        path.write_text(path.read_text(encoding="utf-8") + "\n" + json.dumps(big), encoding="utf-8")
        out = run_hook(install.hook_command(sys.executable),
                       {"last_assistant_message": PARTIAL, "transcript_path": str(path)})
        self.assertEqual(out["decision"], "block")

    def test_codex_transcript(self):
        path = self.root / "codex.jsonl"
        rows = [{"type": "event_msg", "payload": {"type": "user_message", "message": "fix"}},
                {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command"}}]
        path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        out = run_hook(install.hook_command(sys.executable),
                       {"last_assistant_message": PARTIAL, "transcript_path": str(path)})
        self.assertEqual(out["decision"], "block")


class InstallerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        (self.home / ".claude").mkdir()
        self.settings = self.home / ".claude" / "settings.json"
        self.original = {"model": "x", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo keep"}]}]}}
        self.settings.write_text(json.dumps(self.original), encoding="utf-8")

    def run_install(self, *args, env=None):
        return subprocess.run([sys.executable, str(ROOT / "hooks" / "install.py"), *(args or ("--home", str(self.home)))],
                              text=True, capture_output=True, encoding="utf-8", env=env)

    def test_default_prints_without_writing(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("would write", result.stdout)
        self.assertIn("skip", result.stdout)
        self.assertEqual(json.loads(self.settings.read_text(encoding="utf-8")), self.original)
        self.assertFalse((self.home / ".codex").exists())

    def test_preview_omits_private_settings_and_matches_owned_commands_exactly(self):
        command = install.hook_command(sys.executable)
        foreign = command + " --foreign"
        self.settings.write_text(json.dumps({"secret": "fixture-private-value", "hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": foreign}]}]}}), encoding="utf-8")
        preview = self.run_install()
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertNotIn("fixture-private-value", preview.stdout + preview.stderr)
        self.assertEqual(self.run_install("--home", str(self.home), "--write").returncode, 0)
        commands = [h["command"] for g in json.loads(self.settings.read_text(encoding="utf-8"))["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(commands, [foreign, command])

    def test_explicit_prior_owned_command_migrates_changed_interpreter_and_quoting(self):
        prior = '& "old-python" -I "' + install.HOOK.as_posix() + '"'
        foreign = prior + " --foreign"
        self.settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": prior}, {"type": "command", "command": foreign}]}]}}), encoding="utf-8")
        args = ("--home", str(self.home), "--write", "--owned-command", prior)
        result = self.run_install(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = [h["command"] for g in json.loads(self.settings.read_text(encoding="utf-8"))["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(commands, [foreign, install.hook_command(sys.executable)])
        before = self.settings.read_bytes()
        self.assertEqual(self.run_install(*args).returncode, 0)
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual(len(list(self.settings.parent.glob("*.bak-agentlocker-*"))), 1)

    def test_write_merges_backs_up_and_is_idempotent(self):
        (self.home / ".codex").mkdir()
        self.assertEqual(self.run_install("--home", str(self.home), "--write").returncode, 0)
        doc = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(doc["model"], "x")
        commands = [h["command"] for g in doc["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(commands[0], "echo keep")
        self.assertEqual(sum("keep_going.py" in c for c in commands), 1)
        self.assertEqual(len(list((self.home / ".claude").glob("settings.json.bak-agentlocker-*"))), 1)
        codex = json.loads((self.home / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(len(codex["hooks"]["Stop"]), 1)
        before = self.settings.read_text(encoding="utf-8")
        self.assertIn("unchanged", self.run_install("--home", str(self.home), "--write").stdout)
        self.assertEqual(self.settings.read_text(encoding="utf-8"), before)

        transcript = write_transcript(self.home, True)
        self.assertEqual(run_hook(commands[-1], {"last_assistant_message": PARTIAL, "transcript_path": transcript})["decision"], "block")
        self.assertEqual(run_hook(commands[-1], {"last_assistant_message": DONE, "transcript_path": transcript}), {})

    def test_reinstall_keeps_other_hooks_in_a_shared_group(self):
        ours = {"type": "command", "command": install.hook_command(sys.executable)}
        shared = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}, ours]}]}}
        self.settings.write_text(json.dumps(shared), encoding="utf-8")
        self.assertEqual(self.run_install("--home", str(self.home), "--write").returncode, 0)
        commands = [h["command"] for g in json.loads(self.settings.read_text(encoding="utf-8"))["hooks"]["Stop"]
                    for h in g["hooks"]]
        self.assertEqual(commands, ["echo mine", ours["command"]])

    def test_reinstall_keeps_a_foreign_hook_with_the_same_filename(self):
        foreign = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python3 other/keep_going.py"}]}]}}
        self.settings.write_text(json.dumps(foreign), encoding="utf-8")
        self.assertEqual(self.run_install("--home", str(self.home), "--write").returncode, 0)
        commands = [h["command"] for g in json.loads(self.settings.read_text(encoding="utf-8"))["hooks"]["Stop"]
                    for h in g["hooks"]]
        self.assertEqual(commands, ["python3 other/keep_going.py", install.hook_command(sys.executable)])

    @unittest.skipIf(sys.platform == "win32", "POSIX file modes")
    def test_quick_reinstalls_keep_every_backup(self):
        original = self.settings.read_text(encoding="utf-8")
        install.install([(self.settings, False)], sys.executable, True)
        self.settings.write_text(original, encoding="utf-8")
        install.install([(self.settings, False)], sys.executable, True)
        backups = list((self.home / ".claude").glob("settings.json.bak-agentlocker-*"))
        self.assertEqual(len(backups), 2)
        self.assertTrue(all(b.read_text(encoding="utf-8") == original for b in backups))

    @unittest.skipIf(sys.platform == "win32", "POSIX file modes")
    def test_write_keeps_file_mode(self):
        self.settings.chmod(0o600)
        self.assertEqual(self.run_install("--home", str(self.home), "--write").returncode, 0)
        self.assertEqual(self.settings.stat().st_mode & 0o777, 0o600)

    def test_default_home_uses_codex_home(self):
        codex = self.home / "custom-codex"
        codex.mkdir()
        env = {**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home), "CODEX_HOME": str(codex)}
        self.assertEqual(self.run_install("--write", env=env).returncode, 0)
        self.assertTrue((codex / "hooks.json").is_file())
        self.assertFalse((self.home / ".codex").exists())

    @unittest.skipIf(sys.platform == "win32", "POSIX shell quoting")
    def test_command_quotes_shell_characters(self):
        command = install.hook_command("/opt/$(touch pwned)/python")
        self.assertTrue(command.startswith("'/opt/$(touch pwned)/python' -I "))

    def test_refuses_symlinked_settings(self):
        target = self.home / "real.json"
        target.write_text("{}", encoding="utf-8")
        self.settings.unlink()
        self.settings.symlink_to(target)
        result = self.run_install("--home", str(self.home), "--write")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.read_text(encoding="utf-8"), "{}")

    def test_write_does_not_follow_a_planted_temp_symlink(self):
        victim = self.home / "victim.txt"
        victim.write_text("keep", encoding="utf-8")
        (self.settings.parent / "settings.json.agentlocker-tmp").symlink_to(victim)
        result = self.run_install("--home", str(self.home), "--write")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(victim.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
