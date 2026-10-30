import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import hub
import lifecycle
from checks import validators
from test_hub import HubCase


class LifecycleCase(HubCase):
    def run_hub(self, *args, code=0, stdin=subprocess.DEVNULL):
        result = subprocess.run([sys.executable, str(self.root / "hub.py"), *map(str, args)], cwd=self.root,
                                env=self.environment, capture_output=True, text=True, encoding="utf-8", stdin=stdin)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def ship_rule(self):
        path = self.root / "rules/shared.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text("Shared rule\n", encoding="utf-8")
        checksum = validators.digest(path)
        manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        manifest["rules"] = [{"name": "shared", "path": "rules/shared.md", "owner": "agentlocker",
                              "provenance": {"source_id": "src-" + checksum[:12], "origin": "authored"},
                              "sha256": checksum}]
        self.write("manifest.json", manifest)

    def routine(self, owner="maintainer"):
        source = self.base / "routine-input"
        source.mkdir(exist_ok=True)
        self.write_to(source / "routine.json", {
            "id": "nightly", "description": "Fixture", "owner": owner, "runner": "t3-scheduler",
            "schedule": {"type": "interval", "every_seconds": 60}, "timezone": "UTC",
            "prompt": "prompt.md", "overlap": "skip", "timeout_seconds": 60, "enabled": False})
        (source / "prompt.md").write_bytes(b"Fixture prompt\n")
        return source / "routine.json"

    def write_to(self, path, value):
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def mcp(self):
        path = self.base / "server.json"
        self.write_to(path, {"name": "tool-server", "description": "Fixture", "transport": "stdio",
                             "command": "example", "args": []})
        return path

    def tree(self):
        return sorted(p.relative_to(self.root).as_posix() for p in (self.root / ".hub-store").rglob("*"))


class ListTests(LifecycleCase):
    def test_lists_shipped_and_imported_with_source(self):
        self.ship_rule()
        self.cli("import-skill", self.source, "--owner", "maintainer", "--origin", "upstream")
        output = self.run_hub("list")
        self.assertRegex(output, r"rules\s+shared\s+agentlocker\s+authored\s+shipped")
        self.assertRegex(output, r"skills\s+sample\s+maintainer\s+upstream\s+imported")

    def test_json_rows_carry_every_field(self):
        self.ship_rule()
        self.cli("import-skill", self.source)
        rows = json.loads(self.run_hub("list", "--json"))
        self.assertEqual({(r["kind"], r["name"], r["source"]) for r in rows},
                         {("rules", "shared", "shipped"), ("skills", "sample", "imported")})
        self.assertEqual({"kind", "name", "owner", "origin", "source"}, set(rows[0]))

    def test_kind_filter(self):
        self.ship_rule()
        self.cli("import-skill", self.source)
        rows = json.loads(self.run_hub("list", "--kind", "skills", "--json"))
        self.assertEqual([r["name"] for r in rows], ["sample"])
        self.assertEqual(json.loads(self.run_hub("list", "--kind", "plugins", "--json")), [])
        self.run_hub("list", "--kind", "bogus", code=2)

    def test_empty_catalog_says_so(self):
        self.assertIn("No items", self.run_hub("list"))


