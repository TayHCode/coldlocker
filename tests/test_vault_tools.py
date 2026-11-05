import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp_endpoint import dispatch


ROOT = Path(__file__).resolve().parents[1]
VAULT_NAMES = {"vault_search", "vault_show", "vault_status"}


class VaultToolTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.binary = self.root / "vault-search"
        self.record = self.root / "calls.jsonl"
        self.config = self.root / "response.json"
        self.binary.write_text(f"#!{sys.executable}\n" + r"""import json
import os
from pathlib import Path
import sys
import time

root = Path(__file__).parent
with (root / 'calls.jsonl').open('a', encoding='utf-8') as stream:
    stream.write(json.dumps({'argv': sys.argv[1:], 'agent': os.environ.get('VAULT_SEARCH_AGENT'),
                             'session': os.environ.get('VAULT_SEARCH_SESSION_ID')}) + '\n')
config = json.loads((root / 'response.json').read_text(encoding='utf-8'))
time.sleep(config.get('delay', 0))
sys.stdout.write(config['output'])
sys.exit(config['exit'])
""", encoding="utf-8")
        self.binary.chmod(0o700)
        environment = patch.dict(os.environ, {
            "COLDLOCKER_VAULT_SEARCH": str(self.binary),
            "VAULT_SEARCH_AGENT": "inherited",
            "VAULT_SEARCH_SESSION_ID": "inherited-session",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.configure([])

    def configure(self, value, code=0, output=None, delay=0):
        self.config.write_text(json.dumps({"exit": code, "output": json.dumps(value) if output is None else output,
                                          "delay": delay}), encoding="utf-8")

    def call(self, name, arguments=None, client="unknown"):
        return dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": name, "arguments": {} if arguments is None else arguments}}, client=client)

    def calls(self):
        return [json.loads(line) for line in self.record.read_text(encoding="utf-8").splitlines()]

    def assert_value(self, response, value):
        self.assertEqual(response, {"jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": json.dumps(value)}], "isError": False}})

    def assert_tool_error(self, response, text="vault: unavailable"):
        self.assertEqual(response, {"jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": text}], "isError": True}})

    def test_search_argv_and_leading_dash_query(self):
        arguments = {"query": "--db $(literal); query", "area": "notes", "source": "chat", "project": "demo",
                     "since": "2026-01-01", "until": "2026-02-01", "author": "user", "authorship": "human",
                     "speaker": "assistant", "type": "note", "limit": "4"}
        self.assert_value(self.call("vault_search", arguments), [])
        self.assertEqual(self.calls()[0]["argv"], [
            "--json", "--area", "notes", "--source", "chat", "--project", "demo", "--since", "2026-01-01",
            "--until", "2026-02-01", "--author", "user", "--authorship", "human", "--speaker", "assistant",
            "--type", "note", "--limit", "4", "--", "--db $(literal); query"])

    def test_search_uses_path_by_default(self):
        with patch.dict(os.environ, {"PATH": str(self.root) + os.pathsep + os.environ.get("PATH", "")}):
            os.environ.pop("COLDLOCKER_VAULT_SEARCH")
            self.assert_value(self.call("vault_search", {"query": "snow"}), [])
        self.assertEqual(self.calls()[0]["argv"], ["--json", "--", "snow"])

    def test_show_argv(self):
        for selector, value, flag in (("lines", "2:5", "--lines"), ("block_id", "a-block", "--block")):
            with self.subTest(selector=selector):
                self.assert_value(self.call("vault_show", {"path": "Notes/a note.md", selector: value}), [])
                self.assertEqual(self.calls()[-1]["argv"], ["show", "--json", "Notes/a note.md", flag, value])

    def test_rejects_unknown_and_forbidden_arguments_without_running(self):
        for name, base in (("vault_search", {"query": "snow"}),
                           ("vault_show", {"path": "note.md", "lines": "1:2"}), ("vault_status", {})):
            for key in ("unknown", "db", "vault", "include-churn", "include-superseded", "context",
                        "include_churn", "include_superseded", "--db"):
                with self.subTest(name=name, key=key):
                    response = self.call(name, {**base, key: "forbidden"})
                    self.assertEqual(response["error"], {"code": -32602, "message": "Invalid tool or arguments"})
        self.assertFalse(self.record.exists())

    def test_rejects_non_string_values_without_coercion(self):
        fields = ("query", "area", "source", "project", "since", "until", "author", "authorship", "speaker", "type", "limit")
        for name, base, keys in (("vault_search", {"query": "snow"}, fields),
                                 ("vault_show", {"path": "note.md", "lines": "1:2"}, ("path", "lines")),
                                 ("vault_show", {"path": "note.md", "block_id": "a"}, ("block_id",))):
            for key in keys:
                for value in (None, False, 3, 1.5, [], {}):
                    with self.subTest(name=name, key=key, value=value):
                        response = self.call(name, {**base, key: value})
                        self.assertEqual(response["error"], {"code": -32602, "message": "Invalid tool or arguments"})
        self.assertFalse(self.record.exists())

    def test_rejects_non_object_arguments(self):
        for name in VAULT_NAMES:
            for arguments in ([], "text", 1, True):
                with self.subTest(name=name, arguments=arguments):
                    self.assertEqual(self.call(name, arguments)["error"]["code"], -32602)
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": name, "arguments": None}}
            self.assertEqual(dispatch(request)["error"]["code"], -32602)
        self.assertFalse(self.record.exists())

    def test_search_requires_query(self):
        self.assertEqual(self.call("vault_search")["error"]["code"], -32602)
        self.assertFalse(self.record.exists())

    def test_show_requires_path_and_exactly_one_selector(self):
        for arguments in ({}, {"lines": "1:2"}, {"block_id": "a"}, {"path": "note.md"},
                          {"path": "note.md", "lines": "1:2", "block_id": "a"}):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.call("vault_show", arguments)["error"]["code"], -32602)
        self.assertFalse(self.record.exists())

    def test_success_preserves_json_and_untrusted_warning_fields(self):
        values = [{"path": "note.md", "text": "untrusted passage", "untrusted": True,
                   "warning": {"message": "stale", "source": "import"}}]
        self.configure(values)
        self.assert_value(self.call("vault_search", {"query": "snow"}), values)
        self.assert_value(self.call("vault_show", {"path": "note.md", "block_id": "a"}), values)
        self.configure({"untrusted": False, "warning": "unchanged"})
        self.assert_value(self.call("vault_search", {"query": "snow"}), {"untrusted": False, "warning": "unchanged"})

    def test_exit_one_returns_empty_list(self):
        self.configure({"ignored": "no matches"}, code=1)
        for name, arguments in (("vault_search", {"query": "snow"}),
                               ("vault_show", {"path": "note.md", "lines": "1:2"}), ("vault_status", {})):
            with self.subTest(name=name):
                self.assert_value(self.call(name, arguments), [])

    def test_exit_two_returns_error_field_only(self):
        self.configure({"error": "invalid range", "details": "omit"}, code=2)
        self.assert_tool_error(self.call("vault_show", {"path": "note.md", "lines": "bad"}), "invalid range")
        self.assertEqual(len(self.calls()), 1)

    def test_other_exits_and_non_json_are_unavailable_without_retry(self):
        for code, output in ((3, "{}"), (-1, "{}"), (0, "not JSON"), (1, ""), (2, "bad"),
                             (2, "{}"), (2, '{"error": 3}')):
            with self.subTest(code=code, output=output):
                self.configure(None, code=code, output=output)
                self.assert_tool_error(self.call("vault_search", {"query": "snow"}))
        self.assertEqual(len(self.calls()), 7)

    def test_missing_binary_is_unavailable(self):
        with patch.dict(os.environ, {"COLDLOCKER_VAULT_SEARCH": str(self.root / "missing")}):
            self.assert_tool_error(self.call("vault_status"))

    def test_timeout_is_unavailable_without_retry(self):
        self.configure([], delay=2)
        with patch("mcp_endpoint.VAULT_TIMEOUT", 0.25):
            self.assert_tool_error(self.call("vault_search", {"query": "snow"}))
        self.assertEqual(len(self.calls()), 1)

    def test_status_filters_fields_and_argv(self):
        expected = {"complete": False, "last_indexed_at": "2026-11-01T00:00:00Z", "pending_files": 4,
                    "exclusions": ["cache"]}
        self.configure({**expected, "db": "private database", "last_receipt": "private receipt", "extra": "omit"})
        self.assert_value(self.call("vault_status"), expected)
        self.assertEqual(self.calls()[0]["argv"], ["status", "--json"])

    def test_malformed_status_is_unavailable(self):
        for value in ([], None, {"complete": True}):
            with self.subTest(value=value):
                self.configure(value)
                self.assert_tool_error(self.call("vault_status"))

    def test_agent_mapping_and_no_inherited_session(self):
        for client, expected in (("Claude Code", "claude"), ("my-CLAUDE-client", "claude"),
                                 ("Codex CLI", "codex"), ("CODEX", "codex"), ("cursor", "other"),
                                 ("unknown", "other"), (None, "other"), (12, "other")):
            with self.subTest(client=client):
                self.assert_value(self.call("vault_search", {"query": "snow"}, client), [])
                self.assertEqual(self.calls()[-1], {"argv": ["--json", "--", "snow"], "agent": expected,
                                                   "session": None})

    def test_initialize_client_reaches_subprocess_over_stdio(self):
        for client, expected in (("Claude Code", "claude"), ("Codex CLI", "codex"), ("editor", "other")):
            with self.subTest(client=client):
                requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "clientInfo": {"name": client, "version": "1"}}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                        "name": "vault_search", "arguments": {"query": "snow"}}}]
                process = subprocess.run([sys.executable, str(ROOT / "mcp_endpoint.py")],
                                         input="\n".join(json.dumps(request) for request in requests) + "\n",
                                         text=True, capture_output=True, timeout=15)
                self.assertEqual(process.returncode, 0, process.stderr)
                responses = [json.loads(line) for line in process.stdout.splitlines()]
                self.assertEqual([response["id"] for response in responses], [1, 2])
                self.assertFalse(responses[1]["result"]["isError"])
                self.assertEqual(self.calls()[-1]["agent"], expected)
                self.assertIsNone(self.calls()[-1]["session"])

    def test_tools_list_has_strict_read_only_schemas(self):
        response = dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = {tool["name"]: tool for tool in response["result"]["tools"]}
        self.assertTrue(VAULT_NAMES <= tools.keys())
        for name in VAULT_NAMES:
            with self.subTest(name=name):
                schema = tools[name]["inputSchema"]
                self.assertEqual(schema["type"], "object")
                self.assertFalse(schema["additionalProperties"])
                self.assertTrue(tools[name]["annotations"]["readOnlyHint"])
                self.assertTrue(all(prop == {"type": "string"} for prop in schema["properties"].values()))
        self.assertEqual(set(tools["vault_search"]["inputSchema"]["properties"]), {
            "query", "area", "source", "project", "since", "until", "author", "authorship", "speaker", "type", "limit"})
        self.assertEqual(tools["vault_search"]["inputSchema"]["required"], ["query"])
        self.assertEqual(set(tools["vault_show"]["inputSchema"]["properties"]), {"path", "lines", "block_id"})
        self.assertEqual(tools["vault_show"]["inputSchema"]["required"], ["path"])
        self.assertEqual(tools["vault_show"]["inputSchema"]["oneOf"], [
            {"required": ["lines"]}, {"required": ["block_id"]}])
        self.assertEqual(tools["vault_status"]["inputSchema"]["properties"], {})


if __name__ == "__main__":
    unittest.main()
