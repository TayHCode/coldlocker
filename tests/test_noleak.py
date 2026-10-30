import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import unittest

import modes
from test_store import make_hub


ROOT = Path(__file__).resolve().parents[1]


class NoLeakTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for filename in ("store.py", "calllog.py", "portable.py", "mcp_endpoint.py", "gateway_render.py", ".gitignore"):
            shutil.copyfile(ROOT / filename, self.root / filename)
        make_hub(self.root)
        self.canary = "canary-" + secrets.token_hex(24)
        result = self.run_cli("store.py", "set", "sample-token", data=self.canary + "\n")
        self.assertEqual(result.returncode, 0)

    def run_cli(self, script, *args, data=""):
        result = subprocess.run([sys.executable, script, *args], input=data,
                                text=True, capture_output=True, cwd=self.root, encoding='utf-8')
        self.assertNotIn(self.canary, result.stdout)
        self.assertNotIn(self.canary, result.stderr)
        return result

    def test_store_every_command_and_errors(self):
        for arguments, status in ((("has", "sample-token"), 0), (("has", "missing"), 1),
                                  (("list",), 0), (("path",), 0), (("delete", "missing"), 0),
                                  (("set", "../bad"), 1), (("set", "empty"), 1),
                                  (("delete", "../bad"), 1), (("has", "../bad"), 1),
                                  (("list", "extra"), 1), (("set",), 1),
                                  (("unknown", self.canary), 2),
                                  (("set", "sample-token", self.canary), 2)):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.run_cli("store.py", *arguments).returncode, status)
        self.assertEqual(self.run_cli("store.py", "delete", "sample-token").returncode, 0)
        (self.root / "config.json").write_text("invalid " + self.canary, encoding="utf-8")
        self.assertEqual(self.run_cli("store.py", "path").returncode, 1)

    def test_endpoint_every_tool_and_malformed_input(self):
        requests = [{"jsonrpc": "2.0", "id": index, "method": "tools/call", "params": {"name": "hub_list_" + kind, "arguments": {}}}
                    for index, kind in enumerate(("skills", "rules", "mcp_servers", "routines"))]
        requests += [{"jsonrpc": "2.0", "id": 10, "method": "initialize"},
                     {"jsonrpc": "2.0", "method": "notifications/initialized"},
                     {"jsonrpc": "2.0", "id": 11, "method": "ping"},
                     {"jsonrpc": "2.0", "id": 12, "method": "tools/list"},
                     {"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": {"name": self.canary}}]
        data = "\n".join(json.dumps(r) for r in requests) + "\nmalformed " + self.canary + "\n"
        result = self.run_cli("mcp_endpoint.py", data=data)
        self.assertEqual(result.returncode, 0)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(responses), len(requests))
        self.assertTrue(all("result" in r for r in responses[:4]))
        self.assertIn("secret://sample-token", result.stdout)
        self.assertEqual(responses[-1]["error"]["code"], -32700)

    def test_renderer_stdout_file_and_error(self):
        result = self.run_cli("gateway_render.py")
        self.assertEqual(result.returncode, 0)
        self.assertIn("${AGENTLOCKER_SECRET_", result.stdout)
        destination = self.root / ".scratch"
        destination.mkdir()
        self.assertEqual(self.run_cli("gateway_render.py", "--out", str(destination / "render.json")).returncode, 0)
        self.assertNotIn(self.canary, (destination / "render.json").read_text(encoding="utf-8"))
        self.assertEqual(self.run_cli("gateway_render.py", "--unknown", self.canary).returncode, 2)
        fragment = self.root / "mcp/servers/sample.json"
        server = json.loads(fragment.read_text(encoding='utf-8'))
        server["env"]["API_TOKEN"] = self.canary
        fragment.write_text(json.dumps(server), encoding='utf-8')
        self.assertEqual(self.run_cli("gateway_render.py").returncode, 1)
        fragment.write_text("broken " + self.canary, encoding="utf-8")
        self.assertEqual(self.run_cli("gateway_render.py").returncode, 1)

    def test_permissions_and_gitignore(self):
        store = self.root / ".hub-store"
        for path, mode in ((store, 0o700), (store / "secrets", 0o700),
                           (store / "secrets/sample-token", 0o600)):
            modes.check(self, path, mode)
        (self.root / "config.json").write_text(json.dumps({"store_path": ".hub-store"}), encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        files = subprocess.run(["git", "ls-files"], cwd=self.root, check=True, capture_output=True, text=True, encoding="utf-8").stdout.splitlines()
        self.assertNotIn("config.json", files)
        self.assertFalse(any(name.startswith(".hub-store/") for name in files))
        self.assertNotIn(self.canary, "\n".join((self.root / name).read_text(encoding="utf-8") for name in files))
        for name in ("config.json", ".hub-store/secrets/sample-token"):
            checked = subprocess.run(["git", "check-ignore", name], cwd=self.root, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(checked.returncode, 0)
            self.assertEqual(checked.stdout.strip(), name)
