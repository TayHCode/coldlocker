import base64
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

from checks.validators import digest
from mcp_endpoint import MAX_ITEM_BYTES, TOOLS, dispatch, inventory, repo_file
from test_store import make_hub


ROOT = Path(__file__).resolve().parents[1]


def copy_shipped(root):
    root.mkdir(parents=True)
    for name in ("manifest.json", "mcp_endpoint.py", "calllog.py", "store.py", "portable.py"):
        shutil.copyfile(ROOT / name, root / name)
    for name in ("skills", "rules", "mcp", "routines", "plugins", "connections", "checks"):
        if (ROOT / name).is_dir():
            shutil.copytree(ROOT / name, root / name)


class EndpointTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".scratch/test-root"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.shipped = self.root / "shipped"
        copy_shipped(self.shipped)
        self.manifest = make_hub(self.root)
        self.manifest["routines"][0]["path"] += "/routine.json"
        (self.root / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def call(self, method, params=None):
        return dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, self.root)

    def test_initialize_ping_and_notification(self):
        self.assertIn("tools", self.call("initialize")["result"]["capabilities"])
        self.assertEqual(self.call("ping")["result"], {})
        self.assertIsNone(dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}, self.root))

    def test_tool_list_and_all_calls(self):
        tools = self.call("tools/list")["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, set(TOOLS) | {"hub_get_item"})
        for tool in tools:
            if tool["name"] == "hub_get_item":
                continue
            response = self.call("tools/call", {"name": tool["name"], "arguments": {}})
            values = json.loads(response["result"]["content"][0]["text"])
            if tool["name"] in ("hub_list_plugins", "hub_list_connections"):
                self.assertEqual(values, [])
                continue
            entry = values[0]
            self.assertEqual(entry["owner"], "agentlocker")
            self.assertEqual(entry["sha256"], "a" * 64)
            if tool["name"] != "hub_list_rules":
                self.assertIn("description", entry)
        self.assertIn("secret://sample-token", json.dumps(inventory(self.root, "mcp_servers")))

    def test_protocol_errors(self):
        self.assertEqual(dispatch([], self.root)["error"]["code"], -32600)
        self.assertEqual(self.call("unknown")["error"]["code"], -32601)
        self.assertEqual(self.call("tools/call", {"name": "hub_list_skills", "arguments": {"path": "elsewhere"}})["error"]["code"], -32602)
        self.assertEqual(self.call("tools/call", {"name": []})["error"]["code"], -32602)

    def test_routine_manifest_file_path(self):
        for root in (self.root, self.shipped):
            with self.subTest(root=root.name):
                manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
                self.assertTrue(manifest["routines"])
                response = dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                     "params": {"name": "hub_list_routines", "arguments": {}}}, root)
                self.assertNotIn("error", response)
                self.assertIs(response["result"]["isError"], False)
                values = json.loads(response["result"]["content"][0]["text"])
                expected = []
                for entry in manifest["routines"]:
                    definition = json.loads((root / entry["path"]).read_text(encoding="utf-8"))
                    self.assertEqual(entry["id"], definition["id"])
                    expected.append({**entry, "description": definition["description"]})
                self.assertEqual(values, expected)

    def test_stdio_tool_list_and_all_calls(self):
        kinds = ("skills", "rules", "mcp_servers", "routines", "plugins", "connections")
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "endpoint-test", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        requests.extend({"jsonrpc": "2.0", "id": index, "method": "tools/call",
                         "params": {"name": "hub_list_" + kind, "arguments": {}}}
                        for index, kind in enumerate(kinds, 3))
        for name in ("mcp_endpoint.py", "calllog.py", "store.py", "portable.py"):
            shutil.copyfile(ROOT / name, self.root / name)
        for root in (self.root, self.shipped):
            with self.subTest(root=root.name):
                result = subprocess.run([sys.executable, str(root / "mcp_endpoint.py")],
                                        input="".join(json.dumps(r) + "\n" for r in requests),
                                        text=True, capture_output=True, timeout=10, cwd=root, encoding='utf-8')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                responses = [json.loads(line) for line in result.stdout.splitlines()]
                self.assertEqual([r["id"] for r in responses], list(range(1, 9)))
                for response in responses:
                    self.assertEqual(response["jsonrpc"], "2.0")
                    self.assertNotIn("error", response)
                self.assertIn("tools", responses[0]["result"]["capabilities"])
                tools = responses[1]["result"]["tools"]
                self.assertEqual({t["name"] for t in tools}, {"hub_list_" + k for k in kinds} | {"hub_get_item"})
                manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
                for kind, response in zip(kinds, responses[2:]):
                    self.assertIs(response["result"]["isError"], False)
                    content = response["result"]["content"]
                    self.assertEqual(len(content), 1)
                    self.assertEqual(content[0]["type"], "text")
                    values = json.loads(content[0]["text"])
                    if kind in ("plugins", "connections"):
                        self.assertEqual(values, [])
                        continue
                    self.assertTrue(values)
                    metadata = [{key: entry[key] for key in manifest[kind][0]} for entry in values]
                    self.assertEqual(metadata, manifest[kind])
                    if kind != "rules":
                        self.assertTrue(all(entry["description"] for entry in values))

    def test_stdio_always_verify_packages_are_complete(self):
        names = (
            "evidence-before-done", "principle-prove-it-works",
            "create-verification-skill", "maintain-verification-skill",
            "principle-test-behavior-not-implementation",
        )
        requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": "hub_list_skills", "arguments": {}}}]
        requests.extend({"jsonrpc": "2.0", "id": index, "method": "tools/call",
                         "params": {"name": "hub_get_item",
                                    "arguments": {"kind": "skill", "name": name}}}
                        for index, name in enumerate(names, 4))
        result = subprocess.run([sys.executable, "-B", "mcp_endpoint.py"],
                                input="".join(json.dumps(r) + "\n" for r in requests),
                                cwd=self.shipped, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([r["id"] for r in responses], list(range(1, 9)))
        self.assertTrue(all("error" not in r for r in responses))
        self.assertIn("hub_get_item", {t["name"] for t in responses[1]["result"]["tools"]})
        listed = json.loads(responses[2]["result"]["content"][0]["text"])
        self.assertTrue(set(names) <= {entry["name"] for entry in listed})
        for name, response in zip(names, responses[3:]):
            with self.subTest(skill=name):
                self.assertFalse(response["result"]["isError"])
                item = json.loads(response["result"]["content"][0]["text"])
                self.assertEqual(item["omitted"], [])
                self.assertFalse(item["truncated"])
                package = self.shipped / "skills" / name
                expected = {p.relative_to(self.shipped).as_posix(): p.read_text(encoding="utf-8")
                            for p in package.rglob("*") if p.is_file()}
                self.assertEqual(item["files"], expected)
                self.assertIn(f"skills/{name}/SKILL.md", item["files"])
                if name != "evidence-before-done":
                    self.assertIn(f"skills/{name}/LICENSE", item["files"])
                    self.assertIn(f"skills/{name}/PROVENANCE.md", item["files"])
                if name == "create-verification-skill":
                    for file in ("README.md", "create-note.md", "search.md"):
                        self.assertIn(f"skills/{name}/references/feature-map-example/{file}",
                                      item["files"])

    def test_unreadable_description_keeps_metadata_and_reports_error(self):
        (self.root / "skills/sample/SKILL.md").unlink()
        [entry] = inventory(self.root, "skills")
        self.assertEqual({k: v for k, v in entry.items() if k != "error"}, self.manifest["skills"][0])
        self.assertEqual(entry["error"], "unreadable file")

    def test_malformed_files_report_specific_inventory_errors(self):
        (self.root / "skills/sample/SKILL.md").write_bytes(b"---\ndescription: \xff\n---\n")
        self.assertEqual(inventory(self.root, "skills")[0]["error"], "invalid text encoding")
        (self.root / "routines/sample/routine.json").write_text("{", encoding="utf-8")
        self.assertEqual(inventory(self.root, "routines")[0]["error"], "invalid JSON")
        listed = json.loads(self.call("tools/call", {"name": "hub_list_routines"})["result"]["content"][0]["text"])
        self.assertEqual(listed[0]["error"], "invalid JSON")
        self.assertEqual(listed[0]["id"], "sample")
        self.assertNotIn(str(self.root), json.dumps(listed))

    def test_healthy_entries_have_no_error_field(self):
        for kind in ("skills", "rules", "mcp_servers", "routines"):
            self.assertTrue(all("error" not in entry for entry in inventory(self.root, kind)))

    def test_path_escape_and_store_paths_refused(self):
        for path in ("../escape", "/escape", ".hub-store/secrets/sample", "skills/../sample"):
            self.manifest["skills"][0]["path"] = path
            (self.root / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
            self.assertIn("error", self.call("tools/call", {"name": "hub_list_skills"}))

    def test_symlink_outside_is_not_read(self):
        path = self.root / "skills/sample/SKILL.md"
        path.unlink()
        path.symlink_to(self.root.parent / "missing")
        self.assertIn("error", self.call("tools/call", {"name": "hub_list_skills"}))

    def test_sensitive_inline_value_rejected(self):
        path = self.root / "mcp/servers/sample.json"
        data = json.loads(path.read_text(encoding='utf-8'))
        data["env"]["API_TOKEN"] = "in" + "line"
        path.write_text(json.dumps(data), encoding='utf-8')
        response = self.call("tools/call", {"name": "hub_list_mcp_servers"})
        self.assertIn("error", response)
        self.assertNotIn(data["env"]["API_TOKEN"], json.dumps(response))

    def test_multiline_skill_description(self):
        path = self.root / "skills/sample/SKILL.md"
        path.write_text("---\ndescription: >-\n  First line\n  second line\nlicense: MIT\n---\nBody", encoding="utf-8")
        self.assertEqual(inventory(self.root, "skills")[0]["description"], "First line second line")

    def test_manifest_symlink_is_refused(self):
        path = self.root / "manifest.json"
        destination = self.root / "alternate.json"
        path.rename(destination)
        path.symlink_to(destination)
        self.assertIn("error", self.call("tools/call", {"name": "hub_list_rules"}))


class GetItemTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".scratch/test-root"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.shipped = self.root / "shipped"
        copy_shipped(self.shipped)
        self.manifest = make_hub(self.root)
        self.manifest["routines"][0]["path"] += "/routine.json"
        (self.root / "routines/sample/routine-prompt.md").write_text("Sample prompt\n", encoding="utf-8")
        self.write_routine({"id": "sample", "description": "Sample routine", "enabled": False,
                            "prompt": "routine-prompt.md"})

    def seal(self):
        for kind in ("skills", "rules", "routines"):
            for entry in self.manifest[kind]:
                entry["sha256"] = digest(self.root / entry["path"])
        (self.root / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def write_routine(self, definition):
        (self.root / "routines/sample/routine.json").write_text(json.dumps(definition), encoding="utf-8")
        self.seal()

    def call(self, method, params=None, root=None):
        return dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, root or self.root)

    def get(self, kind, name, root=None):
        return self.call("tools/call", {"name": "hub_get_item", "arguments": {"kind": kind, "name": name}}, root)

    def files(self, response):
        self.assertNotIn("error", response)
        self.assertIs(response["result"]["isError"], False)
        return json.loads(response["result"]["content"][0]["text"])["files"]

    def test_tool_is_listed_read_only(self):
        tool = next(t for t in self.call("tools/list")["result"]["tools"] if t["name"] == "hub_get_item")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertEqual(tool["inputSchema"]["required"], ["kind", "name"])

    def test_returns_main_file_text(self):
        self.assertEqual(self.files(self.get("skill", "sample")),
                         {"skills/sample/SKILL.md": "---\ndescription: Sample skill\n---\nBody\n"})
        self.assertEqual(self.files(self.get("rule", "sample")), {"rules/sample.md": "Sample rule\n"})
        routine = self.files(self.get("routine", "sample"))
        self.assertEqual(set(routine), {"routines/sample/routine.json", "routines/sample/routine-prompt.md"})
        self.assertEqual(routine["routines/sample/routine-prompt.md"], "Sample prompt\n")

    def test_shipped_items(self):
        manifest = json.loads((self.shipped / "manifest.json").read_text(encoding="utf-8"))
        for kind, key, path in (("skill", "skills", "SKILL.md"), ("rule", "rules", None), ("routine", "routines", None)):
            entry = manifest[key][0]
            name = entry.get("name", entry.get("id"))
            files = self.files(self.get(kind, name, self.shipped))
            main = entry["path"] + ("/" + path if path else "")
            self.assertEqual(files[main], (self.shipped / main).read_text(encoding='utf-8'))

    def test_refuses_unknown_kind_name_and_arguments(self):
        for kind, name in (("mcp_server", "sample"), ("skill", "missing"), ("skill", "../sample"),
                           ("skill", ".hub-store"), ("skill", "SAMPLE"), ("secret", "sample"), (1, "sample"), ("skill", None)):
            with self.subTest(kind=kind, name=name):
                self.assertIn("error", self.get(kind, name))
        for arguments in ({}, {"kind": "skill"}, {"kind": "skill", "name": "sample", "path": "x"}):
            with self.subTest(arguments=arguments):
                response = self.call("tools/call", {"name": "hub_get_item", "arguments": arguments})
                self.assertEqual(response["error"]["code"], -32602)

    def test_refuses_store_and_outside_manifest_paths(self):
        for path in ("../escape", "/escape", ".hub-store/secrets/sample", "skills/../sample"):
            self.manifest["skills"][0]["path"] = path
            (self.root / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
            with self.subTest(path=path):
                self.assertIn("error", self.get("skill", "sample"))

    def test_refuses_symlinks(self):
        outside = self.root.parent / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        path = self.root / "skills/sample/SKILL.md"
        path.unlink()
        path.symlink_to(outside)
        self.assertIn("error", self.get("skill", "sample"))

    def test_refuses_digest_mismatch(self):
        (self.root / "skills/sample/SKILL.md").write_text("tampered", encoding="utf-8")
        self.assertIn("error", self.get("skill", "sample"))

    def test_routine_prompt_is_confined(self):
        for prompt in ("../../rules/sample.md", "/etc/passwd", "sub/../routine-prompt.md", ".hub-store/x", 1):
            self.write_routine({"id": "sample", "description": "d", "prompt": prompt})
            with self.subTest(prompt=prompt):
                self.assertIn("error", self.get("routine", "sample"))

    def test_stdio_from_other_cwd(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "hub_get_item", "arguments": {"kind": "skill", "name": "evidence-before-done"}}}
        result = subprocess.run([sys.executable, str(self.shipped / "mcp_endpoint.py")], input=json.dumps(request) + "\n",
                                text=True, capture_output=True, timeout=10, cwd=self.root, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        files = json.loads(json.loads(result.stdout)["result"]["content"][0]["text"])["files"]
        self.assertEqual(files["skills/evidence-before-done/SKILL.md"],
                         (self.shipped / "skills/evidence-before-done/SKILL.md").read_text(encoding="utf-8"))


class CatalogCase(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".scratch/test-root"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = make_hub(self.root)
        self.manifest["routines"][0]["path"] += "/routine.json"
        (self.root / "routines/sample/routine-prompt.md").write_text("Sample prompt\n", encoding="utf-8")
        (self.root / "routines/sample/routine.json").write_text(json.dumps(
            {"id": "sample", "description": "Sample routine", "enabled": False, "prompt": "routine-prompt.md"}), encoding="utf-8")
        skill = self.root / "skills/sample"
        (skill / "references").mkdir()
        (skill / "references/guide.md").write_text("Guide text\n", encoding="utf-8")
        (skill / "scripts").mkdir()
        (skill / "scripts/run.py").write_text("print('hi')\n", encoding="utf-8")
        (skill / "assets").mkdir()
        (skill / "assets/logo.bin").write_bytes(b"\x00\x01\xff\xfe")
        self.seal()

    def seal(self):
        for kind in ("skills", "rules", "routines"):
            for entry in self.manifest[kind]:
                entry["sha256"] = digest(self.root / entry["path"])
        (self.root / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def call(self, method, params=None, root=None):
        return dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, root or self.root)

    def tool(self, name, arguments):
        return self.call("tools/call", {"name": name, "arguments": arguments})

    def get(self, kind, name):
        return self.tool("hub_get_item", {"kind": kind, "name": name})

    def item(self, kind, name):
        response = self.get(kind, name)
        self.assertNotIn("error", response)
        return json.loads(response["result"]["content"][0]["text"])


class SkillFilesTests(CatalogCase):
    def test_every_skill_file_is_reachable(self):
        item = self.item("skill", "sample")
        self.assertEqual(item["files"], {
            "skills/sample/SKILL.md": "---\ndescription: Sample skill\n---\nBody\n",
            "skills/sample/references/guide.md": "Guide text\n",
            "skills/sample/scripts/run.py": "print('hi')\n"})
        self.assertEqual(item["omitted"], [{"path": "skills/sample/assets/logo.bin", "size": 4, "reason": "non-text"}])
        self.assertIs(item["truncated"], False)

    def test_symlinked_supporting_file_is_listed_not_followed(self):
        outside = self.root.parent / "outside-secret.txt"
        outside.write_text("outside", encoding="utf-8")
        (self.root / "skills/sample/references/link.md").symlink_to(outside)
        self.seal()
        item = self.item("skill", "sample")
        self.assertNotIn("outside", json.dumps(item["files"]))
        self.assertIn({"path": "skills/sample/references/link.md", "reason": "symlink"}, item["omitted"])

    def test_git_metadata_is_reported_as_excluded(self):
        (self.root / "skills/sample/.git").mkdir()
        (self.root / "skills/sample/.git/config").write_text("x", encoding="utf-8")
        self.seal()
        item = self.item("skill", "sample")
        self.assertIn({"path": "skills/sample/.git", "reason": "excluded"}, item["omitted"])
        self.assertNotIn("skills/sample/.git/config", item["files"])

    def test_unsafe_file_name_is_reported(self):
        name = "a-invalid.md" if sys.platform == "win32" else "a:b.md"
        relative = "skills/sample/references/" + name
        (self.root / relative).write_text("x", encoding="utf-8")
        self.seal()
        def validate(root, path):
            if path == relative:
                raise ValueError("invalid repository path")
            return repo_file(root, path)
        if sys.platform == "win32":
            with patch("mcp_endpoint.repo_file", side_effect=validate):
                item = self.item("skill", "sample")
        else:
            item = self.item("skill", "sample")
        self.assertIn({"path": relative, "reason": "unsafe-path"}, item["omitted"])
        self.assertNotIn(relative, item["files"])

    def test_unsafe_directory_is_omitted_without_losing_other_files(self):
        relative = "skills/sample/rejected-directory"
        (self.root / relative).mkdir()
        (self.root / relative / "note.md").write_text("hidden", encoding="utf-8")
        self.seal()
        def validate(root, path):
            if path == relative:
                raise ValueError("invalid repository path")
            return repo_file(root, path)
        with patch("mcp_endpoint.repo_file", side_effect=validate):
            item = self.item("skill", "sample")
        self.assertIn({"path": relative, "reason": "unsafe-path"}, item["omitted"])
        self.assertNotIn(relative + "/note.md", item["files"])
        self.assertEqual(item["files"]["skills/sample/references/guide.md"], "Guide text\n")

    def test_size_cap_reports_truncation_explicitly(self):
        (self.root / "skills/sample/references/big.md").write_text("x" * (MAX_ITEM_BYTES + 1), encoding="utf-8")
        (self.root / "skills/sample/references/zzz-small.md").write_text("tail", encoding="utf-8")
        self.seal()
        item = self.item("skill", "sample")
        self.assertIs(item["truncated"], True)
        self.assertEqual(item["max_bytes"], MAX_ITEM_BYTES)
        self.assertIn({"path": "skills/sample/references/big.md", "size": MAX_ITEM_BYTES + 1, "reason": "size-cap"}, item["omitted"])
        self.assertEqual(item["files"]["skills/sample/references/zzz-small.md"], "tail")
        self.assertIn("skills/sample/SKILL.md", item["files"])

    def test_total_size_cap_spans_files(self):
        half = MAX_ITEM_BYTES // 2 + 10
        for name in ("a", "b"):
            (self.root / f"skills/sample/references/{name}.md").write_text("y" * half, encoding="utf-8")
        self.seal()
        item = self.item("skill", "sample")
        self.assertIs(item["truncated"], True)
        self.assertEqual(sum(1 for r in item["omitted"] if r["reason"] == "size-cap"), 1)
        self.assertLessEqual(sum(len(t.encode()) for t in item["files"].values()), MAX_ITEM_BYTES)

    def test_hash_still_verified_over_supporting_files(self):
        (self.root / "skills/sample/references/guide.md").write_text("tampered", encoding="utf-8")
        response = self.get("skill", "sample")
        self.assertEqual(response["error"]["code"], -32603)
        self.assertEqual(response["error"]["message"], "Content hash mismatch for skill: sample")

    def test_rules_and_routines_unchanged(self):
        self.assertEqual(self.item("rule", "sample")["files"], {"rules/sample.md": "Sample rule\n"})
        routine = self.item("routine", "sample")
        self.assertEqual(set(routine["files"]), {"routines/sample/routine.json", "routines/sample/routine-prompt.md"})
        self.assertIs(routine["truncated"], False)
        self.assertEqual(routine["omitted"], [])

    def test_windows_newlines_match_text_reader_behavior(self):
        paths = {"skill": "skills/sample/SKILL.md", "rule": "rules/sample.md",
                 "routine": "routines/sample/routine-prompt.md"}
        for path in paths.values():
            (self.root / path).write_bytes(b"first\r\nsecond\rthird\n")
        self.seal()
        expected = "first\nsecond\nthird\n"
        for kind, path in paths.items():
            with self.subTest(kind=kind):
                self.assertEqual(self.item(kind, "sample")["files"][path], expected)
        prompt = self.call("prompts/get", {"name": "sample"})["result"]
        self.assertTrue(prompt["messages"][0]["content"]["text"].startswith(expected))
        response = self.call("resources/read", {"uri": "agentlocker://skills/sample/SKILL.md"})
        self.assertEqual(response["result"]["contents"][0]["text"], expected)


class ProtocolTests(CatalogCase):
    def test_version_negotiation(self):
        for requested in ("2025-06-18", "2025-03-26", "2024-11-05"):
            with self.subTest(requested=requested):
                result = self.call("initialize", {"protocolVersion": requested})["result"]
                self.assertEqual(result["protocolVersion"], requested)
        for requested in ("2099-01-01", "1999-01-01", 5, None):
            with self.subTest(requested=requested):
                self.assertEqual(self.call("initialize", {"protocolVersion": requested})["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(self.call("initialize")["result"]["protocolVersion"], "2025-06-18")

    def test_capabilities_advertise_prompts_and_resources(self):
        capabilities = self.call("initialize")["result"]["capabilities"]
        self.assertEqual(set(capabilities), {"tools", "prompts", "resources"})

    def test_tool_descriptions_are_useful(self):
        tools = {t["name"]: t for t in self.call("tools/list")["result"]["tools"]}
        for name, tool in tools.items():
            with self.subTest(name=name):
                self.assertFalse(tool["description"].startswith("List hub "))
                self.assertGreater(len(tool["description"]), 60)
        self.assertIn("hub_get_item", tools["hub_list_skills"]["description"])
        properties = tools["hub_get_item"]["inputSchema"]["properties"]
        for field in ("kind", "name"):
            self.assertTrue(properties[field]["description"])
        self.assertIn("references", tools["hub_get_item"]["description"])


class PromptTests(CatalogCase):
    def test_prompts_list_one_per_skill(self):
        prompts = self.call("prompts/list")["result"]["prompts"]
        self.assertEqual(prompts, [{"name": "sample", "description": "Sample skill", "arguments": []}])

    def test_prompts_get_returns_skill_text_and_resource_hints(self):
        result = self.call("prompts/get", {"name": "sample"})["result"]
        self.assertEqual(result["description"], "Sample skill")
        [message] = result["messages"]
        self.assertEqual(message["role"], "user")
        self.assertEqual(message["content"]["type"], "text")
        text = message["content"]["text"]
        self.assertTrue(text.startswith("---\ndescription: Sample skill\n---\nBody\n"))
        self.assertIn("agentlocker://skills/sample/references/guide.md", text)
        self.assertIn("agentlocker://skills/sample/assets/logo.bin", text)
        self.assertNotIn("agentlocker://skills/sample/SKILL.md", text)

    def test_prompts_get_without_supporting_files_has_no_footer(self):
        shutil.rmtree(self.root / "skills/sample/references")
        shutil.rmtree(self.root / "skills/sample/scripts")
        shutil.rmtree(self.root / "skills/sample/assets")
        self.seal()
        text = self.call("prompts/get", {"name": "sample"})["result"]["messages"][0]["content"]["text"]
        self.assertEqual(text, "---\ndescription: Sample skill\n---\nBody\n")

    def test_prompts_get_errors(self):
        self.assertEqual(self.call("prompts/get", {"name": "missing"})["error"],
                         {"code": -32602, "message": "Unknown prompt: missing"})
        for params in ({}, {"name": 1}, {"name": "../x"}, {"name": "sample", "arguments": []}):
            with self.subTest(params=params):
                self.assertEqual(self.call("prompts/get", params)["error"]["code"], -32602)
        (self.root / "skills/sample/SKILL.md").write_text("tampered", encoding="utf-8")
        self.assertEqual(self.call("prompts/get", {"name": "sample"})["error"]["message"],
                         "Content hash mismatch for skill: sample")


class ResourceTests(CatalogCase):
    def uris(self):
        return {r["uri"]: r for r in self.call("resources/list")["result"]["resources"]}

    def read(self, uri):
        return self.call("resources/read", {"uri": uri})

    def test_resources_list_covers_skills_rules_and_routines(self):
        self.assertEqual(set(self.uris()), {
            "agentlocker://skills/sample/SKILL.md", "agentlocker://skills/sample/references/guide.md",
            "agentlocker://skills/sample/scripts/run.py", "agentlocker://skills/sample/assets/logo.bin",
            "agentlocker://rules/sample", "agentlocker://routines/sample/routine.json",
            "agentlocker://routines/sample/routine-prompt.md"})
        listed = self.uris()["agentlocker://skills/sample/references/guide.md"]
        self.assertEqual(listed["mimeType"], "text/markdown")
        self.assertEqual(listed["name"], "sample/references/guide.md")

    def test_resources_read_text(self):
        for uri, text in (("agentlocker://skills/sample/references/guide.md", "Guide text\n"),
                          ("agentlocker://rules/sample", "Sample rule\n"),
                          ("agentlocker://routines/sample/routine-prompt.md", "Sample prompt\n")):
            with self.subTest(uri=uri):
                [content] = self.read(uri)["result"]["contents"]
                self.assertEqual(content["uri"], uri)
                self.assertEqual(content["text"], text)

    def test_resources_read_binary_as_blob(self):
        [content] = self.read("agentlocker://skills/sample/assets/logo.bin")["result"]["contents"]
        self.assertEqual(base64.b64decode(content["blob"]), b"\x00\x01\xff\xfe")
        self.assertNotIn("text", content)

    def test_resources_read_refusals(self):
        bad = ("agentlocker://skills/sample/../../manifest.json", "agentlocker://skills/sample/missing.md",
               "agentlocker://skills/missing/SKILL.md", "agentlocker://skills/sample", "agentlocker://rules/missing",
               "agentlocker://mcp/sample", "https://example.com/x", "agentlocker://skills/sample//SKILL.md",
               "agentlocker://skills/sample/./SKILL.md", "agentlocker://skills/sample/a%2F..%2Fb")
        for uri in bad:
            with self.subTest(uri=uri):
                response = self.read(uri)
                self.assertEqual(response["error"]["code"], -32602)
                self.assertNotIn(str(self.root), response["error"]["message"])
        for params in ({}, {"uri": 5}):
            self.assertEqual(self.call("resources/read", params)["error"]["code"], -32602)

    def test_resources_read_unknown_message(self):
        self.assertEqual(self.read("agentlocker://skills/missing/SKILL.md")["error"]["message"], "Unknown skill: missing")

    def test_resources_read_verifies_hash(self):
        (self.root / "skills/sample/references/guide.md").write_text("tampered", encoding="utf-8")
        response = self.read("agentlocker://skills/sample/references/guide.md")
        self.assertEqual(response["error"]["message"], "Content hash mismatch for skill: sample")

    def test_resources_read_oversize_is_refused(self):
        (self.root / "skills/sample/references/big.md").write_text("x" * (MAX_ITEM_BYTES + 1), encoding="utf-8")
        self.seal()
        response = self.read("agentlocker://skills/sample/references/big.md")
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("size limit", response["error"]["message"])

    def test_symlinks_are_not_resources(self):
        outside = self.root.parent / "outside-secret.txt"
        outside.write_text("outside", encoding="utf-8")
        (self.root / "skills/sample/references/link.md").symlink_to(outside)
        self.seal()
        self.assertNotIn("agentlocker://skills/sample/references/link.md", self.uris())
        self.assertIn("error", self.read("agentlocker://skills/sample/references/link.md"))

    def test_templates(self):
        templates = self.call("resources/templates/list")["result"]["resourceTemplates"]
        self.assertEqual({t["uriTemplate"] for t in templates}, {
            "agentlocker://skills/{name}/{+path}", "agentlocker://rules/{name}", "agentlocker://routines/{id}/{file}"})

    def test_skill_template_expands_to_readable_nested_resource(self):
        templates = self.call("resources/templates/list")["result"]["resourceTemplates"]
        template = next(t["uriTemplate"] for t in templates if t["name"] == "skill-file")
        path = "references/guide.md"
        uri = template.replace("{name}", "sample").replace("{path}", quote(path, safe=""))
        uri = uri.replace("{+path}", quote(path, safe="/"))
        response = self.read(uri)
        self.assertNotIn("error", response)
        self.assertEqual(response["result"]["contents"][0]["text"], "Guide text\n")


class ErrorTests(CatalogCase):
    def test_unknown_item_is_specific(self):
        for kind in ("skill", "rule", "routine"):
            self.assertEqual(self.get(kind, "missing")["error"], {"code": -32602, "message": f"Unknown {kind}: missing"})

    def test_hash_mismatch_for_each_kind(self):
        (self.root / "rules/sample.md").write_text("tampered", encoding="utf-8")
        self.assertEqual(self.get("rule", "sample")["error"]["message"], "Content hash mismatch for rule: sample")

    def test_invalid_manifest(self):
        (self.root / "manifest.json").write_text("{", encoding="utf-8")
        for response in (self.get("skill", "sample"), self.tool("hub_list_skills", {}), self.call("prompts/list")):
            self.assertEqual(response["error"], {"code": -32603, "message": "Invalid manifest"})
        (self.root / "manifest.json").write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
        self.assertEqual(self.get("skill", "sample")["error"]["message"], "Invalid manifest")

    def test_missing_manifest(self):
        (self.root / "manifest.json").unlink()
        self.assertEqual(self.tool("hub_list_skills", {})["error"], {"code": -32603, "message": "Hub manifest unreadable"})

    def test_unreadable_file_names_repo_relative_path_only(self):
        (self.root / "skills/sample/SKILL.md").unlink()
        self.seal()
        response = self.get("skill", "sample")
        self.assertEqual(response["error"], {"code": -32603, "message": "Unreadable file: skills/sample/SKILL.md"})

    def test_unreadable_routine_prompt(self):
        (self.root / "routines/sample/routine-prompt.md").unlink()
        self.seal()
        self.assertEqual(self.get("routine", "sample")["error"]["message"], "Unreadable file: routines/sample/routine-prompt.md")

    def test_invalid_routine_prompt(self):
        (self.root / "routines/sample/routine.json").write_text(json.dumps({"id": "sample", "prompt": "../x"}), encoding="utf-8")
        self.seal()
        self.assertEqual(self.get("routine", "sample")["error"]["message"], "Invalid routine prompt: sample")

    def test_symlink_is_refused_with_safe_message(self):
        path = self.root / "skills/sample/SKILL.md"
        path.unlink()
        path.symlink_to(self.root.parent / "missing")
        response = self.get("skill", "sample")
        self.assertEqual(response["error"]["code"], -32603)
        self.assertNotIn(str(self.root), json.dumps(response))

    def test_no_message_leaks_absolute_paths_or_secret_values(self):
        server = self.root / "mcp/servers/sample.json"
        data = json.loads(server.read_text(encoding="utf-8"))
        data["env"]["API_TOKEN"] = "plaintext-value-1234"
        server.write_text(json.dumps(data), encoding="utf-8")
        for response in (self.tool("hub_list_mcp_servers", {}), self.get("skill", "missing"),
                         self.call("resources/read", {"uri": "agentlocker://skills/sample/nope"})):
            text = json.dumps(response)
            self.assertNotIn(str(self.root), text)
            self.assertNotIn("plaintext-value-1234", text)
        self.assertEqual(self.tool("hub_list_mcp_servers", {})["error"]["message"], "Invalid hub content")


class StdioEndToEndTests(CatalogCase):
    def test_initialize_prompts_resources_and_get_item_over_stdio(self):
        for name in ("mcp_endpoint.py", "calllog.py", "store.py", "portable.py"):
            shutil.copyfile(ROOT / name, self.root / name)
        shutil.copytree(ROOT / "checks", self.root / "checks")
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "e2e", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "prompts/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "agentlocker://skills/sample/references/guide.md"}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
                "name": "hub_get_item", "arguments": {"kind": "skill", "name": "sample"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
                "name": "hub_get_item", "arguments": {"kind": "skill", "name": "missing"}}},
        ]
        result = subprocess.run([sys.executable, str(self.root / "mcp_endpoint.py")],
                                input="".join(json.dumps(r) + "\n" for r in requests),
                                text=True, capture_output=True, timeout=20, cwd=self.root, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        responses = {r["id"]: r for r in map(json.loads, result.stdout.splitlines())}
        self.assertEqual(set(responses), {1, 2, 3, 4, 5})
        self.assertEqual(responses[1]["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(set(responses[1]["result"]["capabilities"]), {"tools", "prompts", "resources"})
        self.assertEqual(responses[2]["result"]["prompts"][0]["name"], "sample")
        self.assertEqual(responses[3]["result"]["contents"][0]["text"], "Guide text\n")
        item = json.loads(responses[4]["result"]["content"][0]["text"])
        self.assertEqual(item["files"]["skills/sample/references/guide.md"], "Guide text\n")
        self.assertEqual(responses[5]["error"]["message"], "Unknown skill: missing")


class ShippedStatusSkillTests(unittest.TestCase):
    def test_i_have_adhd_package_is_retrievable_over_stdio(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "status-skill-test", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": "hub_list_skills", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
                "name": "hub_get_item", "arguments": {"kind": "skill", "name": "i-have-adhd"}}},
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "shipped"
            copy_shipped(root)
            result = subprocess.run([sys.executable, "-B", "mcp_endpoint.py"], cwd=root,
                                    input="".join(json.dumps(r) + "\n" for r in requests),
                                    text=True, capture_output=True, encoding="utf-8", timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            responses = {r["id"]: r for r in map(json.loads, result.stdout.splitlines())}
            self.assertEqual(set(responses), {1, 2, 3, 4})
            for response in responses.values():
                self.assertNotIn("error", response)
            self.assertIn("hub_get_item", {t["name"] for t in responses[2]["result"]["tools"]})
            entries = json.loads(responses[3]["result"]["content"][0]["text"])
            entry = next(e for e in entries if e["name"] == "i-have-adhd")
            self.assertEqual(entry["sha256"], digest(root / "skills/i-have-adhd"))
            self.assertFalse(responses[4]["result"]["isError"])
            item = json.loads(responses[4]["result"]["content"][0]["text"])
            expected = {"SKILL.md", "LICENSE", "PROVENANCE.md", "references/keep-going.md",
                        "agents/openai.yaml", "agents/gemini.toml"}
            self.assertEqual(set(item["files"]), {"skills/i-have-adhd/" + f for f in expected})
            for path, content in item["files"].items():
                self.assertEqual(content, (root / path).read_text(encoding="utf-8"))
            self.assertEqual(item["omitted"], [])
            self.assertFalse(item["truncated"])
            skill = item["files"]["skills/i-have-adhd/SKILL.md"]
            self.assertIn("(references/keep-going.md)", skill)
            contract = item["files"]["skills/i-have-adhd/references/keep-going.md"]
            for field in ("Goal:", "Done condition:", "Evidence:", "Remaining: none", "Status: DONE", "Status: BLOCKED"):
                self.assertIn(field, contract)


class EndpointIsolationTests(unittest.TestCase):
    def test_endpoint_suite_ignores_populated_local_store(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "hub"
            copy_shipped(root)
            local = root / ".hub-store/catalog"
            local.mkdir(parents=True)
            manifest = make_hub(local)
            manifest["routines"][0]["path"] += "/routine.json"
            (local / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            before = {p.relative_to(local): p.read_bytes() for p in local.rglob("*") if p.is_file()}
            suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(case)
                                       for case in (EndpointTests, GetItemTests, SkillFilesTests, ProtocolTests, PromptTests, ResourceTests, ErrorTests))
            result = unittest.TestResult()
            with patch(__name__ + ".ROOT", root):
                suite.run(result)
            self.assertTrue(result.wasSuccessful(), "\n".join(detail for _, detail in result.errors + result.failures))
            self.assertEqual({p.relative_to(local): p.read_bytes() for p in local.rglob("*") if p.is_file()}, before)
