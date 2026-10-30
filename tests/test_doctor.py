import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from checks.validators import digest
from test_hub import HubCase


class DoctorTests(HubCase):
    def setUp(self):
        super().setUp()
        self.import_skill()

    def test_user_commit_identity_does_not_fail_installation_health(self):
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=someone@example.org",
                 "commit", "-qm", "fixture")
        output = self.cli("doctor")
        self.assertEqual(output.count("PASS:"), 6)
        self.assertNotIn("someone@example.org", output)
        self.assertIn("FAIL: commit emails allowlisted", self.cli("check", code=1))

    def test_archive_without_any_git_ancestor_is_healthy(self):
        with tempfile.TemporaryDirectory() as directory:
            import shutil
            target = Path(directory) / "installed catalog"
            shutil.copytree(self.root, target)
            env = {**self.environment, "GIT_CEILING_DIRECTORIES": directory}
            before = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
            result = subprocess.run([os.sys.executable, "hub.py", "doctor"], cwd=target,
                                    env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.count("PASS:"), 6)
            self.assertEqual(before, {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()})

    def test_private_configuration_and_secret_store_are_not_scanned(self):
        external = self.base / "private state"
        external.mkdir()
        self.write("config.json", {"store_path": str(external)})
        private = external / "secrets" / "fixture"
        private.parent.mkdir()
        secret = "sk" + "-proj-" + "a" * 30
        private.write_text(secret)
        output = self.cli("doctor")
        self.assertNotIn(str(external), output)
        self.assertNotIn(secret, output)
        self.assertEqual(private.read_text(), secret)

    def test_doctor_does_not_scan_unrelated_source_or_dependencies(self):
        path = self.root / "remote/node_modules/fixture/metadata.txt"
        path.parent.mkdir(parents=True)
        path.write_text("someone@example.org\n")
        self.cli("doctor")

    def test_changed_import_hash_still_fails(self):
        (self.local / "skills/sample/nested/data.txt").write_text("changed\n")
        self.assertIn("FAIL: manifest content hashes", self.cli("doctor", code=1))

    def test_catalog_private_text_is_reported_without_value(self):
        path = self.local / "skills/sample/nested/data.txt"
        email = "@".join(("fixture", "fixture.example.net"))
        path.write_text(email + "\n")
        manifest = self.manifest()
        manifest["skills"][0]["sha256"] = digest(path.parents[1])
        self.write(".hub-store/catalog/manifest.json", manifest)
        output = self.cli("doctor", code=1)
        self.assertIn("email-address", output)
        self.assertNotIn(email, output)

    def test_catalog_prompt_is_screened_even_though_definition_hash_is_unchanged(self):
        routine = self.base / "incoming routine"
        routine.mkdir()
        value = {"id": "fixture-routine", "description": "Fixture routine", "owner": "agentlocker",
                 "runner": "cron", "schedule": {"type": "interval", "every_seconds": 3600},
                 "timezone": "UTC", "prompt": "prompt.md", "overlap": "skip",
                 "timeout_seconds": 60, "enabled": False}
        (routine / "routine.json").write_text(json.dumps(value))
        (routine / "prompt.md").write_text("Fixture prompt.\n")
        self.cli("import-routine", routine / "routine.json")
        email = "@".join(("fixture", "fixture.example.net"))
        (self.local / "routines/fixture-routine/prompt.md").write_text(email + "\n")
        self.assertIn("email-address", self.cli("doctor", code=1))

    def test_tracked_private_state_still_fails(self):
        self.git("init", "-q")
        self.write("config.json", {})
        self.git("add", "-f", "config.json")
        self.assertIn("FAIL: installation state", self.cli("doctor", code=1))

    def test_invalid_config_and_symlinked_state_are_refused(self):
        self.write("config.json", [])
        self.assertIn("FAIL: installation state", self.cli("doctor", code=1))
        (self.root / "config.json").unlink()
        preserved = self.base / "preserved.json"
        preserved.write_text("{}")
        try:
            (self.root / "config.json").symlink_to(preserved)
        except OSError as error:
            self.skipTest(str(error))
        self.assertIn("FAIL: installation state", self.cli("doctor", code=1))
        self.assertEqual(preserved.read_text(), "{}")


if __name__ == "__main__":
    unittest.main()