class RemoveTests(LifecycleCase):
    def test_remove_deletes_files_and_entry_and_keeps_history(self):
        self.cli("import-skill", self.source)
        output = self.run_hub("remove", "skills", "sample", "--yes")
        self.assertIn("Removed skills: sample", output)
        self.assertFalse((self.local / "skills/sample").exists())
        self.assertEqual(self.manifest()["skills"], [])
        kept = list((self.root / ".hub-store/history/skills/sample").glob("*/skills/sample/SKILL.md"))
        self.assertEqual(len(kept), 1)
        self.assertIn("Hello", kept[0].read_text(encoding="utf-8"))
        self.assertNotIn("FAIL", self.run_hub("check"))

    def test_singular_kind_is_accepted(self):
        self.cli("import-skill", self.source)
        self.run_hub("remove", "skill", "sample", "--yes")
        self.assertEqual(self.manifest()["skills"], [])

    def test_non_interactive_without_yes_fails_and_changes_nothing(self):
        self.cli("import-skill", self.source)
        before = self.tree()
        output = self.run_hub("remove", "skills", "sample", code=1)
        self.assertIn("--yes", output)
        self.assertEqual(self.tree(), before)
        self.assertEqual(len(self.manifest()["skills"]), 1)

    def test_refuses_shipped_item(self):
        self.ship_rule()
        output = self.run_hub("remove", "rules", "shared", "--yes", code=1)
        self.assertIn("shipped", output)
        self.assertTrue((self.root / "rules/shared.md").exists())

    def test_refuses_item_owned_by_another_owner(self):
        self.cli("import-skill", self.source, "--owner", "maintainer")
        output = self.run_hub("remove", "skills", "sample", "--yes", code=1)
        self.assertIn("maintainer", output)
        self.assertTrue((self.local / "skills/sample/SKILL.md").exists())
        self.run_hub("remove", "skills", "sample", "--owner", "maintainer", "--yes")
        self.assertFalse((self.local / "skills/sample").exists())

    def test_remove_refuses_symlinked_history_before_deleting(self):
        self.import_skill()
        outside = self.base / "outside-history"
        outside.mkdir()
        (self.root / ".hub-store/history").symlink_to(outside, target_is_directory=True)
        self.run_hub("remove", "skills", "sample", "--yes", code=1)
        self.assertTrue((self.local / "skills/sample/SKILL.md").exists())
        self.assertEqual(len(self.manifest()["skills"]), 1)
        self.assertEqual(list(outside.iterdir()), [])

    def test_remove_refuses_symlinked_catalog_skill_and_preserves_source(self):
        self.import_skill()
        destination = self.local / "skills/sample"
        import shutil
        shutil.rmtree(destination)
        destination.symlink_to(self.source, target_is_directory=True)
        before = (self.source / "SKILL.md").read_bytes()
        self.run_hub("remove", "skills", "sample", "--yes", code=1)
        self.assertEqual((self.source / "SKILL.md").read_bytes(), before)
        self.assertTrue(destination.is_symlink())
        self.assertEqual(len(self.manifest()["skills"]), 1)

    def test_unknown_item_is_reported(self):
        self.assertIn("no imported", self.run_hub("remove", "skills", "ghost", "--yes", code=1))

    def test_refuses_content_changed_since_import(self):
        self.cli("import-skill", self.source)
        (self.local / "skills/sample/SKILL.md").write_text("---\nname: sample\ndescription: edited\n---\n", encoding="utf-8")
        self.run_hub("remove", "skills", "sample", "--yes", code=1)
        self.assertTrue((self.local / "skills/sample").exists())
        self.assertEqual(len(self.manifest()["skills"]), 1)

    def test_remove_rule_and_mcp_and_other_items_untouched(self):
        rule = self.base / "notes.md"
        rule.write_bytes(b"Notes\n")
        self.cli("import-rule", rule)
        self.cli("import-mcp", self.mcp())
        self.import_skill()
        self.run_hub("remove", "rules", "notes", "--yes")
        self.run_hub("remove", "mcp_servers", "tool-server", "--yes")
        manifest = self.manifest()
        self.assertEqual(manifest["rules"], [])
        self.assertEqual(manifest["mcp_servers"], [])
        self.assertEqual([e["name"] for e in manifest["skills"]], ["sample"])
        self.assertFalse((self.local / "rules/notes.md").exists())
        self.assertFalse((self.local / "mcp/servers/tool-server.json").exists())

    def test_remove_routine_removes_definition_and_prompt(self):
        self.cli("import-routine", self.routine())
        self.assertTrue((self.local / "routines/nightly/prompt.md").exists())
        self.run_hub("remove", "routines", "nightly", "--owner", "maintainer", "--yes")
        self.assertFalse((self.local / "routines/nightly").exists())
        self.assertEqual(self.manifest()["routines"], [])

    def test_remove_routine_keeps_unrelated_files_in_its_directory(self):
        self.cli("import-routine", self.routine())
        (self.local / "routines/nightly/notes.txt").write_text("mine\n", encoding="utf-8")
        output = self.run_hub("remove", "routines", "nightly", "--owner", "maintainer", "--yes")
        self.assertIn("notes.txt", output)
        self.assertEqual(sorted(p.name for p in (self.local / "routines/nightly").iterdir()), ["notes.txt"])

    def test_remove_clears_source_path_bindings(self):
        source = self.mcp()
        args = SimpleNamespace(command="import-mcp", path=str(source), owner="agentlocker", origin="local-import",
                               name=None, approve_risky=False,
                               path_bindings={"command": {"portable": "example", "resolved": str(self.base / "orig")}})
        with contextlib.redirect_stdout(io.StringIO()):
            hub.import_item(args, self.root)
        bindings = self.root / ".hub-store/source-path-bindings.json"
        self.assertIn("tool-server", json.loads(bindings.read_text(encoding="utf-8"))["servers"])
        self.run_hub("remove", "mcp_servers", "tool-server", "--yes")
        self.assertTrue(not bindings.exists() or json.loads(bindings.read_text(encoding="utf-8"))["servers"] == {})

    def test_failed_publication_restores_everything_and_leaves_no_history(self):
        self.cli("import-skill", self.source)
        before = self.tree()
        manifest = (self.local / "manifest.json").read_bytes()
        original = Path.replace

        def failing(path, target):
            if path.name == "pending" and Path(target) == self.local / "manifest.json":
                raise OSError("injected")
            return original(path, target)

        args = SimpleNamespace(kind="skills", name="sample", owner=None, yes=True)
        with patch.object(Path, "replace", failing), self.assertRaises(OSError), \
                contextlib.redirect_stdout(io.StringIO()):
            lifecycle.remove(args, hub, self.root)
        self.assertEqual(self.tree(), before)
        self.assertEqual((self.local / "manifest.json").read_bytes(), manifest)
        self.assertTrue((self.local / "skills/sample/SKILL.md").exists())

    def test_reports_links_that_would_dangle_without_touching_them(self):
        self.import_skill()
        project = self.base / "project"
        project.mkdir()
        self.run_hub("link", "--target", project)
        link = project / ".claude/skills/sample"
        self.assertTrue(link.is_symlink())
        output = self.run_hub("remove", "skills", "sample", "--yes")
        self.assertIn("Dangling link", output)
        self.assertIn(str(link), output)
        self.assertIn(str(project / ".agents/skills/sample"), output)
        self.assertTrue(link.is_symlink())
        self.assertFalse(link.exists())

    def test_unrelated_links_are_not_reported(self):
        self.import_skill()
        project = self.base / "project"
        project.mkdir()
        self.run_hub("link", "--target", project)
        other = self.base / "other"
        (other / "SKILL.md").parent.mkdir()
        (other / "SKILL.md").write_text("---\nname: other\ndescription: d\n---\n", encoding="utf-8")
        self.cli("import-skill", other)
        self.assertNotIn("Dangling", self.run_hub("remove", "skills", "other", "--yes"))

    def test_confirmation_eof_requires_explicit_flag(self):
        with patch("sys.stdin") as stdin, patch("builtins.input", side_effect=EOFError):
            stdin.isatty.return_value = True
            with self.assertRaisesRegex(ValueError, "--yes"):
                lifecycle.confirmed(SimpleNamespace(yes=False), "Remove it?")

    def test_confirmation_prompt(self):
        self.import_skill()
        with patch("sys.stdin") as stdin, patch("builtins.input", return_value="y"):
            stdin.isatty.return_value = True
            self.assertTrue(lifecycle.confirmed(SimpleNamespace(yes=False), "Remove it?"))
        with patch("sys.stdin") as stdin, patch("builtins.input", return_value="n"):
            stdin.isatty.return_value = True
            self.assertFalse(lifecycle.confirmed(SimpleNamespace(yes=False), "Remove it?"))
        with patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            with self.assertRaises(ValueError):
                lifecycle.confirmed(SimpleNamespace(yes=False), "Remove it?")


