import ipaddress
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import safety_fixtures
from test_hub import HubCase, REPO
from checks import checker
from checks.scan import findings, scan
from checks.validators import manifest


class CheckTests(HubCase):
    def setUp(self):
        super().setUp()
        self.maintainer = (REPO / "config/commit-email-allowlist.txt").read_text(encoding="utf-8").split()[0]
        self.import_skill()
        self.git("init", "-q")
        self.git("add", ".")

    def test_good_fixture(self):
        output = self.cli("check")
        self.assertEqual(output.count("PASS:"), 7)
        self.assertNotIn("FAIL:", output)

    def commit_as(self, email):
        self.git("-c", "user.name=Fixture", "-c", f"user.email={email}", "commit", "-q", "-m", "fixture", "--allow-empty")

    def test_allowlisted_commit_email_passes(self):
        self.commit_as(self.maintainer)
        self.assertIn("PASS: commit emails allowlisted", self.cli("check"))

    def commit_with_identities(self, author, committer):
        self.git("-c", "user.name=Fixture", "-c", f"user.email={committer}", "commit", "-q",
                 "--author", f"Fixture <{author}>", "-m", "fixture", "--allow-empty")

    def test_web_flow_committer_with_allowlisted_author_passes(self):
        self.commit_with_identities(self.maintainer, "@".join(("noreply", "github.com")))
        self.assertIn("PASS: commit emails allowlisted", self.cli("check"))

    def test_web_flow_committer_with_unlisted_author_fails(self):
        self.commit_with_identities("someone@example.org", "@".join(("noreply", "github.com")))
        output = self.cli("check", code=1)
        self.assertIn("FAIL: commit emails allowlisted", output)
        self.assertIn("someone@example.org", output)

    def test_other_unlisted_committer_with_allowlisted_author_fails(self):
        self.commit_with_identities(self.maintainer, "someone@example.org")
        output = self.cli("check", code=1)
        self.assertIn("FAIL: commit emails allowlisted", output)
        self.assertIn("someone@example.org", output)

    def test_web_flow_committer_must_match_exactly(self):
        self.commit_with_identities(self.maintainer, "@".join(("NOREPLY", "github.com")))
        self.assertIn("FAIL: commit emails allowlisted", self.cli("check", code=1))

    def test_web_flow_email_is_not_allowed_as_author(self):
        self.commit_with_identities("@".join(("noreply", "github.com")), self.maintainer)
        self.assertIn("FAIL: commit emails allowlisted", self.cli("check", code=1))

    def test_main_github_commit_identities_pass(self):
        author = "@".join(("216775752+TayHCode", "users.noreply.github.com"))
        committer = "@".join(("noreply", "github.com"))
        self.commit_with_identities(author, committer)
        self.assertIn("PASS: commit emails allowlisted", self.cli("check"))

    def test_unlisted_commit_email_fails(self):
        self.commit_as(self.maintainer)
        self.commit_as("someone@example.org")
        output = self.cli("check", code=1)
        self.assertIn("FAIL: commit emails allowlisted", output)
        self.assertIn("someone@example.org", output)

    def test_unlisted_email_on_other_branch_is_outside_checkout_history(self):
        self.commit_as(self.maintainer)
        self.git("checkout", "-q", "-b", "side")
        self.commit_as("someone@example.org")
        self.git("tag", "side-export")
        self.git("update-ref", "refs/remotes/fixture/export", "HEAD")
        self.git("checkout", "-q", "-")
        self.assertIn("PASS: commit emails allowlisted", self.cli("check"))

    def test_detached_unlisted_commit_fails(self):
        self.commit_as(self.maintainer)
        self.git("checkout", "-q", "--detach")
        self.commit_as("someone@example.org")
        self.assertIn("FAIL: commit emails allowlisted", self.cli("check", code=1))

    def synthetic_pr_merge(self, email=None):
        self.commit_as(self.maintainer)
        base = self.git("rev-parse", "HEAD").strip()
        self.commit_as(email or self.maintainer)
        if email:
            self.commit_as(self.maintainer)
        head = self.git("rev-parse", "HEAD").strip()
        merge = self.git("-c", "user.name=Fixture", "-c", "user.email=someone@example.org",
                         "commit-tree", "HEAD^{tree}", "-p", base, "-p", head, "-m", "Merge fixture").strip()
        self.git("update-ref", "refs/remotes/origin/pr-merge", merge)
        self.git("checkout", "-q", "--detach", merge)
        self.environment.update(GITHUB_EVENT_NAME="pull_request", AGENTLOCKER_PR_BASE=base,
                                AGENTLOCKER_PR_HEAD=head)

    def test_synthetic_pr_merge_email_is_excluded(self):
        self.synthetic_pr_merge()
        self.assertIn("PASS: commit emails allowlisted", self.cli("check"))

    def test_real_unlisted_pr_commit_still_fails(self):
        self.synthetic_pr_merge("someone@example.org")
        self.assertIn("FAIL: commit emails allowlisted", self.cli("check", code=1))

    def test_local_merge_email_is_not_excluded(self):
        self.synthetic_pr_merge()
        self.environment.pop("GITHUB_EVENT_NAME")
        self.assertIn("FAIL: commit emails allowlisted", self.cli("check", code=1))

    def test_pr_merge_with_wrong_parents_is_not_excluded(self):
        self.synthetic_pr_merge()
        self.git("update-ref", "-d", "refs/remotes/origin/pr-merge")
        self.environment["AGENTLOCKER_PR_HEAD"] = self.environment["AGENTLOCKER_PR_BASE"]
        self.assertIn("FAIL: commit emails allowlisted", self.cli("check", code=1))

    def test_missing_allowlist_fails(self):
        self.commit_as(self.maintainer)
        (self.root / "config/commit-email-allowlist.txt").unlink()
        self.assertIn("FAIL: commit emails allowlisted", self.cli("check", code=1))

    def test_bad_schema(self):
        value = self.manifest()
        value["schema_version"] = 2
        self.write("manifest.json", value)
        self.assertIn("FAIL: manifest schema", self.cli("check", code=1))

    def test_non_mit_license(self):
        (self.root / "LICENSE").write_text("MIT License\nNo permission grant.\n", encoding="utf-8")
        self.assertIn("FAIL: MIT license", self.cli("check", code=1))

    def test_hash_mismatch(self):
        (self.local / "skills/sample/nested/data.txt").write_text("Changed\n", encoding="utf-8")
        self.assertIn("FAIL: manifest content hashes", self.cli("check", code=1))

    def test_missing_path(self):
        (self.local / "skills/sample/SKILL.md").unlink()
        self.assertIn("FAIL: manifest content hashes", self.cli("check", code=1))

    def test_tracked_config(self):
        self.write("config.json", {})
        self.git("add", "-f", "config.json")
        self.assertIn("FAIL: local state", self.cli("check", code=1))

    def test_tracked_store(self):
        self.write(".hub-store/fixture", {})
        self.git("add", "-f", ".hub-store/fixture")
        self.assertIn("FAIL: local state", self.cli("check", code=1))

    def test_missing_ignore_rule(self):
        (self.root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        self.assertIn("FAIL: local state", self.cli("check", code=1))

    def test_injected_forbidden_tracked_content(self):
        fixtures = {
            "github-token": "gh" + "p_" + "a" * 30,
            "openai-token": "sk" + "-proj-" + "a" * 30,
            "aws-key": "AK" + "IA" + "A" * 16,
            "slack-token": "xo" + "xb-" + "a" * 20,
            **safety_fixtures.PROVIDER_KEYS,
            "bearer-value": "Bear" + "er " + "a" * 20,
            "private-key": "-----BEGIN " + "RSA PRIVATE " + "KEY-----",
            "home-path": "/" + "home" + "/fixture/private",
            "tailnet-ip": str(ipaddress.IPv4Address((100 << 24) + (64 << 16) + 258)),
        }
        path = self.root / "injected.txt"
        for rule, content in fixtures.items():
            with self.subTest(rule=rule):
                path.write_text("safe\n" + content + "\n", encoding="utf-8")
                self.git("add", "injected.txt")
                output = self.cli("check", code=1)
                self.assertIn(f"injected.txt:2: {rule}", output)
                self.assertNotIn(content, output)

    def test_check_reports_every_provider_key_line(self):
        keys = [*safety_fixtures.PROVIDER_KEYS.items(), ("stripe-token", safety_fixtures.STRIPE_TEST_KEY)]
        (self.root / "injected.txt").write_text("\n".join(key for _, key in keys) + "\n", encoding="utf-8")
        self.git("add", "injected.txt")
        output = self.cli("check", code=1)
        for number, (rule, key) in enumerate(keys, 1):
            self.assertIn(f"injected.txt:{number}: {rule}", output)
            self.assertNotIn(key, output)

    def test_check_flags_names_from_local_pattern_file(self):
        (self.root / "injected.txt").write_text("safe\nowner is Zebulon Quux\n", encoding="utf-8")
        self.git("add", "injected.txt")
        self.assertEqual(self.cli("check").count("PASS:"), 7)
        store = self.root / ".hub-store"
        store.mkdir(exist_ok=True)
        (store / "private-patterns.txt").write_text("zebulon[ ]quux\n", encoding="utf-8")
        output = self.cli("check", code=1)
        self.assertIn("injected.txt:2: private-pattern", output)
        self.assertNotIn("Zebulon", output)

    def test_local_patterns_skip_only_the_commit_email_allowlist(self):
        store = self.root / ".hub-store"
        store.mkdir(exist_ok=True)
        (store / "private-patterns.txt").write_text(re.escape(self.maintainer) + "\n", encoding="utf-8")
        (self.root / "other.txt").write_text(self.maintainer + "\n", encoding="utf-8")
        self.git("add", ".")
        output = self.cli("check", code=1)
        self.assertIn("other.txt:1: private-pattern", output)
        self.assertNotIn("commit-email-allowlist.txt", output)

    def test_scan_checks_symlink_text_without_following(self):
        target = "/" + "home" + "/fixture/missing"
        (self.root / "linked").symlink_to(target)
        self.git("add", "linked")
        output = self.cli("check", code=1)
        self.assertIn("linked:1: home-path", output)
        self.assertNotIn(target, output)

    def test_all_home_path_styles(self):
        for value in ("/" + "Users" + "/fixture", "C:" + "\\" + "Users" + "\\fixture"):
            self.assertIn((1, "home-path"), findings(value))

    def remove_git(self):
        for directory, _, names in os.walk(self.root / ".git"):
            for name in names:
                os.chmod(Path(directory) / name, stat.S_IWRITE)
        shutil.rmtree(self.root / ".git")

    def test_scanner_falls_back_without_git(self):
        self.git("status", "--short")
        self.remove_git()
        (self.root / "bad.txt").write_text("sk" + "-" + "x" * 30, encoding="utf-8")
        ignored = self.root / ".scratch"
        ignored.mkdir()
        (ignored / "ignored.txt").write_text("sk" + "-" + "x" * 30, encoding="utf-8")
        with patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(self.base)}):
            errors = scan(self.root)
        self.assertEqual(errors, ["bad.txt:1: openai-token"])

    def test_scan_skips_pycache_outside_git(self):
        self.remove_git()
        home_path = "/" + "home" + "/someone"
        cache = self.root / "__pycache__"
        cache.mkdir()
        (cache / "x.pyc").write_bytes(home_path.encode())
        (self.root / "notes.md").write_text(home_path, encoding="utf-8")
        with patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(self.base)}):
            errors = scan(self.root)
        self.assertEqual(errors, ["notes.md:1: home-path"])

    def test_manifest_entry_constraints(self):
        changes = [{"name": "Bad"}, {"path": "../escape"}, {"path": "/absolute"},
                   {"path": "skills\\sample"}, {"sha256": "x" * 64},
                   {"provenance": {"source_id": "src-invalid", "origin": "local-import"}},
                   {"owner": ["one", "two"]}]
        for change in changes:
            with self.subTest(change=change):
                value = self.manifest()
                value["skills"][0].update(change)
                with self.assertRaises(ValueError):
                    manifest(value)

    def test_manifest_duplicate_and_unsorted_lists(self):
        value = self.manifest()
        value["skills"] *= 2
        with self.assertRaises(ValueError):
            manifest(value)
        value = self.manifest()
        entry = dict(value["skills"][0], name="alpha", path="skills/alpha")
        value["skills"].append(entry)
        with self.assertRaises(ValueError):
            manifest(value)


