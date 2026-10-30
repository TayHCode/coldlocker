import hashlib
import ipaddress
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from checks.validators import digest
from connect import python_command


class HubCase(unittest.TestCase):
    def setUp(self):
        scratch = REPO / ".scratch" / "test-root"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "hub"
        self.root.mkdir()
        self.local = self.root / ".hub-store/catalog"
        for filename in ("hub.py", "consistency_check.py", "connect.py", "remote_connect.py", "remote_settings.py", "scanner.py", "importers.py", "composio_connector.py", "lifecycle.py", "routine_enable.py", "routine_runtime.py", "routine_schtasks.py", "gateway_render.py", "mcp_endpoint.py", "calllog.py", "store.py", "safety.py", "portable.py", "client_paths.py", "remote_publisher.py"):
            shutil.copyfile(REPO / filename, self.root / filename)
        shutil.copytree(REPO / "checks", self.root / "checks", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(REPO / "config", self.root / "config")
        shutil.copyfile(REPO / "LICENSE", self.root / "LICENSE")
        self.write("manifest.json", {"schema_version": 1, "name": "agentlocker", "license": "MIT",
                                     "skills": [], "rules": [], "mcp_servers": [], "routines": []})
        (self.root / ".gitignore").write_text("config.json\n.hub-store/\n__pycache__/\n", encoding="utf-8")
        self.environment = {**os.environ, "HOME": str(self.base / "home"), "USERPROFILE": str(self.base / "home"), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
        self.source = self.base / "sample"
        self.source.mkdir()
        (self.source / "SKILL.md").write_text("---\nname: sample\ndescription: A sample skill\n---\nHello\n", encoding="utf-8")
        (self.source / "nested").mkdir()
        (self.source / "nested" / "data.txt").write_text("payload\n", encoding="utf-8")

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        return path

    def cli(self, *args, code=0):
        result = subprocess.run([sys.executable, str(self.root / "hub.py"), *map(str, args)],
                                cwd=self.root, env=self.environment, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def manifest(self):
        path = self.local / "manifest.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"skills": [], "rules": [], "mcp_servers": [], "routines": []}

    def import_skill(self):
        self.cli("import-skill", self.source)

    def git(self, *args):
        result = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout


class SkillTests(HubCase):
    def test_import_whole_package_and_provenance(self):
        self.cli("import-skill", self.source, "--owner", "maintainer", "--origin", "upstream")
        entry = self.manifest()["skills"][0]
        self.assertEqual(entry["owner"], "maintainer")
        self.assertEqual(entry["provenance"], {"source_id": "src-" + entry["sha256"][:12], "origin": "upstream"})
        self.assertEqual((self.local / "skills/sample/nested/data.txt").read_text(encoding="utf-8"), "payload\n")
        self.assertEqual(entry["sha256"], digest(self.source))

    def test_failed_replacement_keeps_existing_skill(self):
        import hub
        from unittest.mock import patch
        self.import_skill()
        installed = self.local / "skills/sample"
        (self.source / "SKILL.md").write_text("---\nname: sample\ndescription: A sample skill\n---\nChanged\n", encoding="utf-8")
        original = Path.rename

        def failing(path, target):
            if path.name == "package":
                raise OSError("simulated crash")
            return original(path, target)

        with patch.object(Path, "rename", failing):
            with self.assertRaises(OSError):
                hub.copy_skill(self.source, installed)
        self.assertIn("Hello", (installed / "SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual(sorted(p.name for p in installed.parent.iterdir()), ["sample"])

    def test_idempotent_reimport_from_destination(self):
        self.import_skill()
        before = (self.local / "manifest.json").read_bytes()
        self.cli("import-skill", self.local / "skills/sample")
        self.assertEqual(before, (self.local / "manifest.json").read_bytes())

    def test_update_replaces_whole_package(self):
        self.import_skill()
        (self.source / "nested/data.txt").unlink()
        self.import_skill()
        self.assertFalse((self.local / "skills/sample/nested/data.txt").exists())
        self.assertEqual(len(self.manifest()["skills"]), 1)

    def test_bad_name(self):
        self.cli("import-skill", self.source, "--name", "Bad_Name", code=1)
        self.assertFalse((self.local / "skills").exists())

    def test_mismatched_frontmatter(self):
        self.cli("import-skill", self.source, "--name", "other", code=1)
        self.assertEqual(self.manifest()["skills"], [])

    def test_missing_description(self):
        (self.source / "SKILL.md").write_text("---\nname: sample\n---\n", encoding="utf-8")
        self.cli("import-skill", self.source, code=1)

    def test_missing_or_unclosed_frontmatter(self):
        for content in ("No frontmatter", "---\nname: sample\n"):
            with self.subTest(content=content):
                (self.source / "SKILL.md").write_text(content, encoding="utf-8")
                self.cli("import-skill", self.source, code=1)

    def test_symlink_rejected(self):
        (self.source / "link").symlink_to("missing")
        self.cli("import-skill", self.source, code=1)
        self.assertEqual(self.manifest()["skills"], [])

    def test_symlinked_destination_parent_rejected(self):
        outside = self.base / "outside"
        outside.mkdir()
        self.local.mkdir(parents=True)
        (self.local / "skills").symlink_to(outside, target_is_directory=True)
        self.cli("import-skill", self.source, code=1)
        self.assertEqual(list(outside.iterdir()), [])

    def test_rule_import_and_sorted_manifest(self):
        source = self.base / "policy.md"
        source.write_text("Shared policy\n", encoding="utf-8")
        self.cli("import-rule", source, "--name", "z-rule")
        self.cli("import-rule", source, "--name", "a-rule")
        self.assertEqual([entry["name"] for entry in self.manifest()["rules"]], ["a-rule", "z-rule"])
        self.assertEqual((self.local / "rules/a-rule.md").read_bytes(), source.read_bytes())

    def test_rule_import_normalizes_uppercase_filename_and_explains_invalid_name(self):
        for filename, expected in (("CLAUDE.md", "claude"), ("AGENTS.md", "agents")):
            with self.subTest(filename=filename):
                source = self.base / filename
                source.write_text("Shared policy\n", encoding="utf-8")
                self.cli("import-rule", source)
                self.assertTrue((self.local / "rules" / f"{expected}.md").exists())
        source = self.base / "Bad_Name.md"
        source.write_text("Shared policy\n", encoding="utf-8")
        output = self.cli("import-rule", source, code=1)
        self.assertIn("bad_name", output)
        self.assertIn("--name", output)

    def test_import_rule_refuses_tailnet_ip_and_foreign_home_path(self):
        tailnet = str(ipaddress.IPv4Address((100 << 24) + (64 << 16) + 258))
        foreign = "/" + "home" + "/alice/private/notes.txt"
        source = self.base / "ops.md"
        source.write_text(f"SSH to {tailnet} and read {foreign}\n", encoding="utf-8")
        output = self.cli("import-rule", source, code=1)
        self.assertIn("REFUSED: definition contains", output)
        self.assertIn("tailnet-ip at line 1 (ops.md)", output)
        self.assertIn("home-path at line 1 (ops.md)", output)
        self.assertFalse((self.local / "rules").exists())
        self.assertEqual(self.manifest()["rules"], [])

    def test_import_rule_rewrites_own_home_path(self):
        own = self.base / "home" / "notes" / "todo.md"
        source = self.base / "ops.md"
        source.write_text(f"Read {own}\n", encoding="utf-8")
        self.cli("import-rule", source)
        self.assertEqual((self.local / "rules/ops.md").read_text(encoding="utf-8"), f"Read {Path('~', 'notes', 'todo.md')}\n")
        self.assertEqual(self.manifest()["rules"][0]["sha256"], digest(self.local / "rules/ops.md"))

    def test_tree_hash_contract(self):
        expected = "".join(f"{name}\0{hashlib.sha256((self.source / name).read_bytes()).hexdigest()}\n"
                           for name in ("SKILL.md", "nested/data.txt"))
        self.assertEqual(digest(self.source), hashlib.sha256(expected.encode()).hexdigest())


class LinkTests(HubCase):
    def setUp(self):
        super().setUp()
        self.import_skill()
        self.target = self.base / "target"

    def test_relative_links_resolve_and_are_idempotent(self):
        self.cli("link", "--target", self.target)
        for agent in (".claude", ".agents"):
            link = self.target / agent / "skills/sample"
            self.assertTrue(link.is_symlink())
            self.assertFalse(os.path.isabs(os.readlink(link)))
            self.assertEqual(os.readlink(link), os.path.relpath(self.local / "skills/sample", link.parent))
            self.assertEqual(link.resolve(), self.local / "skills/sample")
        self.assertIn("Nothing to do", self.cli("link", "--target", self.target))

    def test_conflict_makes_no_partial_changes(self):
        conflict = self.target / ".agents/skills/sample"
        conflict.mkdir(parents=True)
        self.cli("link", "--target", self.target, code=1)
        self.assertFalse((self.target / ".claude").exists())
        self.assertTrue(conflict.is_dir())

    def test_wrong_link_conflict(self):
        conflict = self.target / ".agents/skills/sample"
        conflict.parent.mkdir(parents=True)
        conflict.symlink_to("missing")
        self.cli("link", "--target", self.target, code=1)
        self.assertEqual(os.readlink(conflict), "missing")
        self.assertFalse((self.target / ".claude").exists())

    def test_dry_run_does_not_create_target(self):
        output = self.cli("link", "--target", self.target, "--dry-run")
        self.assertEqual(output.count("Would link"), 2)
        self.assertFalse(self.target.exists())

    def test_home_guard(self):
        self.cli("link", "--target", self.environment["HOME"], code=1)
        self.assertFalse(Path(self.environment["HOME"]).exists())

    def test_home_config_guard(self):
        home = Path(self.environment["HOME"])
        for config in (".claude", ".codex", ".agents", ".cursor"):
            with self.subTest(config=config):
                protected = home / config
                protected.mkdir(parents=True)
                self.cli("link", "--target", protected / "nested", code=1)
                self.assertEqual(list(protected.iterdir()), [])

    def test_home_dot_directories_refused_with_reason(self):
        home = Path(self.environment["HOME"])
        for relative in (".config/opencode", ".grok", ".claude", ".codex/skills"):
            with self.subTest(target=relative):
                output = self.cli("link", "--target", home / relative, "--dry-run", code=1)
                self.assertIn("inside your home directory", output)
                self.assertNotIn("Would link", output)

    def test_home_project_without_git_refused(self):
        home = Path(self.environment["HOME"])
        (home / "projects/plain").mkdir(parents=True)
        output = self.cli("link", "--target", home / "projects/plain", "--dry-run", code=1)
        self.assertIn("inside your home directory", output)

    def test_home_git_project_allowed(self):
        home = Path(self.environment["HOME"])
        project = home / "projects/real"
        (project / ".git").mkdir(parents=True)
        self.assertEqual(self.cli("link", "--target", project, "--dry-run").count("Would link"), 2)
        self.cli("link", "--target", project / "sub", "--dry-run")

    def test_home_config_with_git_still_refused(self):
        home = Path(self.environment["HOME"])
        (home / ".claude/.git").mkdir(parents=True)
        self.cli("link", "--target", home / ".claude", code=1)

    def test_symlink_to_home_config_guard(self):
        protected = Path(self.environment["HOME"]) / ".codex"
        protected.mkdir(parents=True)
        self.target.symlink_to(protected, target_is_directory=True)
        self.cli("link", "--target", self.target, code=1)
        self.assertEqual(list(protected.iterdir()), [])

    def test_symlinked_parent_refused_before_changes(self):
        outside = self.base / "outside"
        outside.mkdir()
        self.target.mkdir()
        (self.target / ".agents").symlink_to(outside, target_is_directory=True)
        self.cli("link", "--target", self.target, code=1)
        self.assertFalse((self.target / ".claude").exists())
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlinked_skills_directory_refused(self):
        outside = self.base / "outside"
        outside.mkdir()
        parent = self.target / ".agents"
        parent.mkdir(parents=True)
        (parent / "skills").symlink_to(outside, target_is_directory=True)
        self.cli("link", "--target", self.target, code=1)
        self.assertFalse((self.target / ".claude").exists())


class StubTests(HubCase):
    def mcp_input(self):
        return {"name": "sample-server", "description": "Sample", "transport": "stdio",
                "command": "example", "args": [], "env": {"API_TOKEN": "secret://sample-token", "MODE": "safe"}}

    def routine_input(self):
        return {"id": "digest", "description": "Sample", "owner": "maintainer", "runner": "t3-scheduler",
                "schedule": {"type": "interval", "every_seconds": 600}, "timezone": "UTC",
                "prompt": "prompt.md", "overlap": "skip", "timeout_seconds": 60, "enabled": False}

    def test_import_mcp_is_metadata_only(self):
        value = self.mcp_input()
        source = self.write("input.json", value)
        self.cli("import-mcp", source)
        self.assertEqual(json.loads((self.local / "mcp/servers/sample-server.json").read_text(encoding="utf-8")), value)
        self.assertEqual(len(self.manifest()["mcp_servers"]), 1)

    def test_sensitive_key_requires_reference(self):
        value = self.mcp_input()
        value["env"]["API_TOKEN"] = "ordinary-value"
        self.cli("import-mcp", self.write("input.json", value), code=1)
        self.assertFalse((self.local / "mcp").exists())

    def test_inline_token_rejected_even_under_neutral_key(self):
        value = self.mcp_input()
        value["env"]["MODE"] = "sk" + "-" + "a" * 30
        self.cli("import-mcp", self.write("input.json", value), code=1)

    def test_private_windows_path_rejected_before_serialization(self):
        value = self.mcp_input()
        value["env"]["MODE"] = "C:" + "\\" + "Users" + "\\fixture"
        self.cli("import-mcp", self.write("input.json", value), code=1)
        self.assertFalse((self.local / "mcp").exists())

    def test_unknown_mcp_fields_and_invalid_refs_rejected(self):
        for change in ({"extra": "value"}, {"env": {"API_TOKEN": "secret://Bad"}}):
            with self.subTest(change=change):
                value = {**self.mcp_input(), **change}
                self.cli("import-mcp", self.write("input.json", value), code=1)

    def test_http_server(self):
        value = {"name": "remote", "description": "Sample", "transport": "http",
                 "url": "https://example.invalid/mcp", "headers": {"Authorization": "secret://remote"}}
        self.cli("import-mcp", self.write("input.json", value), "--approve-risky")

    def test_routine_copies_prompt_without_enabling(self):
        value = self.routine_input()
        (self.root / "prompt.md").write_text("Summarize the fixture.\n", encoding="utf-8")
        self.cli("import-routine", self.write("input.json", value))
        self.assertEqual((self.local / "routines/digest/prompt.md").read_text(encoding="utf-8"), "Summarize the fixture.\n")
        self.assertEqual(self.manifest()["routines"][0]["owner"], "maintainer")
        self.assertFalse(json.loads((self.local / "routines/digest/routine.json").read_text(encoding="utf-8"))["enabled"])

    def test_import_routine_prompt_applies_private_text_rules(self):
        source = self.write("input.json", self.routine_input())
        prompt = source.parent / self.routine_input()["prompt"]
        tailnet = str(ipaddress.IPv4Address((100 << 24) + (64 << 16) + 258))
        prompt.write_text(f"Ping {tailnet}\n", encoding="utf-8")
        output = self.cli("import-routine", source, code=1)
        self.assertIn("REFUSED: definition contains tailnet-ip at line 1", output)
        self.assertFalse((self.local / "routines").exists())
        prompt.write_text(f"Read {self.base / 'home'}/log.txt\n", encoding="utf-8")
        self.cli("import-routine", source)
        self.assertEqual((self.local / "routines/digest/prompt.md").read_text(encoding="utf-8"), "Read ~/log.txt\n")

    def test_routine_owner_flag_must_match_file(self):
        source = self.write("input.json", self.routine_input())
        output = self.cli("import-routine", source, "--owner", "someone-else", code=1)
        self.assertIn("owner flag does not match routine owner", output)
        self.assertFalse((self.local / "routines").exists())
        self.cli("import-routine", source, "--owner", "maintainer")
        self.assertEqual(self.manifest()["routines"][0]["owner"], "maintainer")

    def test_routine_rejects_enabled_or_multiple_owners_or_bad_runner(self):
        for change in ({"enabled": True}, {"owner": ["one", "two"]}, {"runner": "unknown"}):
            with self.subTest(change=change):
                value = {**self.routine_input(), **change}
                self.cli("import-routine", self.write("input.json", value), code=1)
                self.assertFalse((self.local / "routines").exists())

    def test_routine_other_schedule_types(self):
        schedules = [{"type": "fixed_time", "time_of_day": "09:00", "weekdays": [1, 2, 3, 4, 5]},
                     {"type": "cron", "expression": "0 9 * * *"}, {"type": "webhook"}]
        for schedule in schedules:
            with self.subTest(schedule=schedule):
                value = {**self.routine_input(), "schedule": schedule}
                self.cli("import-routine", self.write("input.json", value))
                self.assertEqual(len(self.manifest()["routines"]), 1)

    def test_routine_schedule_validation(self):
        for schedule in ({"type": "interval", "every_seconds": 0},
                         {"type": "fixed_time", "time_of_day": "25:00"},
                         {"type": "cron", "expression": ""}, {"type": "unknown"}):
            with self.subTest(schedule=schedule):
                value = {**self.routine_input(), "schedule": schedule}
                self.cli("import-routine", self.write("input.json", value), code=1)

    def test_duplicate_json_owner_rejected(self):
        value = json.dumps(self.routine_input()).replace('"owner": "maintainer"', '"owner": "one", "owner": "two"')
        source = self.root / "input.json"
        source.write_text(value, encoding='utf-8')
        self.cli("import-routine", source, code=1)

    def test_stub_help(self):
        self.assertIn("Never starts or contacts the server", self.cli("import-mcp", "--help"))
        self.assertIn("Never schedules or runs it", self.cli("import-routine", "--help"))


class ComposioCLITests(HubCase):
    def test_connect_composio_round_trip(self):
        arguments = ('connect-composio', '--name', 'research', '--url', 'https://example.com/session/mcp')
        output = self.cli(*arguments)
        self.assertIn('python3 store.py set composio-api-key', output)
        entry = self.manifest()['mcp_servers'][0]
        self.assertEqual(entry['name'], 'composio-research')
        server = json.loads((self.local / entry['path'].removeprefix('.hub-store/catalog/')).read_text(encoding='utf-8'))
        self.assertEqual(server['headers'], {'x-api-key': 'secret://composio-api-key'})
        before = (self.local / 'manifest.json').read_bytes()
        self.assertIn('Already connected', self.cli(*arguments))
        self.assertEqual((self.local / 'manifest.json').read_bytes(), before)
        self.assertIn('PASS', self.cli('check'))


class MetadataCLITests(HubCase):
    def test_plugin_import_is_metadata_only(self):
        source = self.base / "plugin.json"
        source.write_text(json.dumps({"name": "fixture-plugin", "agent": "claude", "source": "fixture@market", "version": "1", "enabled": True, "install_hint": "Use the source agent manager"}), encoding="utf-8")
        self.cli("import-plugin", source, "--origin", "scan-claude")
        entry = self.manifest()["plugins"][0]
        self.assertEqual(entry["path"], "plugins/fixture-plugin.json")
        self.assertEqual(entry["provenance"]["origin"], "scan-claude")
        self.assertFalse((self.local / "skills").exists())

    def test_connection_import_rejects_auth_payload_and_invalid_endpoint(self):
        source = self.base / "connection.json"
        value = {"name": "fixture-connection", "provider": "fixture", "endpoint": "https://example.invalid/mcp", "auth_status": "unknown"}
        source.write_text(json.dumps(value), encoding='utf-8')
        self.cli("import-connection", source)
        self.assertEqual(self.manifest()["connections"][0]["path"], "connections/fixture-connection.json")
        for change in ({"token": "canary-secret-value-0001"}, {"endpoint": None}):
            source.write_text(json.dumps({**value, **change}), encoding='utf-8')
            output = self.cli("import-connection", source, code=1)
            self.assertNotIn("canary-secret-value-0001", output)
            self.assertNotIn("Traceback", output)


class ScanConnectCLITests(HubCase):
    def test_import_ends_with_single_executable_connect_next_step(self):
        home = self.base / "fixture-home"
        skill = home / ".claude/skills/sample"
        skill.parent.mkdir(parents=True)
        shutil.copytree(self.source, skill)
        output = self.cli("scan", "--home", home, "--agent", "claude", "--import", "--yes")
        command = python_command() + " hub.py connect"
        self.assertEqual(output.splitlines()[-1], command)
        self.assertEqual(output.splitlines().count(command), 1)
        result = json.loads(output.splitlines()[-2])
        self.assertEqual(result["counts"], {"imported": 1, "rejected": 0})
        self.assertEqual(self.manifest()["skills"][0]["name"], "sample")
        self.assertIn("Claude", self.cli("connect", "--home", home, "--agent", "claude"))
        self.assertEqual(json.loads((self.local / "manifest.json").read_text(encoding="utf-8"))["skills"][0]["name"], "sample")
        self.assertFalse((home / ".claude.json").exists())


class GrokBoxExportCLITests(HubCase):
    def test_real_shaped_routine_imports_and_check_passes(self):
        export = self.base / "box-export"
        (export / "workflows/alpha").mkdir(parents=True)
        (export / "workflows/alpha/SKILL.md").write_text("---\nname: alpha\ndescription: Box workflow.\n---\nDo it.\n", encoding="utf-8")
        (export / "routines.json").write_text(json.dumps([
            {"name": "hub-t3-done", "owner_bot": "Agent Hub Ops", "trigger": {"type": "webhook"}, "prompt": "Report the finished task."}]), encoding="utf-8")
        home = self.base / "empty-home"
        home.mkdir()
        self.environment["GROK_EXPORT_DIR"] = str(export)
        output = self.cli("scan", "--home", home, "--agent", "grok", "--all", "--json")
        report = json.loads(output[:output.rindex("}") + 1])
        routine = next(i for i in report["items"] if i["kind"] == "routine")
        self.assertEqual((routine["name"], routine["status"], routine["agent"]), ("hub-t3-done", "new", "grok"))
        self.cli("scan", "--home", home, "--agent", "grok", "--import", "--yes")
        value = json.loads((self.local / "routines/hub-t3-done/routine.json").read_text(encoding="utf-8"))
        self.assertEqual((value["runner"], value["owner"], value["enabled"]), ("grok-bot", "agent-hub-ops", False))
        self.assertEqual(value["schedule"], {"type": "webhook"})
        self.assertEqual([s["name"] for s in self.manifest()["skills"]], ["alpha"])
        self.cli("check")