class UpdateTests(LifecycleCase):
    def test_update_refuses_symlinked_history_and_preserves_old_content(self):
        self.import_skill()
        before = (self.local / "skills/sample/SKILL.md").read_bytes()
        outside = self.base / "outside-history"
        outside.mkdir()
        (self.root / ".hub-store/history").symlink_to(outside, target_is_directory=True)
        (self.source / "SKILL.md").write_text("---\nname: sample\ndescription: changed\n---\n", encoding="utf-8")
        self.cli("import-skill", self.source, code=1)
        self.assertEqual((self.local / "skills/sample/SKILL.md").read_bytes(), before)
        self.assertEqual(list(outside.iterdir()), [])

    def test_changed_reimport_updates_and_keeps_previous_content(self):
        self.cli("import-skill", self.source, "--origin", "scan-claude")
        (self.source / "SKILL.md").write_text("---\nname: sample\ndescription: A sample skill\n---\nChanged\n", encoding="utf-8")
        self.cli("import-skill", self.source, "--origin", "scan-claude")
        self.assertIn("Changed", (self.local / "skills/sample/SKILL.md").read_text(encoding="utf-8"))
        kept = list((self.root / ".hub-store/history/skills/sample").glob("*/skills/sample/SKILL.md"))
        self.assertEqual(len(kept), 1)
        self.assertIn("Hello", kept[0].read_text(encoding="utf-8"))

    def test_unchanged_reimport_keeps_no_history(self):
        self.import_skill()
        self.import_skill()
        self.assertFalse((self.root / ".hub-store/history").exists())

    def test_first_import_keeps_no_history(self):
        self.import_skill()
        self.assertFalse((self.root / ".hub-store/history").exists())

    def test_routine_prompt_change_keeps_previous_prompt(self):
        routine = self.routine()
        self.cli("import-routine", routine)
        (routine.parent / "prompt.md").write_bytes(b"Changed prompt\n")
        self.cli("import-routine", routine)
        kept = list((self.root / ".hub-store/history/routines/nightly").glob("*/routines/nightly/prompt.md"))
        self.assertEqual([p.read_bytes() for p in kept], [b"Fixture prompt\n"])

    def test_failed_update_keeps_no_history(self):
        self.import_skill()
        before = self.tree()
        (self.source / "SKILL.md").write_text("---\nname: sample\ndescription: A sample skill\n---\nChanged\n", encoding="utf-8")
        original = Path.replace

        def failing(path, target):
            if path.name == "pending" and Path(target) == self.local / "manifest.json":
                raise OSError("injected")
            return original(path, target)

        args = SimpleNamespace(command="import-skill", path=str(self.source), owner=None, origin="local-import",
                               name=None, approve_risky=False)
        with patch.object(Path, "replace", failing), self.assertRaises(OSError), \
                contextlib.redirect_stdout(io.StringIO()):
            hub.import_item(args, self.root)
        self.assertEqual(self.tree(), before)

    def test_scan_conflict_hint_command_updates_the_item(self):
        import scanner
        self.cli("import-skill", self.source, "--origin", "scan-claude")
        (self.source / "SKILL.md").write_text("---\nname: sample\ndescription: A sample skill\n---\nChanged\n", encoding="utf-8")
        item = {"kind": "skill", "agent": "claude", "name": "sample", "source": "~/.claude/skills/sample"}
        scanner.conflict(item, "reason", True)
        hint = item["next_step"]
        self.assertIn("--origin scan-claude", hint)
        self.cli("import-skill", self.source, "--name", "sample", "--origin", "scan-claude")
        self.assertIn("Changed", (self.local / "skills/sample/SKILL.md").read_text(encoding="utf-8"))

    def test_other_origin_error_names_the_origin_to_use(self):
        self.cli("import-skill", self.source, "--origin", "upstream")
        output = self.cli("import-skill", self.source, "--origin", "other", code=1)
        self.assertIn("upstream", output)

    def test_other_owner_error_names_the_owner_to_use(self):
        self.cli("import-skill", self.source, "--owner", "maintainer")
        output = self.cli("import-skill", self.source, "--owner", "someone", code=1)
        self.assertIn("maintainer", output)


