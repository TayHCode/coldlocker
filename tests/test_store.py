import json
from pathlib import Path
import tempfile
import unittest

import modes
from windows_text import windows_text_writes
from store import FileStore, reference_name


def make_hub(root):
    manifest = {"schema_version": 1}
    paths = {"skills": "skills/sample", "rules": "rules/sample.md",
             "mcp_servers": "mcp/servers/sample.json", "routines": "routines/sample"}
    for kind, path in paths.items():
        entry = {"id" if kind == "routines" else "name": "sample", "path": path,
                 "owner": "agentlocker", "provenance": {"source_id": "src-" + "a" * 12,
                 "origin": "authored"}, "sha256": "a" * 64}
        manifest[kind] = [entry]
        target = root / path
        if kind == "skills":
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text('---\ndescription: Sample skill\n---\nBody\n', encoding='utf-8')
        elif kind == "routines":
            target.mkdir(parents=True)
            (target / "routine.json").write_text(json.dumps({"id": "sample", "description": "Sample routine", "enabled": False}), encoding="utf-8")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            if kind == "rules":
                target.write_text("Sample rule\n", encoding="utf-8")
            else:
                target.write_text(json.dumps({"name": "sample", "description": "Sample server", "transport": "stdio",
                    "command": "example", "args": [], "env": {"API_TOKEN": "secret://sample-token", "LOG_LEVEL": "info"}}), encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


class StoreTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".scratch/test-root"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = FileStore(self.root)

    def test_roundtrip_permissions_and_replace(self):
        self.store.put("sample-token", "first")
        self.store.put("sample-token", "second")
        self.assertEqual(self.store.resolve("secret://sample-token"), "second")
        self.assertEqual(self.store.names(), ["sample-token"])
        for path in (self.store.path, self.store.secrets):
            modes.check(self, path, 0o700)
        modes.check(self, self.store.secrets / "sample-token", 0o600)
        self.assertEqual(len(list(self.store.secrets.iterdir())), 1)

    def test_secret_value_keeps_its_exact_bytes_with_windows_text_writes(self):
        with windows_text_writes():
            self.store.put("sample-token", "line one\nline two\n")
        self.assertEqual((self.store.secrets / "sample-token").read_bytes(), b"line one\nline two\n")

    def test_delete_and_missing(self):
        self.assertFalse(self.store.has("sample"))
        self.store.delete("sample")
        self.store.put("sample", "value")
        self.store.delete("sample")
        self.assertEqual(self.store.names(), [])
        with self.assertRaises(OSError):
            self.store.resolve("secret://sample")

    def test_names_and_references(self):
        for name in ("", "../escape", "Upper", "a" * 64, "a/b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.store.put(name, "value")
        for ref in ("sample", "secret://../sample", "secret://"):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                reference_name(ref)
        self.store.put("valid._-0", "value")
        self.assertTrue(self.store.has("valid._-0"))

    def test_redaction_longest_first(self):
        self.store.put("a", "example")
        self.store.put("b", "example-long")
        self.assertEqual(self.store.redact("example-long example"), "[REDACTED] [REDACTED]")

    def test_custom_relative_store(self):
        (self.root / "config.json").write_text(json.dumps({"store_path": "local-state"}), encoding="utf-8")
        store = FileStore(self.root)
        store.put("sample", "value")
        self.assertEqual(store.path, self.root / "local-state")

    def test_no_initial_write(self):
        self.assertFalse(self.store.path.exists())
        self.assertEqual(self.store.names(), [])
        self.assertFalse(self.store.path.exists())

    def test_invalid_config_and_empty_value(self):
        (self.root / "config.json").write_text('{"store_path": 3}', encoding='utf-8')
        with self.assertRaises(ValueError):
            FileStore(self.root)
        with self.assertRaises(ValueError):
            self.store.put("sample", "")

    def test_store_symlinks_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.store.path.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.store.put("sample", "value")
        self.assertEqual(list(outside.iterdir()), [])

    def test_secret_symlinks_refused(self):
        self.store.put("sample", "value")
        (self.store.secrets / "link").symlink_to(self.store.secrets / "sample")
        with self.assertRaises(ValueError):
            self.store.resolve("secret://link")
        self.assertEqual(self.store.names(), ["sample"])
