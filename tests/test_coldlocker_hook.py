import json
from hashlib import sha256
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))
import install
import publication_guard
import review_guard

EVENTS = ("SessionStart", "SubagentStart", "UserPromptSubmit", "PreToolUse")
BRIEFING = ("Outside the blocks.\n<!-- session:begin -->\nSession policy.\n"
            "<!-- kiss:begin -->\nKeep it small.\n<!-- kiss:end -->\n<!-- session:end -->\nPrivate appendix.")
TARGET = {"providerInstanceId": "codex", "model": "fixture-model", "options": []}


class ColdlockerHookTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.root = self.folder / "private coldlocker"
        self.root.mkdir()
        (self.root / "BRIEFING.md").write_text(BRIEFING, encoding="utf-8")
        (self.root / "target.json").write_text(json.dumps(TARGET), encoding="utf-8")
        (self.root / "cold").write_text(
            "import json, os, sys\nfrom pathlib import Path\n"
            "root = Path(__file__).parent\n"
            "(root / 'request.json').write_text(json.dumps({'args': sys.argv[1:], "
            "'cwd': str(Path.cwd()), 'repo': os.environ.get('AGENTFLEET_REPO_ROOT'), "
            "'models': os.environ.get('FLEET_MODELS_FILE')}))\n"
            "print((root / 'target.json').read_text())\n", encoding="utf-8")
        self.command = [sys.executable, "-I", str(ROOT / "hooks" / "coldlocker_hook.py"), "--root", str(self.root)]

    def hook(self, event=None, raw=None, runs=(), **extra):
        command = self.command + [arg for run in runs for arg in ("--run", run)]
        result = subprocess.run(command, input=raw if raw is not None else json.dumps(
            {"hook_event_name": event, **extra}), capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "HOME": str(self.folder)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1, result.stdout)
        return json.loads(result.stdout)

    def test_session_and_subagent_receive_session_block_and_file_paths(self):
        expected = ("Session policy.\n<!-- kiss:begin -->\nKeep it small.\n<!-- kiss:end -->\n\nFiles: "
                    + ", ".join(str(self.root / name) for name in
                                ("BRIEFING.md", "models.yaml", "skills/README.md", "lessons")))
        for event in ("SessionStart", "SubagentStart"):
            with self.subTest(event=event):
                self.assertEqual(self.hook(event), {"hookSpecificOutput": {
                    "hookEventName": event, "additionalContext": expected}})

    def test_session_falls_back_to_kiss(self):
        (self.root / "BRIEFING.md").write_text("<!-- kiss:begin -->Fallback.<!-- kiss:end -->", encoding="utf-8")
        context = self.hook("SessionStart")["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(context.startswith("Fallback.\n\nFiles: "), context)

    def test_external_briefing_supplies_session_and_subagent_context(self):
        briefing = self.folder / "shared briefing.md"
        briefing.write_text("Outside.<!-- session:begin -->External policy.<!-- session:end -->", encoding="utf-8")
        self.command.extend(("--briefing", str(briefing)))
        expected = (f"External policy.\n\nFiles: {briefing}, {self.root / 'models.yaml'}, "
                    f"{self.root / 'skills/README.md'}, {self.root / 'lessons'}")
        for event in ("SessionStart", "SubagentStart"):
            with self.subTest(event=event):
                self.assertEqual(self.hook(event), {"hookSpecificOutput": {
                    "hookEventName": event, "additionalContext": expected}})

    def test_external_briefing_lists_its_own_models_skills_and_lessons(self):
        locker = self.folder / "locker"
        locker.mkdir()
        briefing = locker / "BRIEFING.md"
        briefing.write_text("<!-- session:begin -->Locker policy.<!-- session:end -->", encoding="utf-8")
        (locker / "models.yaml").write_text("", encoding="utf-8")
        self.command.extend(("--briefing", str(briefing)))
        expected = (f"Locker policy.\n\nFiles: {briefing}, {locker / 'models.yaml'}, "
                    f"{locker / 'skills/README.md'}, {locker / 'lessons'}")
        self.assertEqual(self.hook("SessionStart"), {"hookSpecificOutput": {
            "hookEventName": "SessionStart", "additionalContext": expected}})

    def test_missing_external_briefing_names_the_actual_path(self):
        briefing = self.folder / "missing briefing.md"
        self.command.extend(("--briefing", str(briefing)))
        context = self.hook("SessionStart")["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(context.startswith(f"briefing missing at {briefing}\n\nFiles: {briefing}, "), context)

    def test_missing_briefing_names_the_missing_path(self):
        (self.root / "BRIEFING.md").unlink()
        context = self.hook("SessionStart")["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(context.startswith(f"briefing missing at {self.root / 'BRIEFING.md'}\n\nFiles: "), context)

    def test_prompt_receives_kiss_and_completion_reminder(self):
        output = self.hook("UserPromptSubmit")["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "UserPromptSubmit")
        self.assertEqual(output["additionalContext"],
                         "Keep it small.\n\nState the goal and done condition before acting. End tool work with "
                         "Goal:/Done condition:/Evidence:/Remaining:/Status: DONE or BLOCKED. "
                         "DONE requires Remaining: none.")

    def test_stop_and_other_events_do_nothing(self):
        for event in ("Stop", "SubagentStop", "PostToolUse", None):
            with self.subTest(event=event):
                self.assertEqual(self.hook(event), {})

    def child(self, name, output):
        script = self.folder / f"{name}.py"
        script.write_text("import json, sys\nfrom pathlib import Path\n"
                          f"Path({str(self.folder / (name + '.seen'))!r}).write_text(sys.stdin.read())\n"
                          f"print({output!r})\n", encoding="utf-8")
        return shlex.join([sys.executable, str(script)])

    def push_repo(self):
        repo = self.folder / "repo"
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(self.folder / "destination")],
                       check=True, capture_output=True)
        return {"tool_name": "exec_command", "cwd": str(repo), "tool_input": {"cmd": "git push origin main"}}

    def test_chat_prefix_silences_every_hook_for_that_session_until_work(self):
        recall = self.child("recall", json.dumps({"hookSpecificOutput": {"additionalContext": "Recalled."}}))
        runs = [f"UserPromptSubmit,PreToolUse={recall}"]
        push = self.push_repo()
        notice = self.hook("UserPromptSubmit", runs=runs, session_id="s1", prompt="  Chat: just talking")
        self.assertIn("Chat mode is on", notice["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("Recalled.", notice["hookSpecificOutput"]["additionalContext"])
        self.assertTrue((self.folder / ".coldlocker" / "chat" / "s1").is_file())
        for event, extra in (("UserPromptSubmit", {"prompt": "hello"}), ("SessionStart", {}),
                             ("SubagentStart", {}), ("PreToolUse", push)):
            with self.subTest(event=event):
                self.assertEqual(self.hook(event, runs=runs, session_id="s1", **extra), {})
        self.assertFalse((self.folder / "recall.seen").exists())
        other = self.hook("PreToolUse", runs=runs, session_id="s2", **push)
        self.assertEqual(other["hookSpecificOutput"]["permissionDecision"], "deny")
        resumed = self.hook("UserPromptSubmit", runs=runs, session_id="s1", prompt="work: ship it")
        self.assertTrue(resumed["hookSpecificOutput"]["additionalContext"].startswith("Chat mode is off."))
        self.assertIn("Keep it small.", resumed["hookSpecificOutput"]["additionalContext"])
        self.assertIn("Recalled.", resumed["hookSpecificOutput"]["additionalContext"])
        again = self.hook("PreToolUse", runs=runs, session_id="s1", **push)
        self.assertEqual(again["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_chat_prefix_needs_a_safe_session_id(self):
        for session in (None, "../escape", ""):
            with self.subTest(session=session):
                output = self.hook("UserPromptSubmit", session_id=session, prompt="chat: hi")
                self.assertNotIn("Chat mode", output["hookSpecificOutput"]["additionalContext"])
        self.assertFalse((self.folder / ".coldlocker").exists())

    def test_run_chains_commands_only_on_their_events_and_merges_context(self):
        vault = self.child("vault", "Search the vault.")
        recall = self.child("recall", json.dumps({"hookSpecificOutput": {"additionalContext": "Recalled."}}))
        broken = shlex.join([sys.executable, "-c", "import sys; sys.exit(3)"])
        runs = [f"SessionStart,UserPromptSubmit={vault}", f"UserPromptSubmit={recall}", f"UserPromptSubmit={broken}"]
        output = self.hook("UserPromptSubmit", runs=runs, session_id="s1", prompt="hello")
        self.assertEqual(output["hookSpecificOutput"]["additionalContext"].split("\n\n"), [
            "Keep it small.", "State the goal and done condition before acting. End tool work with "
            "Goal:/Done condition:/Evidence:/Remaining:/Status: DONE or BLOCKED. DONE requires Remaining: none.",
            "Search the vault.", "Recalled."])
        self.assertEqual(json.loads((self.folder / "vault.seen").read_text())["prompt"], "hello")
        (self.folder / "recall.seen").unlink()
        self.hook("SessionStart", runs=runs, session_id="s1")
        self.assertFalse((self.folder / "recall.seen").exists())

    def test_chained_deny_wins_and_chained_context_does_not_override_guard_deny(self):
        deny = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": "child says no"}}
        child = self.child("guard", json.dumps(deny))
        self.assertEqual(self.hook("PreToolUse", runs=[f"PreToolUse={child}"], tool_name="Read",
                                   tool_input={"file_path": "x"}), deny)
        talker = self.child("talker", json.dumps({"hookSpecificOutput": {"additionalContext": "noise"}}))
        output = self.hook("PreToolUse", runs=[f"PreToolUse={talker}"], **self.push_repo())
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertNotEqual(output["hookSpecificOutput"]["permissionDecisionReason"], "child says no")

    def test_guard_deny_returns_without_running_chained_commands(self):
        child = self.child("chained", "{}")
        output = self.hook("PreToolUse", runs=[f"PreToolUse={child}"], **self.push_repo())
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertFalse((self.folder / "chained.seen").exists())

    def test_chained_commands_share_one_time_budget(self):
        import coldlocker_hook
        slow = shlex.join([sys.executable, "-c", "import time; time.sleep(5)"])
        runs = [(frozenset({"UserPromptSubmit"}), slow)] * 3
        with patch.object(coldlocker_hook, "CHAIN_BUDGET", 1), patch.dict(os.environ, {"HOME": str(self.folder)}):
            started = time.monotonic()
            output = coldlocker_hook.dispatch({"hook_event_name": "UserPromptSubmit", "prompt": "hi"}, self.root, runs)
        self.assertLess(time.monotonic() - started, 3)
        self.assertIn("Keep it small.", output["hookSpecificOutput"]["additionalContext"])

    @unittest.skipIf(os.name == "nt", "POSIX shell fixture")
    def test_timed_out_chained_command_leaves_no_running_child(self):
        import coldlocker_hook
        marker = self.folder / "orphan.ran"
        late = shlex.join([sys.executable, "-c", f"import time; time.sleep(2); open({str(marker)!r}, 'w')"])
        runs = [(frozenset({"UserPromptSubmit"}), f"true; {late}")]
        with patch.object(coldlocker_hook, "CHAIN_BUDGET", 0.5), patch.dict(os.environ, {"HOME": str(self.folder)}):
            coldlocker_hook.dispatch({"hook_event_name": "UserPromptSubmit", "prompt": "hi"}, self.root, runs)
        time.sleep(3)
        self.assertFalse(marker.exists())

    def test_timed_out_pretool_chained_command_denies(self):
        import coldlocker_hook
        slow = shlex.join([sys.executable, "-c", "import time; time.sleep(5)"])
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {"file_path": "x"}}
        with patch.object(coldlocker_hook, "CHAIN_BUDGET", 0.5), patch.dict(os.environ, {"HOME": str(self.folder)}):
            output = coldlocker_hook.dispatch(payload, self.root, [(frozenset({"PreToolUse"}), slow)])
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_non_object_json_stdout_is_appended_as_plain_text(self):
        output = self.hook("UserPromptSubmit", runs=["UserPromptSubmit=echo 123"], session_id="s1", prompt="hi")
        self.assertTrue(output["hookSpecificOutput"]["additionalContext"].endswith("\n\n123"))

    def test_one_chained_command_may_use_the_whole_budget(self):
        pause = shlex.join([sys.executable, "-c", "import time; time.sleep(10.5)"])
        late = f"{pause}; {self.child('late', 'Late context.')}"
        output = self.hook("UserPromptSubmit", runs=[f"UserPromptSubmit={late}"], session_id="s1", prompt="hi")
        self.assertTrue(output["hookSpecificOutput"]["additionalContext"].endswith("Late context."))

    def test_pretool_chained_command_skipped_for_budget_denies(self):
        import coldlocker_hook
        runs = [(frozenset({"PreToolUse"}), "true")]
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {"file_path": "x"}}
        with patch.object(coldlocker_hook, "CHAIN_BUDGET", 0), patch.dict(os.environ, {"HOME": str(self.folder)}):
            output = coldlocker_hook.dispatch(payload, self.root, runs)
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_malformed_input_prints_empty_object_and_exits_zero(self):
        for raw in ("{", "", "null", "[]", '"text"'):
            with self.subTest(raw=raw):
                self.assertEqual(self.hook(raw=raw), {})

    def test_malformed_pretool_payload_is_denied(self):
        output = self.hook(raw='{"tool_name": 7, "hook_event_name": "PreToolUse"}')
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_push_to_unverified_remote_is_denied(self):
        repo = self.folder / "repo"
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(self.folder / "destination")],
                       check=True, capture_output=True)
        output = self.hook("PreToolUse", tool_name="exec_command", cwd=str(repo),
                           tool_input={"cmd": "git push origin main"})
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_dispatch_without_role_is_denied(self):
        output = self.hook("PreToolUse", tool_name="mcp__t3__delegate_task",
                           tool_input={"task": "Do the work", "target": TARGET})
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("coldlocker-role", output["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertFalse((self.root / "request.json").exists())

    def test_dispatch_resolves_against_supplied_root_and_passes_metadata(self):
        output = self.hook("PreToolUse", tool_name="delegate_task", cwd=str(self.folder), tool_input={
            "task": "coldlocker-role: reviewer\ncoldlocker-reason: check auth\ncoldlocker-audit-kind: security\n"
                    "coldlocker-builder-model: fixture-builder\ncoldlocker-builder-model: another-builder",
            "target": TARGET})
        self.assertEqual(output, {})
        request = json.loads((self.root / "request.json").read_text())
        self.assertEqual(request, {"args": ["model", "target", "reviewer", "--reason", "check auth",
                                          "--audit-kind", "security", "--builder-model", "fixture-builder",
                                          "--builder-model", "another-builder"],
                                   "cwd": str(self.folder), "repo": str(self.folder),
                                   "models": str(self.root / "models.yaml")})

    def test_dispatch_routes_with_models_beside_the_briefing(self):
        locker = self.folder / "locker"
        locker.mkdir()
        (locker / "models.yaml").write_text("", encoding="utf-8")
        self.command.extend(("--briefing", str(locker / "BRIEFING.md")))
        output = self.hook("PreToolUse", tool_name="delegate_task", cwd=str(self.folder),
                           tool_input={"task": "coldlocker-role: builder", "target": TARGET})
        self.assertEqual(output, {})
        request = json.loads((self.root / "request.json").read_text())
        self.assertEqual(request["models"], str(locker / "models.yaml"))

    def test_dispatch_keeps_root_models_without_a_briefing_registry(self):
        self.command.extend(("--briefing", str(self.folder / "standalone.md")))
        output = self.hook("PreToolUse", tool_name="delegate_task", cwd=str(self.folder),
                           tool_input={"task": "coldlocker-role: builder", "target": TARGET})
        self.assertEqual(output, {})
        request = json.loads((self.root / "request.json").read_text())
        self.assertEqual(request["models"], str(self.root / "models.yaml"))

    def test_dispatch_rejects_mismatched_target_and_failed_routing(self):
        tool = {"task": "coldlocker-role: builder-default", "target": {**TARGET, "options": [{"id": "other"}]}}
        self.assertEqual(self.hook("PreToolUse", tool_name="delegate_task", tool_input=tool)
                         ["hookSpecificOutput"]["permissionDecision"], "deny")
        (self.root / "cold").write_text("raise SystemExit(1)\n", encoding="utf-8")
        tool["target"] = TARGET
        self.assertEqual(self.hook("PreToolUse", tool_name="delegate_task", tool_input=tool)
                         ["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_opus_reservation_uses_supplied_root(self):
        target = {**TARGET, "model": "fixture-opus"}
        (self.root / "target.json").write_text(json.dumps(target), encoding="utf-8")
        ops = self.root / "ops"
        ops.mkdir()
        (ops / "__init__.py").touch()
        receipt = self.root / "reservation.json"
        (ops / "model_usage.py").write_text(
            "import json\nfrom pathlib import Path\n"
            "def repo_key(path):\n    return str(path)\n"
            "def reserve_opus(key):\n    Path(__file__).parents[1].joinpath('reservation.json').write_text(json.dumps(key))\n",
            encoding="utf-8")
        self.assertEqual(self.hook("PreToolUse", tool_name="delegate_task", cwd=str(self.folder), tool_input={
            "task": "coldlocker-role: auditor-high-stakes\ncoldlocker-reason: auth audit\ncoldlocker-audit-kind: security",
            "target": target}), {})
        self.assertEqual(json.loads(receipt.read_text()), str(self.folder))

    def test_missing_reservation_module_denies_dispatch(self):
        target = {**TARGET, "model": "fixture-opus"}
        (self.root / "target.json").write_text(json.dumps(target), encoding="utf-8")
        output = self.hook("PreToolUse", tool_name="delegate_task", tool_input={
            "task": "coldlocker-role: auditor-high-stakes\ncoldlocker-reason: auth audit\ncoldlocker-audit-kind: security",
            "target": target})
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_wrapped_publication_commands_are_denied(self):
        for wrapper in ("nohup", "nice -n 10", "ionice -c 3", "setsid", "timeout -k 1s 5s", "stdbuf -o L",
                        "sudo -u fixture", "time -p", "time -f %E", "time -a", "env -u FIXTURE",
                        "exec -a fixture", "xargs", "parallel", "flock proof.lock", "watch -n 1",
                        "taskset -c 0"):
            for command in ("git push origin main", "gh repo edit --visibility public"):
                with self.subTest(wrapper=wrapper, command=command):
                    output = self.hook("PreToolUse", tool_name="Bash", tool_input={"command": wrapper + " " + command})
                    self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        for command in ("env -S'git push origin main'", "env -iS'git push origin main'",
                        "env -iS 'git push origin main'", "env -S 'git push origin main'",
                        "bash -xc 'git push origin main'", "sh -ec 'gh repo edit --visibility public'",
                        r"find . -exec git push origin main \;"):
            with self.subTest(command=command):
                output = self.hook("PreToolUse", tool_name="Bash", tool_input={"command": command})
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_unsupported_executable_shell_syntax_is_denied(self):
        for command in ("git $'push' origin main", 'git "$operation" origin main',
                        "git {push,pull} origin main", "g?t push origin main",
                        "echo `git push origin main`", 'echo "$(git push origin main)"',
                        "eval 'git push origin main'", "builtin eval 'git push origin main'",
                        "trap 'git push origin main' EXIT", "coproc git push origin main",
                        "bash <<EOF\ngit push origin main\nEOF\n",
                        "bash -s <<'EOF'\ngit push origin main\nEOF\n",
                        "printf '%s' 'git push origin main' | bash"):
            with self.subTest(command=command):
                output = self.hook("PreToolUse", tool_name="Bash", tool_input={"command": command})
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_github_api_writes_and_request_bodies_are_denied(self):
        for command in ("gh api -X PATCH repos/example/repo -f visibility=public",
                        "gh api repos/example/repo --method=PATCH --raw-field private=false",
                        "gh api user/repos -F name=sample", "gh api graphql -f query=mutation",
                        "gh api repos/example/repo --input request.json", "gh api -iXPATCH repos/example/repo",
                        "gh api -XPATCH repos/example/repo", "gh api repos/example/repo --method=$METHOD"):
            with self.subTest(command=command):
                output = self.hook("PreToolUse", tool_name="Bash", tool_input={"command": command})
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_plain_github_api_reads_are_allowed(self):
        for command in ("gh api repos/example/repo", "gh api -X GET repos/example/repo",
                        "gh api --method=HEAD repos/example/repo", "gh api -XGET repos/example/repo",
                        "gh api repos/example/repo --jq .private --paginate"):
            with self.subTest(command=command):
                self.assertEqual(self.hook("PreToolUse", tool_name="Bash", tool_input={"command": command}), {})

    def test_heredoc_data_and_nonpublication_commands_are_allowed(self):
        for command in ("git status --short", "echo 'git push origin main'", "echo git push origin main",
                        "printf '%s' git push", "echo '`git push origin main`'",
                        "bash -n script.sh", "[ -f script.sh ]", "[[ -f script.sh ]]",
                        "cat <<EOF\ngit push origin main\nEOF\n",
                        "python3 - <<'PY'\ntext = \"git $'push' origin main\"\nPY\n"):
            with self.subTest(command=command):
                self.assertEqual(self.hook("PreToolUse", tool_name="Bash", tool_input={"command": command}), {})

    def test_heredoc_substitutions_require_a_quoted_delimiter(self):
        for command in ("cat <<EOF\n$(git push origin main)\nEOF\n",
                        "cat <<EOF\n`git push origin main`\nEOF\n",
                        "cat <<-EOF\n\t$(git push origin main)\n\tEOF\n",
                        'python3 - <<EOF\ntext = "$(git push origin main)"\nEOF\n'):
            with self.subTest(command=command):
                output = self.hook("PreToolUse", tool_name="Bash", tool_input={"command": command})
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        for delimiter in ("'EOF'", '"EOF"', r"\EOF", 'E"OF"'):
            with self.subTest(delimiter=delimiter):
                self.assertEqual(self.hook("PreToolUse", tool_name="Bash", tool_input={
                    "command": f"cat <<{delimiter}\n$(git push origin main)\nEOF\n"}), {})

    def test_routing_lookup_has_fifteen_second_timeout(self):
        with patch.object(review_guard.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, json.dumps(TARGET), "")) as run:
            self.assertEqual(review_guard.dispatch_guard({"tool_name": "delegate_task", "tool_input": {
                "task": "coldlocker-role: builder-default", "target": TARGET}}, self.root, self.root / "models.yaml"), {})
        self.assertEqual(run.call_args.kwargs["timeout"], 15)
        self.assertEqual(run.call_args.args[0][:2], [sys.executable, str(self.root / "cold")])

    def test_routing_lookup_gets_an_absolute_models_path(self):
        with patch.object(review_guard.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, json.dumps(TARGET), "")) as run:
            review_guard.dispatch_guard({"tool_name": "delegate_task", "cwd": str(self.folder), "tool_input": {
                "task": "coldlocker-role: builder-default", "target": TARGET}}, self.root, Path("locker/models.yaml"))
        self.assertEqual(run.call_args.kwargs["env"]["FLEET_MODELS_FILE"],
                         str(Path.cwd() / "locker" / "models.yaml"))

    def test_push_requires_verified_private_visibility(self):
        payload = {"tool_name": "Bash", "cwd": str(self.folder), "tool_input": {"command": "git push origin main"}}
        for visibility in ("PUBLIC", "INTERNAL", "PRIVATE", None):
            with self.subTest(visibility=visibility), patch.object(publication_guard.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0, "https://github.com/example/repo.git\n", ""),
                    subprocess.CompletedProcess([], 0, json.dumps({"visibility": visibility}), ""),
                    subprocess.CompletedProcess([], 0, "no\n", ""),
                    subprocess.CompletedProcess([], 1, "", "unknown revision")]):
                output = publication_guard.publication_guard(payload)
                if visibility == "PRIVATE":
                    self.assertEqual(output, {})
                else:
                    self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_public_push_requires_a_receipt_for_the_source_commit(self):
        repo = self.folder / "repo"
        subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "push.followTags", "false"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "push.recurseSubmodules", "no"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Example", "-c",
                        "user.email=fixture", "commit", "--allow-empty", "-m", "Initial"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/example/repo.git"],
                       check=True, capture_output=True)
        sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "main"], text=True).strip()
        receipts = self.folder / ".local/state/coldlocker/release-approvals/example__repo"
        receipts.mkdir(parents=True)
        run = subprocess.run
        def remote_run(command, **kwargs):
            if command[0] == "gh":
                return subprocess.CompletedProcess(command, 0, '{"visibility":"PUBLIC"}', "")
            return run(command, **kwargs)
        payload = {"tool_name": "Bash", "cwd": str(repo), "tool_input": {"command": "git push origin main"}}
        with patch.object(publication_guard.Path, "home", return_value=self.folder), \
                patch.object(publication_guard.subprocess, "run", side_effect=remote_run):
            for receipt_sha, expected in ((None, "deny"), ("0" * 40, "deny"), (sha, None)):
                with self.subTest(receipt_sha=receipt_sha):
                    if receipt_sha:
                        (receipts / receipt_sha).write_text("release-report.json", encoding="utf-8")
                    output = publication_guard.publication_guard(payload)
                    self.assertEqual(output.get("hookSpecificOutput", {}).get("permissionDecision"), expected)
            payload["tool_input"]["command"] = "git push origin main:refs/heads/release"
            self.assertEqual(publication_guard.publication_guard(payload), {})
            with self.subTest(follow_tags=True):
                subprocess.run(["git", "-C", str(repo), "config", "push.followTags", "true"], check=True)
                self.assertEqual(publication_guard.publication_guard(payload)
                                 ["hookSpecificOutput"]["permissionDecision"], "deny")
            subprocess.run(["git", "-C", str(repo), "config", "push.followTags", "false"], check=True)
            with self.subTest(recursive_push=True):
                subprocess.run(["git", "-C", str(repo), "config", "push.recurseSubmodules", "on-demand"], check=True)
                self.assertEqual(publication_guard.publication_guard(payload)
                                 ["hookSpecificOutput"]["permissionDecision"], "deny")
            subprocess.run(["git", "-C", str(repo), "config", "push.recurseSubmodules", "no"], check=True)
            for command in ("git push --force origin main", "git push origin +main", "git push --all origin",
                            "git push --tags origin", "git push origin :main", "git push origin --delete main",
                            "git push origin main other", "git push origin main && echo done",
                            "gh repo create sample --public", "gh repo edit --visibility public"):
                with self.subTest(command=command):
                    payload["tool_input"]["command"] = command
                    self.assertEqual(publication_guard.publication_guard(payload)
                                     ["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_private_parent_cannot_push_submodules_through_effective_config(self):
        repo = self.folder / "recursive-repo"
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/example/repo.git"],
                       check=True)
        config = self.folder / "global.gitconfig"
        run = subprocess.run
        def remote_run(command, **kwargs):
            if command[0] == "gh":
                return subprocess.CompletedProcess(command, 0, '{"visibility":"PRIVATE"}', "")
            return run(command, **kwargs)
        payload = {"tool_name": "Bash", "cwd": str(repo), "tool_input": {"command": "git push origin main"}}
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1"}), \
                patch.object(publication_guard.subprocess, "run", side_effect=remote_run):
            for setting in ("[push]\nrecurseSubmodules = on-demand\n", "[push]\nrecurseSubmodules = only\n",
                            "[submodule]\nrecurse = true\n"):
                with self.subTest(setting=setting):
                    config.write_text(setting, encoding="utf-8")
                    self.assertEqual(publication_guard.publication_guard(payload)
                                     ["hookSpecificOutput"]["permissionDecision"], "deny")
                    subprocess.run(["git", "-C", str(repo), "config", "push.recurseSubmodules", "no"], check=True)
                    self.assertEqual(publication_guard.publication_guard(payload), {})
                    subprocess.run(["git", "-C", str(repo), "config", "--unset", "push.recurseSubmodules"], check=True)
            config.write_text("[submodule]\nrecurse = false\n", encoding="utf-8")
            self.assertEqual(publication_guard.publication_guard(payload), {})

    def test_public_repo_creation_and_visibility_edits_are_denied(self):
        for command in ("gh repo create sample --public", "gh repo edit --public", "gh repo edit --visibility public",
                        "git push", "git -C elsewhere push origin main", "git push origin main && echo done"):
            with self.subTest(command=command):
                output = self.hook("PreToolUse", tool_name="Bash", tool_input={"command": command})
                self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")


class ColdlockerInstallerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.hook_root = self.home / "private coldlocker"
        self.hook_root.mkdir()
        (self.hook_root / "BRIEFING.md").write_text(BRIEFING, encoding="utf-8")
        (self.hook_root / "cold").write_text("print('{}')\n", encoding="utf-8")
        self.paths = [self.home / ".claude" / "settings.json", self.home / ".codex" / "hooks.json"]
        for path in self.paths:
            path.parent.mkdir()
            path.write_text(json.dumps({"other-setting": "keep", "hooks": {
                "Stop": [{"hooks": [{"type": "command", "command": "echo keep"}]}]}}), encoding="utf-8")

    def cli(self, *args):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "hooks" / "install.py"),
                                 "--home", str(self.home), *args], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def command(self, path, root=None):
        return install.hook_command(sys.executable, path.name == "hooks.json" and os.name == "nt",
                                    install.COLDLOCKER_HOOK, ("--root", (root or self.hook_root).as_posix()))

    def snapshot(self):
        return {p: p.read_bytes() for path in self.paths for p in path.parent.iterdir() if p.is_file()}

    def test_hook_run_is_per_client_and_installed_chain_executes(self):
        self.cli("--hook-root", str(self.hook_root), "--write",
                 "--hook-run", "claude:UserPromptSubmit=echo claude-only",
                 "--hook-run", "codex:UserPromptSubmit=echo codex-only")
        for path, mine, theirs in zip(self.paths, ("claude-only", "codex-only"), ("codex-only", "claude-only")):
            command = json.loads(path.read_text())["hooks"]["UserPromptSubmit"][-1]["hooks"][0]["command"]
            self.assertIn(mine, command)
            self.assertNotIn(theirs, command)
            if os.name != "nt":
                result = subprocess.run(command, shell=True, capture_output=True, text=True, encoding="utf-8",
                                        input=json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "hi"}))
                self.assertTrue(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"].endswith(mine))

    def test_hook_run_moves_a_separately_installed_command_into_the_chain(self):
        self.cli("--recall-command", "echo recalled", "--write")
        self.cli("--hook-root", str(self.hook_root), "--write",
                 "--hook-run", "claude:UserPromptSubmit,PreToolUse=echo recalled")
        hooks = json.loads(self.paths[0].read_text())["hooks"]
        commands = [h["command"] for groups in hooks.values() for g in groups for h in g["hooks"]]
        self.assertNotIn("echo recalled", commands)
        self.assertTrue(any("echo recalled" in c for c in commands))
        codex = json.loads(self.paths[1].read_text())["hooks"]
        self.assertIn("echo recalled", [h["command"] for g in codex["UserPromptSubmit"] for h in g["hooks"]])

    def test_reinstall_without_hook_run_refuses_to_drop_installed_chain(self):
        self.cli("--hook-root", str(self.hook_root), "--write", "--hook-run", "claude:UserPromptSubmit=echo chained")
        before = self.snapshot()
        result = subprocess.run([sys.executable, "-B", str(ROOT / "hooks" / "install.py"), "--home", str(self.home),
                                 "--hook-root", str(self.hook_root), "--write"], capture_output=True, text=True,
                                encoding="utf-8")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_changing_an_installed_chain_needs_explicit_removal_of_the_old_command(self):
        self.cli("--hook-root", str(self.hook_root), "--write", "--hook-run", "claude:UserPromptSubmit=echo one",
                 "--hook-run", "claude:UserPromptSubmit=echo two")
        old = json.loads(self.paths[0].read_text())["hooks"]["UserPromptSubmit"][-1]["hooks"][0]["command"]
        before = self.snapshot()
        result = subprocess.run([sys.executable, "-B", str(ROOT / "hooks" / "install.py"), "--home", str(self.home),
                                 "--hook-root", str(self.hook_root), "--write", "--hook-run",
                                 "claude:UserPromptSubmit=echo one"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.snapshot(), before)
        self.cli("--hook-root", str(self.hook_root), "--write", "--remove-command", old)
        commands = [h["command"] for groups in json.loads(self.paths[0].read_text())["hooks"].values()
                    for g in groups for h in g["hooks"]]
        self.assertNotIn(old, commands)
        self.assertFalse(any("--run" in c for c in commands))
        self.assertTrue(any("coldlocker_hook.py" in c for c in commands))

    def test_interrupted_chain_install_still_needs_explicit_removal(self):
        self.cli("--hook-root", str(self.hook_root), "--write", "--hook-run", "claude:UserPromptSubmit=echo chained")
        receipt = self.paths[0].with_name("settings.json.coldlocker-receipt.json")
        command = json.loads(receipt.read_text())["owned_commands"][0]
        receipt.write_text(json.dumps({"owned_commands": [], "pending_command": command,
                                       "previous_settings_sha256": "0" * 64,
                                       "settings_sha256": sha256(self.paths[0].read_bytes()).hexdigest()}))
        before = self.snapshot()
        result = subprocess.run([sys.executable, "-B", str(ROOT / "hooks" / "install.py"), "--home", str(self.home),
                                 "--hook-root", str(self.hook_root), "--write"], capture_output=True, text=True,
                                encoding="utf-8")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_hook_run_keeps_standalone_registrations_on_events_it_does_not_chain(self):
        self.cli("--recall-command", "echo recalled", "--write")
        self.cli("--hook-root", str(self.hook_root), "--write", "--hook-run", "claude:UserPromptSubmit=echo recalled")
        hooks = json.loads(self.paths[0].read_text())["hooks"]
        standalone = lambda event: [h["command"] for g in hooks[event] for h in g["hooks"]]
        self.assertNotIn("echo recalled", standalone("UserPromptSubmit"))
        self.assertIn("echo recalled", standalone("PreToolUse"))

    def test_hook_run_rejects_bad_specs_without_writes(self):
        before = self.snapshot()
        for spec in ("UserPromptSubmit=echo x", "gemini:UserPromptSubmit=echo x", "claude:echo x", "claude:=echo x",
                     "claude:Stop=echo x", "codex:UserPromptSubmit,Stop=echo x"):
            with self.subTest(spec=spec):
                result = subprocess.run([sys.executable, "-B", str(ROOT / "hooks" / "install.py"), "--home",
                                         str(self.home), "--hook-root", str(self.hook_root), "--hook-run", spec, "--write"],
                                        capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_preview_does_not_write_settings_receipts_or_backups(self):
        before = self.snapshot()
        self.assertIn("would write", self.cli("--hook-root", str(self.hook_root)).stdout)
        self.assertEqual(self.snapshot(), before)

    def test_installs_all_events_and_is_idempotent_in_both_clients(self):
        self.cli("--hook-root", str(self.hook_root), "--recall-command", "recall-tool hook", "--write")
        for path in self.paths:
            doc = json.loads(path.read_text())
            self.assertEqual(doc["other-setting"], "keep")
            self.assertEqual([h["command"] for g in doc["hooks"]["Stop"] for h in g["hooks"]],
                             ["echo keep", install.hook_command(sys.executable,
                               path.name == "hooks.json" and os.name == "nt")])
            for event in EVENTS:
                handlers = [h for g in doc["hooks"][event] for h in g["hooks"]]
                self.assertIn({"type": "command", "command": self.command(path), "timeout": 30}, handlers)
                self.assertEqual(len(handlers), 2 if event in ("UserPromptSubmit", "PreToolUse") else 1)
            self.assertEqual(json.loads(path.with_name(path.name + ".coldlocker-receipt.json").read_text()),
                             {"owned_commands": [self.command(path)],
                              "settings_sha256": sha256(path.read_bytes()).hexdigest()})
        before = self.snapshot()
        self.cli("--hook-root", str(self.hook_root), "--recall-command", "recall-tool hook", "--write")
        self.assertEqual(self.snapshot(), before)

    def test_briefing_replaces_root_only_command_and_is_idempotent_in_both_clients(self):
        self.cli("--hook-root", str(self.hook_root), "--write")
        briefing = self.home / "shared briefing.md"
        briefing.write_text("<!-- session:begin -->External policy.<!-- session:end -->", encoding="utf-8")
        args = ("--hook-root", str(self.hook_root), "--hook-briefing",
                str(self.home / "not-created" / ".." / briefing.name), "--write")
        self.cli(*args)
        for path in self.paths:
            command = install.hook_command(sys.executable, path.name == "hooks.json" and os.name == "nt",
                                           install.COLDLOCKER_HOOK, ("--root", self.hook_root.as_posix(),
                                                               "--briefing", briefing.as_posix()))
            hooks = json.loads(path.read_text())["hooks"]
            for event in EVENTS:
                self.assertEqual(hooks[event], [{"hooks": [{"type": "command", "command": command, "timeout": 30}]}])
            self.assertEqual(json.loads(path.with_name(path.name + ".coldlocker-receipt.json").read_text()),
                             {"owned_commands": [command], "settings_sha256": sha256(path.read_bytes()).hexdigest()})
            if os.name != "nt":
                result = subprocess.run(shlex.split(command), input=json.dumps({"hook_event_name": "SessionStart"}),
                                        capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"].startswith(
                    f"External policy.\n\nFiles: {briefing}, "))
        before = self.snapshot()
        self.cli(*args)
        self.assertEqual(self.snapshot(), before)

    def test_briefing_requires_hook_root(self):
        before = self.snapshot()
        result = subprocess.run([sys.executable, "-B", str(ROOT / "hooks/install.py"),
                                 "--home", str(self.home), "--hook-briefing", str(self.home / "BRIEFING.md"),
                                 "--write"], capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--hook-briefing requires --hook-root", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_plan_passes_briefing_to_powershell_rendering(self):
        briefing = self.home / "owner's briefing.md"
        with patch.object(install, "os", SimpleNamespace(name="nt")):
            changes = install.plan([(self.paths[1], True)], sys.executable,
                                   hook_root=self.hook_root, hook_briefing=briefing)
        doc = json.loads(next(new for path, _, new in changes if path == self.paths[1]))
        command = doc["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertTrue(command.startswith("& '"), command)
        quoted_briefing = self.home.as_posix() + "/owner''s briefing.md"
        self.assertTrue(command.endswith(f" '--root' '{self.hook_root.as_posix()}' '--briefing' "
                                         f"'{quoted_briefing}'"), command)

    @unittest.skipIf(os.name == "nt", "POSIX shell command execution")
    def test_installed_command_executes_with_a_quoted_root(self):
        self.cli("--hook-root", str(self.hook_root), "--write")
        doc = json.loads(self.paths[0].read_text())
        command = doc["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        result = subprocess.run(shlex.split(command), input=json.dumps({"hook_event_name": "SessionStart"}),
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"].startswith("Session policy."))

    def test_root_replacement_preserves_shared_and_unrelated_hooks(self):
        self.cli("--hook-root", str(self.hook_root), "--write")
        before = {}
        for path in self.paths:
            doc = json.loads(path.read_text())
            for event in EVENTS:
                doc["hooks"][event][0]["hooks"].append({"type": "command", "command": "echo shared"})
                doc["hooks"][event].insert(0, {"matcher": "Read", "hooks": [
                    {"type": "command", "command": self.command(path) + " --foreign"},
                    {"type": "prompt", "prompt": "Keep this"}]})
            path.write_text(json.dumps(doc), encoding="utf-8")
            before[path] = path.read_bytes()
        replacement = self.home / "new coldlocker"
        replacement.mkdir()
        self.cli("--hook-root", str(replacement), "--write")
        for path in self.paths:
            doc = json.loads(path.read_text())
            for event in EVENTS:
                self.assertEqual(doc["hooks"][event], [
                    {"matcher": "Read", "hooks": [
                        {"type": "command", "command": self.command(path) + " --foreign"},
                        {"type": "prompt", "prompt": "Keep this"}]},
                    {"hooks": [{"type": "command", "command": "echo shared"}]},
                    {"hooks": [{"type": "command", "command": self.command(path, replacement), "timeout": 30}]}])
            self.assertEqual(json.loads(path.with_name(path.name + ".coldlocker-receipt.json").read_text()),
                             {"owned_commands": [self.command(path, replacement)],
                              "settings_sha256": sha256(path.read_bytes()).hexdigest()})
            self.assertIn(before[path], [p.read_bytes() for p in path.parent.glob(path.name + ".bak-coldlocker-*")])

    def test_coldlocker_retry_preserves_matching_foreign_hooks_after_interrupted_writes(self):
        self.cli("--hook-root", str(self.hook_root), "--write")
        replacement = self.home / "replacement"
        replacement.mkdir()
        write = install.atomic_write
        for path, stage in zip(self.paths, ("settings", "receipt")):
            with self.subTest(client=path.name, stage=stage):
                command = self.command(path, replacement)
                foreign = {"hooks": [{"type": "command", "command": command, "timeout": 30}]}
                doc = json.loads(path.read_text())
                for event in EVENTS:
                    doc["hooks"][event].insert(0, foreign)
                path.write_text(json.dumps(doc), encoding="utf-8")
                receipt = path.with_name(path.name + ".coldlocker-receipt.json")
                original = path.read_bytes()
                def interrupted(target, data, mode=0o600):
                    if target == (path if stage == "settings" else receipt):
                        if stage == "settings" or "pending_command" not in json.loads(data):
                            raise OSError("interrupted write")
                    write(target, data, mode)
                args = ([(path, path.name == "hooks.json" and os.name == "nt")], sys.executable, True)
                with patch.object(install, "atomic_write", side_effect=interrupted):
                    with self.assertRaises(OSError):
                        install.install(*args, hook_root=replacement)
                self.assertEqual(path.read_bytes() == original, stage == "settings")
                install.install(*args, hook_root=replacement)
                hooks = json.loads(path.read_text())["hooks"]
                for event in EVENTS:
                    self.assertEqual(hooks[event], [foreign, foreign])
                before = self.snapshot()
                install.install(*args, hook_root=replacement)
                self.assertEqual(self.snapshot(), before)

    def test_coldlocker_replacement_refuses_ambiguous_ownership_without_writes(self):
        self.cli("--hook-root", str(self.hook_root), "--write")
        replacement = self.home / "replacement"
        replacement.mkdir()
        for path in self.paths:
            with self.subTest(client=path.name):
                doc = json.loads(path.read_text())
                for event in EVENTS:
                    doc["hooks"][event].append({"hooks": [
                        {"type": "command", "command": self.command(path), "timeout": 30},
                        {"type": "command", "command": "echo keep"}]})
                path.write_text(json.dumps(doc), encoding="utf-8")
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, "ambiguous"):
                    install.install([(path, False)], sys.executable, True, hook_root=replacement)
                self.assertEqual(self.snapshot(), before)

    def test_remove_command_is_exact_repeatable_and_preserves_stop_and_unrelated_handlers(self):
        self.cli("--hook-root", str(self.hook_root), "--write")
        originals = {}
        for path in self.paths:
            doc = json.loads(path.read_text())
            for event in (*EVENTS, "Stop", "PostToolUse"):
                doc["hooks"].setdefault(event, []).append({"matcher": "*", "hooks": [
                    {"type": "command", "command": "retired-hook"},
                    {"type": "command", "command": "retired-hook --foreign"},
                    {"type": "command", "command": "another-retired-hook"},
                    {"type": "prompt", "prompt": "Keep this"}]})
            path.write_text(json.dumps(doc), encoding="utf-8")
            originals[path] = path.read_bytes()
        args = ("--remove-command", "retired-hook", "--remove-command", "another-retired-hook")
        before = self.snapshot()
        self.cli(*args)
        self.assertEqual(self.snapshot(), before)
        self.cli(*args, "--write")
        for path in self.paths:
            doc = json.loads(path.read_text())
            self.assertEqual(doc["other-setting"], "keep")
            for event in (*EVENTS, "Stop", "PostToolUse"):
                self.assertEqual(doc["hooks"][event][-1], {"matcher": "*", "hooks": [
                    {"type": "command", "command": "retired-hook --foreign"},
                    {"type": "prompt", "prompt": "Keep this"}]})
            self.assertIn(install.hook_command(sys.executable, path.name == "hooks.json" and os.name == "nt"),
                          [h.get("command") for g in doc["hooks"]["Stop"] for h in g["hooks"]])
            self.assertIn(originals[path], [p.read_bytes() for p in path.parent.glob(path.name + ".bak-coldlocker-*")])
        before = self.snapshot()
        self.cli(*args, "--write")
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