class HomeLinkTests(LifecycleCase):
    def setUp(self):
        super().setUp()
        self.import_skill()
        self.home = Path(self.environment["HOME"])
        self.home.mkdir()

    def test_target_or_home_is_required_and_exclusive(self):
        self.run_hub("link", code=2)
        self.run_hub("link", "--home", "--target", self.base / "t", code=2)

    def test_home_dry_run_plans_both_directories_and_writes_nothing(self):
        output = self.run_hub("link", "--home", "--dry-run")
        self.assertEqual(output.count("Would link"), 2)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_home_without_confirmation_fails_when_not_interactive(self):
        output = self.run_hub("link", "--home", code=1)
        self.assertIn("--yes", output)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_home_yes_links_into_home_skill_directories(self):
        output = self.run_hub("link", "--home", "--yes")
        self.assertEqual(output.count("Linked"), 2)
        for agent in (".claude", ".agents"):
            link = self.home / agent / "skills/sample"
            self.assertTrue(link.is_symlink())
            self.assertFalse(os.path.isabs(os.readlink(link)))
            self.assertEqual(link.resolve(), (self.local / "skills/sample").resolve())
        self.assertIn("Nothing to do", self.run_hub("link", "--home", "--yes"))

    def test_home_preserves_unfamiliar_files_and_reports_conflicts(self):
        mine = self.home / ".claude/skills/sample"
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_text("mine\n", encoding="utf-8")
        wrong = self.home / ".agents/skills"
        wrong.mkdir(parents=True)
        (wrong / "sample").symlink_to("missing")
        (wrong / "unrelated").mkdir()
        output = self.run_hub("link", "--home", "--yes")
        self.assertIn("Conflict", output)
        self.assertIn(str(mine), output)
        self.assertEqual((mine / "SKILL.md").read_text(encoding="utf-8"), "mine\n")
        self.assertEqual(os.readlink(wrong / "sample"), "missing")
        self.assertTrue((wrong / "unrelated").is_dir())

    def test_home_links_the_free_names_around_a_conflict(self):
        other = self.base / "other"
        other.mkdir()
        (other / "SKILL.md").write_text("---\nname: other\ndescription: d\n---\n", encoding="utf-8")
        self.cli("import-skill", other)
        (self.home / ".claude/skills/sample").mkdir(parents=True)
        self.run_hub("link", "--home", "--yes")
        self.assertTrue((self.home / ".claude/skills/other").is_symlink())
        self.assertTrue((self.home / ".agents/skills/sample").is_symlink())
        self.assertFalse((self.home / ".claude/skills/sample").is_symlink())

    def test_home_refuses_symlinked_skill_directory(self):
        real = self.base / "elsewhere"
        real.mkdir()
        (self.home / ".claude").mkdir()
        (self.home / ".claude/skills").symlink_to(real)
        self.run_hub("link", "--home", "--yes", code=1)
        self.assertEqual(list(real.iterdir()), [])

    def test_home_refuses_non_directory_parent_without_writing_other_agent(self):
        (self.home / ".agents").write_bytes(b"mine")
        self.run_hub("link", "--home", "--yes", code=1)
        self.assertEqual((self.home / ".agents").read_bytes(), b"mine")
        self.assertFalse((self.home / ".claude").exists())

    def test_home_refuses_symlinked_link_registry_without_overwriting_it(self):
        outside = self.base / "registry.json"
        outside.write_bytes(b"mine")
        (self.root / ".hub-store/links.json").symlink_to(outside)
        self.run_hub("link", "--home", "--yes", code=1)
        self.assertEqual(outside.read_bytes(), b"mine")

    def test_target_equal_to_home_is_still_refused_and_points_at_home_flag(self):
        output = self.run_hub("link", "--target", self.home, code=1)
        self.assertIn("--home", output)
        self.assertEqual(list(self.home.iterdir()), [])


class HelpTests(LifecycleCase):
    def test_import_help_is_accurate_per_kind(self):
        for kind in ("mcp", "routine", "plugin", "connection"):
            with self.subTest(kind=kind):
                self.assertNotIn("Metadata-only stub", self.run_hub(f"import-{kind}", "--help"))
        self.assertRegex(self.run_hub("import-mcp", "--help").lower(), r"gateway")
        self.assertRegex(self.run_hub("import-routine", "--help").lower(), r"never schedules|does not schedule")
        self.assertNotRegex(self.run_hub("import-mcp", "--help").lower(), r"never schedules")

    def test_link_help_mentions_projects_and_home(self):
        text = self.run_hub("link", "--help")
        self.assertNotIn("disposable", text)
        self.assertIn("--home", text)
        self.assertIn(".claude/skills", text)


if __name__ == "__main__":
    unittest.main()
