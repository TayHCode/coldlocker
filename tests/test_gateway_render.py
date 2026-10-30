import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from gateway_render import output_path, placeholder, render, write_output
from test_store import make_hub
from windows_text import windows_text_writes


class GatewayTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".scratch/test-root"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        make_hub(self.root)
        self.path = self.root / "mcp/servers/sample.json"

    def change(self, **values):
        server = json.loads(self.path.read_text(encoding='utf-8'))
        server.update(values)
        self.path.write_text(json.dumps(server), encoding='utf-8')

    def test_write_output_keeps_exact_bytes_with_windows_text_writes(self):
        target = self.root / "rendered.json"
        with windows_text_writes():
            write_output(target, '{\n  "a": 1\n}\n')
        self.assertEqual(target.read_bytes(), b'{\n  "a": 1\n}\n')

    def test_render_stdio_without_store(self):
        (self.root / "config.json").write_text("invalid", encoding="utf-8")
        server = render(self.root)["mcpServers"]["sample"]
        self.assertEqual(server["type"], "stdio")
        self.assertEqual(server["env"]["API_TOKEN"], placeholder("secret://sample-token"))
        self.assertEqual(server["env"]["LOG_LEVEL"], "info")
        self.assertFalse((self.root / ".hub-store").exists())

    def test_http_headers(self):
        self.change(transport="http", url="https://example.invalid/mcp", headers={"Authorization": "secret://sample-token"})
        server = render(self.root)["mcpServers"]["sample"]
        self.assertEqual(server["type"], "streamable-http")
        self.assertEqual(server["headers"]["Authorization"], placeholder("secret://sample-token"))

    def test_placeholder_names_do_not_collide(self):
        values = [placeholder("secret://" + n) for n in ("a-b", "a.b", "a_b")]
        self.assertEqual(len(set(values)), 3)
        self.assertTrue(all(v.startswith("${AGENTLOCKER_SECRET_") for v in values))

    def test_invalid_fragments_fail(self):
        cases = ({"transport": "other"}, {"args": [1]}, {"env": {"API_TOKEN": "inline"}},
                 {"env": {"LOG_LEVEL": "${UNEXPECTED}"}}, {"env": {"API_TOKEN": "secret://../bad"}},
                 {"transport": "http", "url": "https://example.invalid/?token=value"})
        for values in cases:
            make = json.loads(self.path.read_text(encoding='utf-8'))
            self.change(**values)
            with self.subTest(values=values), self.assertRaises(ValueError):
                render(self.root)
            self.path.write_text(json.dumps(make), encoding='utf-8')

    def test_tracked_output_refused_and_ignored_allowed(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "manifest.json"], cwd=self.root, check=True)
        with self.assertRaises(ValueError):
            output_path(self.root, self.root / "manifest.json")
        with self.assertRaises(ValueError):
            output_path(self.root, self.root / ".git/config")
        for directory in (".scratch", ".hub-store"):
            target = self.root / directory / "mcp_settings.json"
            self.assertEqual(output_path(self.root, target), target)

    def test_output_symlink_refused(self):
        target = self.root / "link"
        target.symlink_to(self.path)
        with self.assertRaises(ValueError):
            output_path(self.root, target)
