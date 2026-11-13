import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import unittest

from test_hub import HubCase, PACK_MISSING, REPO

needs_pack = unittest.skipIf(PACK_MISSING, "policy pack not installed")

AT = "@"


class UpdateTests(HubCase):
    def setUp(self):
        super().setUp()
        identity = "Fixture"
        address = "fixture" + AT + "example.org"
        self.environment.update(GIT_AUTHOR_NAME=identity, GIT_AUTHOR_EMAIL=address,
                                GIT_COMMITTER_NAME=identity, GIT_COMMITTER_EMAIL=address,
                                GIT_CONFIG_GLOBAL=str(self.base / "gitconfig"), GIT_CONFIG_NOSYSTEM="1")
        self.remote = self.base / "remote.git"
        self.run_git(self.base, "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.run_git(self.root, "init", "-q", "-b", "main")
        self.run_git(self.root, "add", ".")
        self.run_git(self.root, "commit", "-qm", "base")
        self.run_git(self.root, "remote", "add", "origin", str(self.remote))
        self.run_git(self.root, "push", "-q", "origin", "main")
        self.upstream = self.base / "upstream"
        self.run_git(self.base, "clone", "-q", str(self.remote), str(self.upstream))
        self.old = self.head(self.root)
        guards = self.base / "home/.coldlocker/guards.json"
        guards.parent.mkdir(parents=True)
        guards.write_text(json.dumps({"publication": True, "dispatch": True}) + "\n", encoding="utf-8")
        self.home_before = self.snapshot(self.base / "home")

    def run_git(self, where, *args):
        result = subprocess.run(["git", "-C", str(where), *args], env=self.environment,
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def head(self, where):
        return self.run_git(where, "rev-parse", "HEAD")

    def snapshot(self, folder):
        return {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}

    def worktree_files(self):
        return {path: data for path, data in self.snapshot(self.root).items() if path.parts[0] != ".git"}

    def push_upstream(self, relative, text, message):
        path = self.upstream / relative
        path.write_text(text, encoding="utf-8")
        self.run_git(self.upstream, "add", relative)
        self.run_git(self.upstream, "commit", "-qm", message)
        self.run_git(self.upstream, "push", "-q", "origin", "main")
        return self.head(self.upstream)

    def assert_unchanged(self, output):
        self.assertIn(self.old, output)
        self.assertEqual(self.head(self.root), self.old)
        self.assertEqual(self.run_git(self.root, "status", "--porcelain"), "")
        self.assertEqual(self.snapshot(self.base / "home"), self.home_before)

    def test_happy_path_fast_forwards_and_prints_rollback_sha(self):
        new = self.push_upstream("notes.txt", "news\n", "news")
        output = self.cli("update")
        self.assertEqual(self.head(self.root), new)
        self.assertIn(f"Rollback SHA: {self.old}", output)
        self.assertIn(f"reset --keep {self.old}", output)
        if PACK_MISSING:
            self.assertIn("SKIP: hook self-test: policy pack not installed", output)
        for title in () if PACK_MISSING else ("owner state write is denied", "public push is denied",
                                              "unrouted dispatch is denied", "ordinary command is allowed",
                                              "wrote only the decision log to its home"):
            self.assertIn("PASS: hook self-test: " + title, output)
        self.assertNotIn("FAIL", output)
        self.assertEqual(self.snapshot(self.base / "home"), self.home_before)

    @needs_pack
    def test_successful_update_warns_about_installed_hook_without_lessons(self):
        from hooks.install import hook_command
        self.environment.update(COLDLOCKER_LESSONS="", CODEX_HOME="")
        config = self.base / "home/.claude/settings.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        command = hook_command("python3", hook=Path("/installed/hooks/coldlocker_hook.py"),
                               args=("--root", "/installed"))
        config.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": command}]}]}}))
        before = self.snapshot(self.base / "home")
        new = self.push_upstream("notes.txt", "news\n", "news")
        output = self.cli("update")
        self.assertIn("WARN: claude hook injects no lessons", output)
        self.assertEqual(self.head(self.root), new)
        self.assertEqual(self.snapshot(self.base / "home"), before)

    def test_diverged_clone_is_refused_and_unchanged(self):
        self.push_upstream("notes.txt", "news\n", "news")
        (self.root / "local.txt").write_text("mine\n", encoding="utf-8")
        self.run_git(self.root, "add", "local.txt")
        self.run_git(self.root, "commit", "-qm", "local")
        self.old = self.head(self.root)
        before = self.worktree_files()
        output = self.cli("update", code=1)
        self.assertIn("git merge failed", output)
        self.assert_unchanged(output)
        self.assertEqual(self.worktree_files(), before)

    def test_dirty_tree_and_other_branch_are_refused(self):
        self.push_upstream("notes.txt", "news\n", "news")
        (self.root / "hub.py").write_text((self.root / "hub.py").read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertIn("uncommitted changes", self.cli("update", code=1))
        self.assertEqual(self.head(self.root), self.old)
        self.run_git(self.root, "checkout", "-q", "--", "hub.py")
        self.run_git(self.root, "switch", "-qc", "feature")
        self.assertIn("on feature, not main", self.cli("update", code=1))
        self.assertEqual(self.head(self.root), self.old)

    def test_untracked_file_is_refused_even_when_status_hides_untracked(self):
        self.push_upstream("notes.txt", "news\n", "news")
        self.run_git(self.root, "config", "status.showUntrackedFiles", "no")
        (self.root / "stray.txt").write_text("stray\n", encoding="utf-8")
        self.assertIn("uncommitted changes", self.cli("update", code=1))
        self.assertEqual(self.head(self.root), self.old)

    @unittest.skipIf(os.name == "nt", "POSIX shell quoting")
    def test_printed_rollback_command_runs_from_a_path_with_spaces(self):
        checkout = self.base / "my checkout"
        self.run_git(self.base, "clone", "-q", str(self.remote), str(checkout))
        new = self.push_upstream("notes.txt", "news\n", "news")
        result = subprocess.run([sys.executable, str(checkout / "hub.py"), "update"], cwd=checkout, env=self.environment,
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.head(checkout), new)
        command = result.stdout.split("Roll back with: ", 1)[1].strip()
        subprocess.run(shlex.split(command), env=self.environment, check=True, capture_output=True)
        self.assertEqual(self.head(checkout), self.old)

    def test_failed_doctor_rolls_back_and_prints_rollback_sha(self):
        manifest = json.loads((self.upstream / "manifest.json").read_text(encoding="utf-8"))
        manifest["license"] = "GPL"
        new = self.push_upstream("manifest.json", json.dumps(manifest) + "\n", "break doctor")
        output = self.cli("update", code=1)
        self.assertIn("FAIL: manifest schema", output)
        self.assertIn(f"Update failed checks at {new}; rolled back to {self.old}.", output)
        self.assert_unchanged(output)

    def push_appended(self, relative, extra):
        original = (self.upstream / relative).read_text(encoding="utf-8")
        return self.push_upstream(relative, original + extra, "change " + relative)

    def assert_rolled_back(self, new, failed, passed=()):
        output = self.cli("update", code=1)
        for title in failed:
            self.assertIn("FAIL: hook self-test: " + title, output)
        for title in passed:
            self.assertIn("PASS: hook self-test: " + title, output)
        self.assertIn(f"Update failed checks at {new}; rolled back to {self.old}.", output)
        self.assert_unchanged(output)
        return output

    @needs_pack
    def test_weakened_owner_guard_fails_self_test_and_rolls_back(self):
        new = self.push_appended("hooks/owner_rules.py", "\ntouches_owner_state = lambda payload, origin_ok=False: False\n")
        self.assert_rolled_back(new, ["owner state write is denied"],
                                ["public push is denied", "unrouted dispatch is denied", "ordinary command is allowed"])
        self.assertNotIn("touches_owner_state = lambda", (self.root / "hooks/owner_rules.py").read_text(encoding="utf-8"))

    @needs_pack
    def test_removed_publication_guard_fails_self_test_and_rolls_back(self):
        new = self.push_appended("hooks/publication_guard.py", "\n\ndef publication_guard(payload, yes=False):\n    return {}\n")
        self.assert_rolled_back(new, ["public push is denied"], ["owner state write is denied", "unrouted dispatch is denied"])

    @needs_pack
    def test_removed_dispatch_guard_fails_self_test_and_rolls_back(self):
        new = self.push_appended("hooks/review_guard.py", "\n\ndef dispatch_guard(payload, root, models):\n    return {}\n")
        self.assert_rolled_back(new, ["unrouted dispatch is denied"], ["owner state write is denied", "public push is denied"])

    @needs_pack
    def test_deleted_hook_with_pack_installed_fails_self_test_and_rolls_back(self):
        self.run_git(self.upstream, "rm", "-q", "hooks/coldlocker_hook.py")
        self.run_git(self.upstream, "commit", "-qm", "delete hook")
        self.run_git(self.upstream, "push", "-q", "origin", "main")
        new = self.head(self.upstream)
        self.assert_rolled_back(new, ["owner state write is denied", "public push is denied", "unrouted dispatch is denied"])
        self.assertTrue((self.root / "hooks/coldlocker_hook.py").is_file())

    @needs_pack
    def test_update_that_removes_an_installed_pack_fails_self_test_and_rolls_back(self):
        self.run_git(self.upstream, "rm", "-rq", "policy-pack.json", "hooks")
        self.run_git(self.upstream, "commit", "-qm", "remove pack")
        self.run_git(self.upstream, "push", "-q", "origin", "main")
        new = self.head(self.upstream)
        self.assert_rolled_back(new, ["owner state write is denied", "public push is denied", "unrouted dispatch is denied"])
        self.assertTrue((self.root / "policy-pack.json").is_file())

    @needs_pack
    def test_crashing_hook_fail_closed_deny_does_not_pass_the_probes(self):
        new = self.push_appended("hooks/owner_rules.py", "\n\ndef touches_owner_state(payload):\n    raise RuntimeError('boom')\n")
        self.assert_rolled_back(new, ["owner state write is denied", "public push is denied",
                                      "unrouted dispatch is denied", "ordinary command is allowed"])

    @needs_pack
    def test_hook_that_writes_home_cannot_reach_the_callers_home_and_fails(self):
        new = self.push_appended("hooks/owner_rules.py", "\n__import__('pathlib').Path.home().joinpath('planted.txt').write_text('x')\n")
        self.assert_rolled_back(new, ["wrote only the decision log to its home"])
        self.assertFalse((self.base / "home/planted.txt").exists())

    def test_doctor_that_raises_still_rolls_back(self):
        new = self.push_upstream("hub.py", "import sys\nsys.stdout.buffer.write(b'\\xff\\xfe\\n')\n", "undecodable doctor")
        output = self.cli("update", code=1)
        self.assertIn("FAIL: post-update checks raised UnicodeDecodeError", output)
        self.assertIn(f"Update failed checks at {new}; rolled back to {self.old}.", output)
        self.assert_unchanged(output)


if __name__ == "__main__":
    unittest.main()
