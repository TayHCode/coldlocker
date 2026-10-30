import copy
import json
from pathlib import Path, PurePosixPath
import re
import unittest
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parents[1]
NAME = r"[a-z0-9][a-z0-9-]{0,62}"
SECRET = r"secret://[a-z0-9][a-z0-9._-]{0,62}"
SENSITIVE = r"token|secret|key|password|authorization|credential"


def read_json(path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate field: {key}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_pairs)


class ContentTests(unittest.TestCase):
    def example(self, name):
        return read_json(ROOT / "examples" / name)

    def assert_relative_path(self, value):
        self.assertIsInstance(value, str)
        path = PurePosixPath(value)
        self.assertTrue(value)
        self.assertFalse(path.is_absolute())
        self.assertNotIn("..", path.parts)
        self.assertNotIn("\\", value)
        self.assertNotIn(":", value)
        self.assertEqual(path.as_posix(), value)

    def assert_label(self, value):
        self.assertIsInstance(value, str)
        self.assertTrue(value.strip())
        self.assertLessEqual(len(value), 63)
        self.assertNotRegex(value, r"[/\\\s:]")

    def assert_secret_fields(self, definition):
        for field in ("env", "headers"):
            values = definition.get(field, {})
            self.assertIsInstance(values, dict)
            for key, value in values.items():
                self.assertIsInstance(key, str)
                self.assertIsInstance(value, str)
                self.assertTrue(value)
                if re.search(SENSITIVE, key, re.I) or value.startswith("secret://"):
                    self.assertRegex(value, rf"\A{SECRET}\Z")
                else:
                    self.assertLessEqual(len(value), 128)
                    self.assertNotRegex(value, r"[\r\n]")

    def assert_schedule(self, schedule):
        self.assertIsInstance(schedule, dict)
        kind = schedule["type"]
        self.assertIn(kind, {"interval", "fixed_time", "cron", "webhook"})
        if kind == "interval":
            self.assertIs(type(schedule["every_seconds"]), int)
            self.assertGreater(schedule["every_seconds"], 0)
        elif kind == "fixed_time":
            self.assertRegex(schedule["time_of_day"], r"\A(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")
            if "weekdays" in schedule:
                self.assertIsInstance(schedule["weekdays"], list)
                for day in schedule["weekdays"]:
                    self.assertIs(type(day), int)
                    self.assertIn(day, range(1, 8))
        elif kind == "cron":
            self.assertIsInstance(schedule["expression"], str)
            self.assertTrue(schedule["expression"].strip())

    def assert_routine(self, routine):
        self.assertRegex(routine["id"], rf"\A{NAME}\Z")
        self.assertIsInstance(routine["description"], str)
        self.assertTrue(routine["description"].strip())
        self.assert_label(routine["owner"])
        self.assertIn(routine["runner"], {"t3-scheduler", "systemd-timer", "n8n", "grok-bot"})
        self.assert_schedule(routine["schedule"])
        self.assertIsInstance(routine["timezone"], str)
        try:
            ZoneInfo(routine["timezone"])
        except ZoneInfoNotFoundError:
            try:
                ZoneInfo("UTC")
            except ZoneInfoNotFoundError:
                self.skipTest("no IANA time zone database; install tzdata to validate routine time zones")
            raise
        self.assert_relative_path(routine["prompt"])
        self.assertTrue((ROOT / "examples" / routine["prompt"]).is_file())
        self.assertIn(routine["overlap"], {"skip", "queue", "allow"})
        self.assertIs(type(routine["timeout_seconds"]), int)
        self.assertGreater(routine["timeout_seconds"], 0)
        self.assertIs(routine["enabled"], False)

    def assert_mcp(self, server):
        self.assertRegex(server["name"], rf"\A{NAME}\Z")
        self.assertIsInstance(server["description"], str)
        self.assertTrue(server["description"].strip())
        self.assertIn(server["transport"], {"stdio", "http"})
        if server["transport"] == "stdio":
            self.assertIsInstance(server["command"], str)
            self.assertTrue(server["command"].strip())
            self.assertIsInstance(server["args"], list)
            for argument in server["args"]:
                self.assertIsInstance(argument, str)
        else:
            self.assertIsInstance(server["url"], str)
            self.assertRegex(server["url"], r"\Ahttps?://[^\s]+\Z")
        self.assert_secret_fields(server)

    def test_skill_packages(self):
        paths = sorted((ROOT / "skills").glob("*/SKILL.md"))
        indexed = {entry["path"] + "/SKILL.md"
                   for entry in read_json(ROOT / "manifest.json")["skills"]}
        self.assertEqual({path.relative_to(ROOT).as_posix() for path in paths}, indexed)
        for path in paths:
            with self.subTest(skill=path.parent.name):
                text = path.read_text(encoding='utf-8')
                sections = text.split("---\n", 2)
                self.assertEqual(len(sections), 3)
                self.assertEqual(sections[0], "")
                fields = {}
                for line in sections[1].splitlines():
                    key, value = line.split(":", 1)
                    self.assertNotIn(key, fields)
                    fields[key] = value.strip()
                self.assertEqual(fields["name"], path.parent.name)
                self.assertRegex(fields["name"], rf"\A{NAME}\Z")
                self.assertTrue(fields["description"].strip())
                self.assertTrue(sections[2].strip())

    def test_shared_policy_is_short(self):
        lines = (ROOT / "rules/shared-policy.md").read_text(encoding="utf-8").splitlines()
        self.assertGreater(len(lines), 0)
        self.assertLess(len(lines), 40)

    def test_every_json_example_matches_a_definition(self):
        paths = sorted((ROOT / "examples").glob("*.json"))
        self.assertGreaterEqual(len(paths), 2)
        for path in paths:
            with self.subTest(example=path.name):
                value = read_json(path)
                self.assertIsInstance(value, dict)
                self.assertNotEqual("id" in value, "name" in value)
                if "id" in value:
                    self.assert_routine(value)
                else:
                    self.assert_mcp(value)

    def test_routine_example(self):
        self.assert_routine(self.example("routine.example.json"))

    def test_mcp_example(self):
        self.assert_mcp(self.example("mcp-server.example.json"))

    def test_sensitive_keys_require_secret_references(self):
        for field in ("env", "headers"):
            for key in ("API_TOKEN", "client_secret", "api_key", "PASSWORD", "Authorization", "credential"):
                with self.subTest(field=field, key=key):
                    self.assert_secret_fields({field: {key: "secret://example-value"}})
                    with self.assertRaises(AssertionError):
                        self.assert_secret_fields({field: {key: "fixture" + "-value"}})

    def test_malformed_secret_references_are_rejected(self):
        for value in ("secret://", "secret://../sample", "secret://UPPER", "secret://two/names"):
            with self.subTest(value=value), self.assertRaises(AssertionError):
                self.assert_secret_fields({"env": {"API_TOKEN": value}})

    def test_enabled_or_ambiguous_routines_are_rejected(self):
        for key, value in (("enabled", True), ("enabled", 0), ("owner", ["first", "second"]), ("runner", "unknown")):
            routine = copy.deepcopy(self.example("routine.example.json"))
            routine[key] = value
            with self.subTest(field=key, value=value), self.assertRaises(AssertionError):
                self.assert_routine(routine)

    def test_prompt_paths_stay_relative(self):
        for value in ("../prompt.md", "/prompt.md", "X:/prompt.md", "folder\\prompt.md"):
            with self.subTest(value=value), self.assertRaises(AssertionError):
                self.assert_relative_path(value)

    def test_agent_entrypoint_mentions_required_interfaces(self):
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for required in ("VISION.md", "docs/formats.md", "manifest.json", "skills/<name>/SKILL.md", "rules/", "hub_list_*", "mcp_endpoint.py", "secret://", "hub.py link", "hub.py check", "enabled=false", "docs/grok-bot-bootstrap.md"):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_readme_mentions_setup_interfaces(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for required in ("VISION.md", "manifest.json", "hub.py import-skill", "hub.py import-rule", "hub.py import-routine", "hub.py import-mcp", "--owner O", "--origin L", "--name N", "hub.py link", "--target <root>", "--dry-run", "hub.py check", "mcp_endpoint.py", "secret://", "docs/mcp-auth.md", "docs/grok-bot-bootstrap.md", "<HUB_DIR>", "Claude", "Codex", "Cursor", "OpenCode", "MIT"):
            with self.subTest(required=required):
                self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()