class CommitEmailSkipTests(HubCase):
    def test_skips_outside_git_checkout(self):
        self.assertFalse((self.root / ".git").exists())
        self.assertEqual(checker.check_commit_emails(self.root), "not a git checkout")


class RetainedLicenseAttributionTests(unittest.TestCase):
    def test_exact_upstream_copyright_only(self):
        relative = Path('licenses/npm/commander-14.0.0/LICENSE')
        original = (REPO / relative).read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(original)
            self.assertEqual(scan(root, paths=[relative]), [])
            path.write_bytes(original + b'Changed.\n')
            self.assertEqual(scan(root, paths=[relative]),
                             [relative.as_posix() + ':3: email-address'])
            other = root / 'LICENSE'
            other.write_bytes(original)
            self.assertEqual(scan(root, paths=[Path('LICENSE')]), ['LICENSE:3: email-address'])


class RealRepositoryTests(unittest.TestCase):
    def test_real_repository_check(self):
        self.assertTrue((REPO / "manifest.json").is_file(), "real manifest is required")
        result = subprocess.run([sys.executable, str(REPO / "hub.py"), "check"], cwd=REPO,
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("PASS:"), 7)

    def test_clone_with_autocrlf_preserves_manifest_hashes(self):
        email = (REPO / "config" / "commit-email-allowlist.txt").read_text(encoding="utf-8").split()[0]
        listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=REPO,
                                capture_output=True)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        with tempfile.TemporaryDirectory() as directory:
            source, clone = Path(directory) / "source", Path(directory) / "clone"
            for name in filter(None, listed.stdout.decode().split("\0")):
                if (REPO / name).is_file() or (REPO / name).is_symlink():
                    target = source / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(REPO / name, target, follow_symlinks=False)
            for command in (["init", "-q"], ["add", "."],
                            ["-c", "user.name=fixture", "-c", "user.email=" + email, "commit", "-q", "-m", "fixture"]):
                prepared = subprocess.run(["git", *command], cwd=source, capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            cloned = subprocess.run(["git", "-c", "core.autocrlf=true", "clone", "-q", str(source), str(clone)],
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(cloned.returncode, 0, cloned.stdout + cloned.stderr)
            result = subprocess.run([sys.executable, str(clone / "hub.py"), "check"], cwd=clone,
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS: manifest content hashes", result.stdout)
