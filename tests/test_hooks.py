import json
from hashlib import sha256
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

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

    def test_chat_mode_session_is_never_blocked(self):
        command = install.hook_command(sys.executable)
        payload = {"hook_event_name": "Stop", "last_assistant_message": PARTIAL, "session_id": "s1",
                   "transcript_path": write_transcript(self.root, True)}
        (self.root / ".coldlocker" / "chat").mkdir(parents=True)
        (self.root / ".coldlocker" / "chat" / "s1").touch()
        env = {**os.environ, "HOME": str(self.root)}
        allowed = subprocess.run(command, shell=True, input=json.dumps(payload), text=True, capture_output=True, env=env)
        self.assertEqual(json.loads(allowed.stdout), {})
        payload["session_id"] = "s2"
        blocked = subprocess.run(command, shell=True, input=json.dumps(payload), text=True, capture_output=True, env=env)
        self.assertEqual(json.loads(blocked.stdout)["decision"], "block")

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
        self.assertEqual(len(list(self.settings.parent.glob("*.bak-coldlocker-*"))), 1)

    def test_write_merges_backs_up_and_is_idempotent(self):
        (self.home / ".codex").mkdir()
        self.assertEqual(self.run_install("--home", str(self.home), "--write").returncode, 0)
        doc = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(doc["model"], "x")
        commands = [h["command"] for g in doc["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(commands[0], "echo keep")
        self.assertEqual(sum("keep_going.py" in c for c in commands), 1)
        self.assertEqual(len(list((self.home / ".claude").glob("settings.json.bak-coldlocker-*"))), 1)
        codex = json.loads((self.home / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(len(codex["hooks"]["Stop"]), 1)
        before = self.settings.read_text(encoding="utf-8")
        self.assertIn("unchanged", self.run_install("--home", str(self.home), "--write").stdout)
        self.assertEqual(self.settings.read_text(encoding="utf-8"), before)

        transcript = write_transcript(self.home, True)
        self.assertEqual(run_hook(commands[-1], {"last_assistant_message": PARTIAL, "transcript_path": transcript})["decision"], "block")
        self.assertEqual(run_hook(commands[-1], {"last_assistant_message": DONE, "transcript_path": transcript}), {})

    def recall_install(self, command="recall-tool hook"):
        result = self.run_install("--home", str(self.home), "--write", "--recall-command", command)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def recall_targets(self):
        codex = self.home / ".codex"
        codex.mkdir(exist_ok=True)
        return (self.settings, codex / "hooks.json")

    def test_recall_flag_installs_both_events_in_both_clients(self):
        paths = self.recall_targets()
        self.recall_install()
        for path in paths:
            hooks = json.loads(path.read_text(encoding="utf-8"))["hooks"]
            for event in ("UserPromptSubmit", "PreToolUse"):
                self.assertEqual(hooks[event], [{"hooks": [
                    {"type": "command", "command": "recall-tool hook", "timeout": 10}]}])
            self.assertIn(install.hook_command(sys.executable, path.name == "hooks.json" and os.name == "nt"),
                          [h["command"] for g in hooks["Stop"] for h in g["hooks"]])

    def test_recall_reinstall_is_idempotent(self):
        paths = self.recall_targets()
        self.recall_install()
        before = {p: p.read_bytes() for path in paths for p in path.parent.iterdir()}
        result = self.recall_install()
        self.assertEqual({p: p.read_bytes() for path in paths for p in path.parent.iterdir()}, before)
        self.assertEqual(result.stdout.count("unchanged"), 2)

    def test_recall_preserves_preexisting_handlers_with_the_same_command(self):
        paths = self.recall_targets()
        foreign = [{"matcher": "Read", "hooks": [
            {"type": "command", "command": "recall-tool hook", "timeout": 10}]},
            {"hooks": [{"type": "command", "command": "recall-tool hook", "timeout": 30}]},
            {"hooks": [{"type": "command", "command": "recall-tool hook", "timeout": 10}]}]
        for path in paths:
            path.write_text(json.dumps({"hooks": {event: foreign for event in (
                "UserPromptSubmit", "PreToolUse")}}), encoding="utf-8")
        self.recall_install()
        self.recall_install()
        self.recall_install("other-recall hook")
        for path in paths:
            hooks = json.loads(path.read_text(encoding="utf-8"))["hooks"]
            for event in ("UserPromptSubmit", "PreToolUse"):
                self.assertEqual(hooks[event], [*foreign, {"hooks": [
                    {"type": "command", "command": "other-recall hook", "timeout": 10}]}])

    def test_recall_new_command_replaces_previous_in_both_clients(self):
        paths = self.recall_targets()
        self.recall_install()
        self.recall_install("other-recall hook")
        for path in paths:
            hooks = json.loads(path.read_text(encoding="utf-8"))["hooks"]
            for event in ("UserPromptSubmit", "PreToolUse"):
                self.assertEqual(hooks[event], [{"hooks": [
                    {"type": "command", "command": "other-recall hook", "timeout": 10}]}])

    def test_recall_retry_preserves_matching_foreign_hooks_after_interrupted_writes(self):
        write = install.atomic_write
        command = "replacement hook"
        foreign = {"hooks": [{"type": "command", "command": command, "timeout": 10}]}
        for client in ("settings.json", "hooks.json"):
            for previous in (None, "original hook"):
                for stage in ("settings", "receipt"):
                    with self.subTest(client=client, previous=previous, stage=stage):
                        folder = self.home / f"{client}-{previous}-{stage}"
                        folder.mkdir()
                        path = folder / client
                        path.write_text(json.dumps(self.original), encoding="utf-8")
                        if previous:
                            install.install([(path, False)], sys.executable, True, recall_command=previous)
                        doc = json.loads(path.read_text(encoding="utf-8"))
                        for event in ("UserPromptSubmit", "PreToolUse"):
                            doc["hooks"].setdefault(event, []).insert(0, foreign)
                        path.write_text(json.dumps(doc), encoding="utf-8")
                        original_settings = path.read_bytes()
                        receipt = path.with_name(path.name + ".recall-receipt.json")
                        def interrupted(target, data, mode=0o600):
                            if target == (path if stage == "settings" else receipt):
                                if stage == "settings" or "pending_command" not in json.loads(data):
                                    raise OSError("interrupted write")
                            write(target, data, mode)
                        with patch.object(install, "atomic_write", side_effect=interrupted):
                            with self.assertRaises(OSError):
                                install.install([(path, False)], sys.executable, True, recall_command=command)
                        if stage == "settings":
                            self.assertEqual(path.read_bytes(), original_settings)
                        else:
                            self.assertNotEqual(path.read_bytes(), original_settings)
                        install.install([(path, False)], sys.executable, True, recall_command=command)
                        hooks = json.loads(path.read_text(encoding="utf-8"))["hooks"]
                        for event in ("UserPromptSubmit", "PreToolUse"):
                            self.assertEqual(hooks[event], [foreign, foreign])
                        self.assertEqual(json.loads(receipt.read_text(encoding="utf-8")),
                                         {"owned_commands": [command],
                                          "settings_sha256": sha256(path.read_bytes()).hexdigest()})
                        before = {p: p.read_bytes() for p in folder.iterdir()}
                        install.install([(path, False)], sys.executable, True, recall_command=command)
                        self.assertEqual({p: p.read_bytes() for p in folder.iterdir()}, before)

    def test_recall_replacement_refuses_ambiguous_ownership_without_writes(self):
        paths = self.recall_targets()
        self.recall_install()
        for path in paths:
            with self.subTest(client=path.name):
                doc = json.loads(path.read_text(encoding="utf-8"))
                for event in ("UserPromptSubmit", "PreToolUse"):
                    doc["hooks"][event].append({"hooks": [
                        {"type": "command", "command": "recall-tool hook", "timeout": 10},
                        {"type": "command", "command": "echo keep"}]})
                path.write_text(json.dumps(doc), encoding="utf-8")
                before = {p: p.read_bytes() for p in path.parent.iterdir()}
                with self.assertRaisesRegex(ValueError, "ambiguous"):
                    install.install([(path, False)], sys.executable, True, recall_command="replacement hook")
                self.assertEqual({p: p.read_bytes() for p in path.parent.iterdir()}, before)

    def test_recall_preserves_unrelated_hooks_and_settings(self):
        paths = self.recall_targets()
        self.recall_install()
        foreign = {"matcher": "Read", "hooks": [
            {"type": "command", "command": "recall-tool hook --foreign", "timeout": 30},
            {"type": "prompt", "prompt": "Keep this handler"}]}
        for path in paths:
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["other-setting"] = "keep"
            for event in ("UserPromptSubmit", "PreToolUse"):
                doc["hooks"][event].insert(0, foreign)
                doc["hooks"][event][-1]["hooks"].append({"type": "command", "command": "echo shared"})
            doc["hooks"]["PostToolUse"] = [{"hooks": [{"type": "command", "command": "recall-tool hook"}]}]
            path.write_text(json.dumps(doc), encoding="utf-8")
        self.recall_install("other-recall hook")
        for path in paths:
            doc = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(doc["other-setting"], "keep")
            self.assertEqual(doc["hooks"]["PostToolUse"], [{"hooks": [{"type": "command", "command": "recall-tool hook"}]}])
            for event in ("UserPromptSubmit", "PreToolUse"):
                self.assertEqual(doc["hooks"][event], [foreign,
                    {"hooks": [{"type": "command", "command": "echo shared"}]},
                    {"hooks": [{"type": "command", "command": "other-recall hook", "timeout": 10}]}])

    def test_no_recall_flag_leaves_recall_hooks_and_receipts_unchanged(self):
        paths = self.recall_targets()
        self.recall_install()
        before = {p: p.read_bytes() for path in paths for p in path.parent.iterdir()}
        result = self.run_install("--home", str(self.home), "--write")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "".join(f"unchanged {path}\n" for path in paths))
        self.assertEqual({p: p.read_bytes() for path in paths for p in path.parent.iterdir()}, before)

    def test_recall_preview_writes_nothing_and_omits_command(self):
        self.recall_targets()
        before = self.settings.read_bytes()
        result = self.run_install("--home", str(self.home), "--recall-command", "recall-tool hook")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Stop and recall hooks", result.stdout)
        self.assertNotIn("recall-tool", result.stdout + result.stderr)
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.settings.parent.iterdir()), ["settings.json"])
        self.assertEqual(list((self.home / ".codex").iterdir()), [])

    def test_recall_refuses_symlinked_receipt_before_writes(self):
        self.recall_install()
        receipt = self.settings.with_name("settings.json.recall-receipt.json")
        saved = self.home / "saved-receipt.json"
        receipt.rename(saved)
        receipt.symlink_to(saved)
        before = self.settings.read_bytes()
        result = self.run_install("--home", str(self.home), "--write", "--recall-command", "other-recall hook")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual(json.loads(saved.read_text(encoding="utf-8")),
                         {"owned_commands": ["recall-tool hook"], "settings_sha256": sha256(before).hexdigest()})

    def test_recall_replacement_backs_up_previous_client_files(self):
        paths = self.recall_targets()
        self.recall_install()
        before = {path: path.read_bytes() for path in paths}
        self.recall_install("other-recall hook")
        for path in paths:
            backups = [p.read_bytes() for p in path.parent.glob(path.name + ".bak-coldlocker-*")]
            self.assertIn(before[path], backups)

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
        backups = list((self.home / ".claude").glob("settings.json.bak-coldlocker-*"))
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
        (self.settings.parent / "settings.json.coldlocker-tmp").symlink_to(victim)
        result = self.run_install("--home", str(self.home), "--write")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(victim.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
