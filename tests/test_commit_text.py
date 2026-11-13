import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from checks.commit_text import scan_range, text_findings


ROOT = Path(__file__).resolve().parents[1]


def synthetic(*parts):
    return "".join(parts)


class CommitTextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.git("-c", "init.templateDir=", "init", "-q")
        email = (ROOT / "config/commit-email-allowlist.txt").read_text(encoding="utf-8").split()[0]
        self.git("config", "user.email", email)
        self.git("config", "user.name", "fixture")
        self.base = self.commit("Initial fixture")

    def git(self, *args):
        result = subprocess.run(["git", *args], cwd=self.root, capture_output=True,
                                text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, "fixture git command failed")
        return result.stdout.strip()

    def commit(self, text):
        self.git("-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", text)
        return self.git("rev-parse", "HEAD")

    def cli(self, base, head="HEAD", cwd=None):
        return subprocess.run([sys.executable, "-m", "checks.commit_text", "--base", base, "--head", head],
                              cwd=cwd or self.root, env={**os.environ, "PYTHONPATH": str(ROOT)},
                              capture_output=True, text=True, encoding="utf-8")

    def test_merge_subject_private_device_label_is_detected_and_redacted(self):
        label = synthetic("workstation", "-", str(473))
        commit = self.commit("Merge fixture-lane-" + label + "-fix")
        result = self.cli(self.base)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"commit {commit}:1: private-device-label", result.stdout)
        self.assertNotIn(label, result.stdout + result.stderr)

    def test_commit_body_private_text_is_detected_and_redacted(self):
        private = "person" + "@" + "corp" + ".invalidcorp"
        commit = self.commit("Fix fixture\n\nContact " + private)
        errors, count = scan_range(self.root, self.base)
        self.assertEqual(count, 1)
        self.assertIn(f"commit {commit}:3: email-address", errors)
        self.assertNotIn(private, "\n".join(errors))

    def test_prior_history_is_excluded_and_empty_range_is_explicit(self):
        label = synthetic("workstation", "-", str(473))
        floor = self.commit("Old fixture " + label)
        self.commit("Safe followup")
        self.assertEqual(scan_range(self.root, floor), ([], 1))
        result = self.cli("HEAD")
        self.assertEqual(result.returncode, 0)
        self.assertIn("(0 commits)", result.stdout)

    def test_safe_versions_encodings_audit_ids_and_documentation_pass(self):
        text = "Fix V12-11 using UTF-8 and windows-1252\n\nBump package-2.2.0; contact user@example.com"
        self.assertEqual(text_findings(text), [])
        self.commit(text)
        self.assertEqual(scan_range(self.root, self.base), ([], 1))

    def test_tokens_paths_and_private_patterns_are_redacted(self):
        token = "ghp_" + "Ab1Cd2Ef3Gh4" * 3
        home = "/" + "home/fixture-user/private"
        label = "synthetic" + "-confidential"
        folder = self.root / ".hub-store"
        folder.mkdir()
        (folder / "private-patterns.txt").write_text(re.escape(label), encoding="utf-8")
        self.commit("Fix fixture\n\n" + "\n".join((token, home, label)))
        result = self.cli(self.base)
        self.assertEqual(result.returncode, 1)
        for rule in ("github-token", "home-path", "private-pattern"):
            self.assertIn(rule, result.stdout)
        for value in (token, home, label):
            self.assertNotIn(value, result.stdout + result.stderr)

    def test_only_known_automated_coauthor_addresses_are_exempt(self):
        for domain in ("anthropic", "openai"):
            address = synthetic("noreply", "@", domain, ".com")
            self.assertEqual(text_findings("Co-Authored-By: Automation <" + address + ">"), [])
            self.assertIn((1, "email-address"), text_findings("Contact " + address))
        personal = synthetic("person", "@", "corp", ".invalidcorp")
        automated = synthetic("noreply", "@", "anthropic", ".com")
        for text in ("Co-Authored-By: Person <" + personal + ">",
                     "Co-Authored-By: " + personal + " <" + automated + ">",
                     "Signed-off-by: Person <" + personal + ">"):
            self.assertIn((1, "email-address"), text_findings(text))

    def test_allowlisted_coauthor_passes_only_as_a_complete_trailer(self):
        address = (ROOT / 'config/commit-email-allowlist.txt').read_text(encoding='utf-8').split()[0]
        config = self.root / 'config'
        config.mkdir()
        (config / 'commit-email-allowlist.txt').write_text(address + '\n', encoding='utf-8')
        self.commit('Fixture\n\nCo-authored-by: Maintainer <' + address + '>')
        result = self.cli(self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for line in ('Contact ' + address,
                     'Co-authored-by: Maintainer <' + address + '> extra',
                     'Co-authored-by: ' + address + ' <' + synthetic('noreply', '@', 'openai', '.com') + '>'):
            with self.subTest(line=line):
                self.commit('Fixture\n\n' + line)
                result = self.cli('HEAD^')
                self.assertEqual(result.returncode, 1)
                self.assertIn('email-address', result.stdout)

    def test_model_labels_in_automated_trailers_are_not_device_names(self):
        address = synthetic("noreply", "@", "openai", ".com")
        for model in ("GPT-6 Luna", "GPT-6.1-Sol"):
            self.assertEqual(text_findings("Co-Authored-By: " + model + " <" + address + ">"), [])
        private = synthetic("workstation", "-", str(473))
        self.assertIn((1, "private-device-label"), text_findings("Co-Authored-By: " + private + " <" + address + ">"))
        self.assertIn((1, "private-device-label"), text_findings("Connect to " + synthetic("GPT", "-", str(6), ".1-Sol")))
    def test_requested_model_trailer_is_allowed_without_exempting_host_text(self):
        model = synthetic("GPT", "-6.1-Sol")
        address = synthetic("noreply", "@", "openai", ".com")
        self.assertEqual(text_findings("Co-Authored-By: " + model + " <" + address + ">"), [])
        self.assertIn((1, "private-device-label"), text_findings("Contact " + model))
        host = synthetic("workstation", "-", str(473))
        self.assertIn((1, "private-device-label"),
                      text_findings("Co-Authored-By: " + host + " <" + address + ">"))

    def test_openai_model_label_is_exempt_only_in_automated_trailer(self):
        model = "GPT-6.1-Sol"
        address = synthetic("noreply", "@", "openai", ".com")
        trailer = "Co-Authored-By: " + model + " <" + address + ">"
        self.assertEqual(text_findings(trailer), [])
        self.assertIn((1, "private-device-label"), text_findings(model))
        self.assertIn((1, "private-device-label"), text_findings(trailer.replace(model, "private-device-42")))
        self.assertIn((1, "private-device-label"), text_findings(trailer.replace("openai", "anthropic")))
        self.assertIn((1, "private-pattern"), text_findings(trailer, [re.compile(re.escape(model))]))

    def test_required_review_model_trailer_is_not_a_device_label(self):
        address = synthetic("noreply", "@", "openai", ".com")
        model = "GPT-6.1-Sol"
        self.assertEqual(text_findings("Co-Authored-By: " + model + " <" + address + ">"), [])
        self.assertIn((1, "private-device-label"), text_findings("Contact " + model))
        device = synthetic("workstation", "-", str(473))
        self.assertIn((1, "private-device-label"),
                      text_findings("Co-Authored-By: " + device + " <" + address + ">"))

    def test_subdirectory_invocation_uses_repository_local_patterns(self):
        folder = self.root / ".hub-store"
        folder.mkdir()
        label = synthetic("synthetic", "-confidential")
        (folder / "private-patterns.txt").write_text(re.escape(label), encoding="utf-8")
        self.commit("Fix " + label)
        child = self.root / "subdirectory"
        child.mkdir()
        result = self.cli(self.base, cwd=child)
        self.assertEqual(result.returncode, 1)
        self.assertIn("private-pattern", result.stdout)
        self.assertNotIn(label, result.stdout + result.stderr)

    def test_invalid_local_pattern_fails_closed(self):
        folder = self.root / ".hub-store"
        folder.mkdir()
        (folder / "private-patterns.txt").write_text("[", encoding="utf-8")
        result = self.cli(self.base)
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid pattern", result.stdout)

    def test_missing_or_option_like_ref_fails_closed_without_echoing_it(self):
        for value in ("absent-private-ref", "--all", "0" * 40):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    scan_range(self.root, value)
                result = self.cli(value)
                self.assertNotEqual(result.returncode, 0)
                if not value.startswith("--"):
                    self.assertNotIn(value, result.stdout + result.stderr)

    def test_diverged_base_scans_only_head_side_and_includes_merge_text(self):
        self.git("checkout", "-q", "-b", "base-side")
        base = self.commit("Base side")
        self.git("checkout", "-q", "-b", "head-side", self.base)
        self.commit("Head side")
        self.assertEqual(scan_range(self.root, base), ([], 1))
        label = synthetic("workstation", "-", str(473))
        self.git("-c", "commit.gpgsign=false", "merge", "--no-ff", "base-side", "-m", "Merge " + label)
        errors, count = scan_range(self.root, self.base)
        self.assertEqual(count, 3)
        self.assertTrue(any("private-device-label" in error for error in errors))

    def test_unrelated_history_is_rejected(self):
        self.git("checkout", "-q", "--orphan", "unrelated")
        self.commit("Unrelated fixture")
        result = self.cli(self.base)
        self.assertEqual(result.returncode, 1)
        self.assertIn("common full history", result.stdout)

    def test_shallow_history_is_rejected_even_for_an_empty_range(self):
        self.commit("Fixture followup")
        shallow = self.root / "shallow"
        result = subprocess.run(["git", "clone", "-q", "--depth=1", self.root.as_uri(), str(shallow)],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, "fixture shallow clone failed")
        result = self.cli("HEAD", cwd=shallow)
        self.assertEqual(result.returncode, 1)
        self.assertIn("common full history", result.stdout)

    def test_openai_model_names_are_allowed_only_in_automated_trailers(self):
        address = synthetic("noreply", "@", "openai", ".com")
        model = synthetic("gpt", "-6.1", "-sol")
        self.assertEqual(text_findings("Co-Authored-By: " + model + " <" + address + ">"), [])
        self.assertIn((1, "private-device-label"), text_findings("Contact " + model))

    def test_automated_trailer_keeps_private_labels_and_patterns_blocked(self):
        address = synthetic("noreply", "@", "openai", ".com")
        label = synthetic("workstation", "-", str(473))
        self.assertIn((1, "private-device-label"), text_findings("Co-Authored-By: " + label + " <" + address + ">"))
        model = synthetic("gpt", "-6.1", "-sol")
        self.assertIn((1, "private-pattern"), text_findings("Co-Authored-By: " + model + " <" + address + ">", [re.compile(re.escape(model))]))


if __name__ == "__main__":
    unittest.main()
