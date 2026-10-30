import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import sys
import unittest
from unittest.mock import patch

from checks import validators
import remote_publisher
from remote_publisher import ApiKv, CfKv, WranglerKv, build, publish
from store import FileStore


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "cf-" + "t" * 40

SKILL = "---\nname: demo-skill\ndescription: Summarises release notes\n---\n# Demo skill\nBody text.\n"
ROUTINE = {"id": "demo-routine", "description": "Weekly review", "owner": "agentlocker", "runner": "t3-scheduler",
           "schedule": {"type": "interval", "every_seconds": 3600}, "timezone": "UTC", "prompt": "prompt.md",
           "overlap": "skip", "timeout_seconds": 60, "enabled": False}
SERVER = {"name": "demo-server", "description": "Demo upstream", "transport": "http",
          "url": "https://mcp.example.com/mcp", "headers": {"Authorization": "secret://demo-token"}}


class FakeKv:
    def __init__(self):
        self.calls = []

    def put_many(self, pairs):
        self.calls.append(dict(pairs))


def entry(key, name, path, base):
    checksum = validators.digest(base / path)
    return {key: name, "path": path, "owner": "agentlocker",
            "provenance": {"source_id": "src-" + checksum[:12], "origin": "authored"}, "sha256": checksum}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def seal(base):
    manifest = {"schema_version": 1, "name": "agentlocker", "license": "MIT", "skills": [], "rules": [],
                "mcp_servers": [], "routines": []}
    for collection, key, pattern in (("skills", "name", "skills/{}"), ("rules", "name", "rules/{}.md"),
                                     ("mcp_servers", "name", "mcp/servers/{}.json"),
                                     ("routines", "id", "routines/{}/routine.json")):
        directory = {"skills": "skills", "rules": "rules", "mcp_servers": "mcp/servers", "routines": "routines"}[collection]
        names = sorted({p.name.removesuffix(".json").removesuffix(".md") for p in (base / directory).glob("*")}) \
            if (base / directory).is_dir() else []
        manifest[collection] = [entry(key, name, pattern.format(name), base) for name in names]
    write(base / "manifest.json", json.dumps(manifest))


def build_hub(base):
    write(base / "skills/demo-skill/SKILL.md", SKILL)
    write(base / "skills/demo-skill/references/guide.md", "# Guide\nMore detail.\n")
    write(base / "rules/shared-policy.md", "# Shared policy\nBe careful.\n")
    write(base / "mcp/servers/demo-server.json", json.dumps(SERVER))
    write(base / "routines/demo-routine/routine.json", json.dumps(ROUTINE))
    write(base / "routines/demo-routine/prompt.md", "Run the weekly review.\n")
    seal(base)


class PublisherCase(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".scratch/test-root"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        build_hub(self.root)

    def rewrite(self, relative, text):
        write(self.root / relative, text)
        seal(self.root)


class BuildTests(PublisherCase):
    def test_indexes_every_kind_with_files_hashes_and_descriptions(self):
        artifact = build(self.root)
        kinds = artifact.index["kinds"]
        self.assertEqual([e["name"] for e in kinds["skills"]], ["demo-skill"])
        skill = kinds["skills"][0]
        self.assertEqual(skill["description"], "Summarises release notes")
        self.assertEqual([f["path"] for f in skill["files"]],
                         ["skills/demo-skill/SKILL.md", "skills/demo-skill/references/guide.md"])
        self.assertEqual([f["sub"] for f in skill["files"]], ["SKILL.md", "references/guide.md"])
        for record in skill["files"]:
            self.assertEqual(artifact.blobs[record["sha256"]].encode().__len__(), record["size"])
        self.assertEqual([(f["path"], f["sub"]) for f in kinds["rules"][0]["files"]], [("rules/shared-policy.md", None)])
        routine = kinds["routines"][0]
        self.assertEqual(routine["id"], "demo-routine")
        self.assertEqual([f["path"] for f in routine["files"]],
                         ["routines/demo-routine/routine.json", "routines/demo-routine/prompt.md"])
        self.assertEqual(kinds["mcp_servers"][0]["server"]["headers"], {"Authorization": "secret://demo-token"})
        self.assertEqual(kinds["plugins"], [])
        self.assertEqual(kinds["connections"], [])
        self.assertEqual(artifact.index["schema"], 1)

    def test_revision_is_stable_and_changes_with_content(self):
        first = build(self.root).revision
        self.assertEqual(build(self.root).revision, first)
        self.rewrite("rules/shared-policy.md", "# Shared policy\nBe very careful.\n")
        self.assertNotEqual(build(self.root).revision, first)

    def add_local_rule(self):
        local = self.root / ".hub-store/catalog"
        write(local / "rules/private-rule.md", "# Private\nOnly mine.\n")
        write(local / "manifest.json", json.dumps({"schema_version": 1, "name": "agentlocker", "license": "MIT",
              "skills": [], "rules": [entry("name", "private-rule", "rules/private-rule.md", local)],
              "mcp_servers": [], "routines": []}))
        return local

    def test_default_build_excludes_the_local_catalog(self):
        self.add_local_rule()
        artifact = build(self.root)
        self.assertEqual([e["name"] for e in artifact.index["kinds"]["rules"]], ["shared-policy"])
        self.assertNotIn("Only mine.", "".join(artifact.blobs.values()))
        self.assertEqual(remote_publisher.local_items(artifact), [])

    def test_include_local_adds_items_with_their_store_prefixed_paths(self):
        self.add_local_rule()
        artifact = build(self.root, include_local=True)
        rules = artifact.index["kinds"]["rules"]
        self.assertEqual([(e["name"], e["path"]) for e in rules],
                         [("private-rule", ".hub-store/catalog/rules/private-rule.md"),
                          ("shared-policy", "rules/shared-policy.md")])
        self.assertEqual(rules[0]["files"][0]["path"], ".hub-store/catalog/rules/private-rule.md")
        self.assertIn("# Private\nOnly mine.\n", artifact.blobs.values())
        self.assertEqual(remote_publisher.local_items(artifact),
                         [("rules", "private-rule", ".hub-store/catalog/rules/private-rule.md")])

    def test_include_local_changes_the_revision(self):
        self.add_local_rule()
        self.assertNotEqual(build(self.root).revision, build(self.root, include_local=True).revision)

    def test_plugin_and_connection_metadata_are_included(self):
        write(self.root / "plugins/demo-plugin.json", json.dumps(
            {"name": "demo-plugin", "agent": "claude", "source": "https://plugins.example.com/demo",
             "version": "1.0.0", "enabled": True, "install_hint": "install it"}))
        write(self.root / "connections/demo-connection.json", json.dumps(
            {"name": "demo-connection", "provider": "demo", "endpoint": "https://connect.example.com/mcp",
             "auth_status": "needs-auth"}))
        manifest = json.loads((self.root / "manifest.json").read_text())
        manifest["plugins"] = [entry("name", "demo-plugin", "plugins/demo-plugin.json", self.root)]
        manifest["connections"] = [entry("name", "demo-connection", "connections/demo-connection.json", self.root)]
        write(self.root / "manifest.json", json.dumps(manifest))
        kinds = build(self.root).index["kinds"]
        self.assertEqual(kinds["plugins"][0]["metadata"]["version"], "1.0.0")
        self.assertEqual(kinds["connections"][0]["metadata"]["auth_status"], "needs-auth")

    def test_shipped_catalog_builds(self):
        artifact = build(ROOT)
        names = {e["name"] for e in artifact.index["kinds"]["skills"]}
        manifest = json.loads((ROOT / "manifest.json").read_text())
        self.assertEqual(names, {entry["name"] for entry in manifest["skills"]})
        self.assertEqual(artifact.index["kinds"]["routines"][0]["id"], "inventory-digest")


class RejectionTests(PublisherCase):
    def refuses(self, fragment, **options):
        with self.assertRaises(ValueError) as caught:
            build(self.root, **options)
        self.assertIn(fragment, str(caught.exception))
        return str(caught.exception)

    def test_hash_mismatch_is_refused(self):
        write(self.root / "rules/shared-policy.md", "# Shared policy\nTampered after sealing.\n")
        self.refuses("hash mismatch")

    def test_shipped_manifest_cannot_reference_the_local_store(self):
        local = self.root / ".hub-store/catalog"
        write(local / "rules/private-rule.md", "# Private\nLocal import.\n")
        manifest = json.loads((self.root / "manifest.json").read_text())
        private = entry("name", "private-rule", "rules/private-rule.md", local)
        private["path"] = ".hub-store/catalog/" + private["path"]
        manifest["rules"] = [private]
        write(self.root / "manifest.json", json.dumps(manifest))
        self.refuses("shipped manifest")

    def test_manifest_metadata_is_screened_without_echoing_secrets(self):
        token = "gh" + "p_" + "A" * 36
        manifest = json.loads((self.root / "manifest.json").read_text())
        manifest["skills"][0]["owner"] = token
        write(self.root / "manifest.json", json.dumps(manifest))
        message = self.refuses("github-token")
        self.assertNotIn(token, message)

    def test_support_filenames_are_screened_without_echoing_secrets(self):
        token = "gh" + "p_" + "A" * 36
        write(self.root / "skills/demo-skill/references" / (token + ".md"), "Reference text.\n")
        seal(self.root)
        message = self.refuses("github-token")
        self.assertNotIn(token, message)

    def test_omitted_filenames_are_also_screened(self):
        token = "gh" + "p_" + "A" * 36
        (self.root / "skills/demo-skill" / (token + ".bin")).write_bytes(b"\xff\xfe")
        seal(self.root)
        message = self.refuses("github-token")
        self.assertNotIn(token, message)

    def test_include_local_applies_the_same_secret_screening(self):
        token = "gh" + "p_" + "A" * 36
        local = self.root / ".hub-store/catalog"
        write(local / "rules/private-rule.md", f"# Private\nkey {token}\n")
        write(local / "manifest.json", json.dumps({"schema_version": 1, "name": "agentlocker", "license": "MIT",
              "skills": [], "rules": [entry("name", "private-rule", "rules/private-rule.md", local)],
              "mcp_servers": [], "routines": []}))
        build(self.root)
        message = self.refuses("github-token", include_local=True)
        self.assertNotIn(token, message)

    def test_include_local_screens_local_filenames_and_metadata(self):
        token = "gh" + "p_" + "A" * 36
        local = self.root / ".hub-store/catalog"
        write(local / "skills/private-skill/SKILL.md", "---\nname: private-skill\ndescription: Mine\n---\n# Mine\n")
        write(local / "skills/private-skill" / (token + ".md"), "Reference.\n")
        write(local / "manifest.json", json.dumps({"schema_version": 1, "name": "agentlocker", "license": "MIT",
              "skills": [entry("name", "private-skill", "skills/private-skill", local)], "rules": [],
              "mcp_servers": [], "routines": []}))
        message = self.refuses("github-token", include_local=True)
        self.assertNotIn(token, message)

    def test_symlink_inside_a_skill_is_refused(self):
        outside = self.root / "outside.md"
        outside.write_text("outside\n")
        (self.root / "skills/demo-skill/link.md").symlink_to(outside)
        seal(self.root)
        self.refuses("symlink")

    def test_symlinked_rule_file_is_refused(self):
        real = self.root / "real.md"
        real.write_text("# Rule\n")
        (self.root / "rules/shared-policy.md").unlink()
        (self.root / "rules/shared-policy.md").symlink_to(real)
        seal(self.root)
        self.refuses("symlink")

    def test_symlinked_catalog_directory_is_refused(self):
        shutil.move(str(self.root / "rules"), str(self.root / "elsewhere"))
        (self.root / "rules").symlink_to(self.root / "elsewhere")
        self.refuses("symlink")

    def test_secret_value_in_text_is_refused_without_echoing_it(self):
        token = "gh" + "p_" + "A" * 36
        self.rewrite("rules/shared-policy.md", f"# Policy\nuse {token}\n")
        message = self.refuses("shared-policy.md")
        self.assertNotIn(token, message)
        self.assertIn("github-token", message)

    def test_literal_credential_in_server_definition_is_refused(self):
        server = {**SERVER, "headers": {"Authorization": "Bearer " + "A" * 30}}
        self.rewrite("mcp/servers/demo-server.json", json.dumps(server))
        self.refuses("secret references")

    def test_machine_paths_in_text_are_refused(self):
        self.rewrite("skills/demo-skill/references/guide.md", "Open " + "/" + "home" + "/someone/notes\n")
        self.refuses("home-path")

    def test_absolute_command_and_argument_paths_in_server_are_refused(self):
        for server in ({"name": "demo-server", "description": "x", "transport": "stdio", "command": "/usr/bin/node"},
                       {"name": "demo-server", "description": "x", "transport": "stdio", "command": "node",
                        "args": ["/opt/tool/server.js"]},
                       {"name": "demo-server", "description": "x", "transport": "stdio", "command": "node",
                        "args": ["C:\\tool\\server.js"]}):
            with self.subTest(server=server):
                self.rewrite("mcp/servers/demo-server.json", json.dumps(server))
                self.refuses("absolute path")

    def test_server_name_must_match_its_manifest_entry(self):
        self.rewrite("mcp/servers/demo-server.json", json.dumps({**SERVER, "name": "other-server"}))
        self.refuses("does not match")

    def test_enabled_routine_is_refused(self):
        self.rewrite("routines/demo-routine/routine.json", json.dumps({**ROUTINE, "enabled": True}))
        self.refuses("disabled")

    def test_routine_prompt_must_stay_inside_its_directory(self):
        self.rewrite("routines/demo-routine/routine.json", json.dumps({**ROUTINE, "prompt": "../escape.md"}))
        self.refuses("relative POSIX")

    def test_secret_in_routine_prompt_is_refused(self):
        write(self.root / "routines/demo-routine/prompt.md", "token " + "sk-" + "A" * 30 + "\n")
        self.refuses("prompt.md")


class PublishTests(PublisherCase):
    def run_publish(self, kv, artifact=None, **options):
        output = io.StringIO()
        with redirect_stdout(output):
            publish(artifact or build(self.root), kv, **options)
        return output.getvalue()

    def test_dry_run_lists_everything_and_uploads_nothing(self):
        kv = FakeKv()
        text = self.run_publish(kv, dry_run=True)
        self.assertEqual(kv.calls, [])
        revision = build(self.root).revision
        for expected in ("Dry run", revision, "skills: demo-skill", "SKILL.md", "references/guide.md",
                         "rules: shared-policy", "routines: demo-routine", "mcp_servers: demo-server"):
            self.assertIn(expected, text)
        self.assertNotIn("Body text.", text)
        self.assertNotIn("Be careful.", text)

    def test_uploads_blobs_then_revision_then_current_pointer(self):
        kv = FakeKv()
        artifact = build(self.root)
        self.run_publish(kv, artifact=artifact)
        self.assertGreaterEqual(len(kv.calls), 3)
        self.assertEqual(list(kv.calls[-1]), ["current"])
        self.assertEqual(json.loads(kv.calls[-1]["current"]), {"revision": artifact.revision})
        self.assertEqual(list(kv.calls[-2]), ["rev:" + artifact.revision])
        self.assertEqual(json.loads(kv.calls[-2]["rev:" + artifact.revision]), artifact.index)
        blobs = {key: value for call in kv.calls[:-2] for key, value in call.items()}
        self.assertEqual(blobs, {"blob:" + digest: text for digest, text in artifact.blobs.items()})

    def test_large_uploads_are_split_into_bounded_batches(self):
        kv = FakeKv()
        self.rewrite("rules/shared-policy.md", "x" * 1000)
        with patch.object(remote_publisher, "BATCH_BYTES", 1500):
            self.run_publish(kv)
        self.assertGreater(len(kv.calls), 3)

    def test_failed_blob_upload_never_moves_the_pointer(self):
        class Failing(FakeKv):
            def put_many(self, pairs):
                if "current" in pairs:
                    self.calls.append(dict(pairs))
                else:
                    raise OSError("network")

        kv = Failing()
        with self.assertRaises(OSError):
            self.run_publish(kv)
        self.assertEqual(kv.calls, [])


class TransportTests(unittest.TestCase):
    def test_windows_npm_install_launches_javascript_through_node(self):
        with tempfile.TemporaryDirectory() as directory:
            remote = Path(directory) / "remote"
            entry = remote / "node_modules/wrangler/bin/wrangler.js"
            entry.parent.mkdir(parents=True)
            entry.write_text("process.exit(0);\n")
            shims = remote / "node_modules/.bin"
            shims.mkdir()
            (shims / "wrangler").write_text("#!/bin/sh\nexit 0\n")
            (shims / "wrangler.cmd").write_text("@echo off\r\n")
            with patch.object(remote_publisher.shutil, "which", side_effect=lambda name: "fixture-node" if name == "node" else None):
                selected = remote_publisher.resolve_wrangler(remote, platform="win32")
            self.assertEqual(selected, ["fixture-node", str(entry)])
            calls = []
            def runner(command, **options):
                calls.append(command)
                return argparse.Namespace(returncode=0)
            WranglerKv("space", None, remote, Path(directory) / ".scratch",
                       runner=runner, executable=selected).put_many({"k": "v"})
            self.assertEqual(calls[0][:6], ["fixture-node", str(entry), "kv", "bulk", "put", calls[0][5]])
            self.assertNotIn(str(shims / "wrangler"), calls[0])

    def test_api_transport_sends_bearer_token_and_bulk_body(self):
        seen = {}

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b'{"success": true, "result": {"successful_key_count": 1}}'

        def opener(request, timeout=None):
            seen["url"] = request.full_url
            seen["method"] = request.get_method()
            seen["auth"] = request.get_header("Authorization")
            seen["body"] = json.loads(request.data)
            return Response()

        ApiKv("acct", "space", TOKEN, opener=opener).put_many({"k": "v"})
        self.assertEqual(seen["url"], "https://api.cloudflare.com/client/v4/accounts/acct/storage/kv/namespaces/space/bulk")
        self.assertEqual(seen["method"], "PUT")
        self.assertEqual(seen["auth"], "Bearer " + TOKEN)
        self.assertEqual(seen["body"], [{"key": "k", "value": "v"}])

    def test_api_failure_does_not_leak_the_token(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b'{"success": false, "errors": [{"message": "denied"}]}'

        with self.assertRaises(OSError) as caught:
            ApiKv("acct", "space", TOKEN, opener=lambda request, timeout=None: Response()).put_many({"k": "v"})
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_cf_transport_passes_the_batch_by_file_and_removes_it(self):
        scratch = ROOT / ".scratch/test-root"
        scratch.mkdir(parents=True, exist_ok=True)
        recorded = {}

        def runner(command, **options):
            body = command[command.index("--body") + 1]
            self.assertTrue(body.startswith("@"))
            recorded["command"] = command
            recorded["payload"] = json.loads(Path(body[1:]).read_text())
            recorded["path"] = Path(body[1:])
            recorded["mode"] = recorded["path"].stat().st_mode & 0o777
            return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            previous_umask = os.umask(0o022)
            try:
                CfKv("space", "personal", Path(directory), runner=runner).put_many({"k": "v"})
            finally:
                os.umask(previous_umask)
            if os.name != "nt":
                self.assertEqual(recorded["mode"], 0o600)
            self.assertEqual(recorded["payload"], [{"key": "k", "value": "v"}])
            self.assertFalse(recorded["path"].exists())
        self.assertEqual(recorded["command"][:5], ["cf", "--profile", "personal", "kv", "bulk"])

    def test_cf_failure_reports_without_command_output(self):
        def runner(command, **options):
            return type("Done", (), {"returncode": 1, "stdout": "id " + "a" * 32, "stderr": "boom " + "b" * 32})()

        scratch = ROOT / ".scratch/test-root"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            with self.assertRaises(OSError) as caught:
                CfKv("space", None, Path(directory), runner=runner).put_many({"k": "v"})
        self.assertNotIn("a" * 32, str(caught.exception))
        self.assertNotIn("b" * 32, str(caught.exception))

    def test_wrangler_transport_uses_installed_binary_and_private_batch_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote = root / "remote"
            remote.mkdir()
            scratch = root / ".scratch"
            recorded = {}

            def runner(command, **options):
                payload = Path(command[command.index("put") + 1])
                recorded.update(command=command, options=options, payload=payload,
                                content=json.loads(payload.read_text()), mode=payload.stat().st_mode & 0o777)
                return type("Done", (), {"returncode": 0})()

            WranglerKv("namespace-1", "profile", remote, scratch, runner=runner,
                       executable="/installed/wrangler").put_many({"k": "v"})
            self.assertEqual(recorded["content"], [{"key": "k", "value": "v"}])
            if os.name != "nt":
                self.assertEqual(recorded["mode"], 0o600)
            self.assertFalse(recorded["payload"].exists())
            command = recorded["command"]
            self.assertEqual(command[:6], ["/installed/wrangler", "--profile", "profile", "kv", "bulk", "put"])
            self.assertEqual(command[7:], ["--namespace-id=namespace-1", "--remote", "--config", "wrangler.local.jsonc"])
            self.assertEqual(recorded["options"]["cwd"], remote)
            self.assertNotIn("capture_output", recorded["options"])

    def test_wrangler_failure_is_redacted_and_cleans_batch_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote = root / "remote"
            remote.mkdir()
            scratch = root / ".scratch"
            seen = {}

            def runner(command, **options):
                seen["payload"] = Path(command[6])
                return type("Done", (), {"returncode": 7, "stdout": "private output", "stderr": "auth response"})()

            with self.assertRaises(ValueError) as caught:
                WranglerKv("space", None, remote, scratch, runner=runner,
                           executable="/installed/wrangler").put_many({"k": "v"})
            self.assertIn("exit 7", str(caught.exception))
            self.assertNotIn("private output", str(caught.exception))
            self.assertNotIn("auth response", str(caught.exception))
            self.assertFalse(seen["payload"].exists())

    def test_wrangler_missing_executable_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            remote = Path(directory) / "remote"
            remote.mkdir()
            with patch.object(remote_publisher, "resolve_wrangler", return_value=None), self.assertRaises(ValueError) as caught:
                WranglerKv("space", None, remote, Path(directory) / ".scratch", executable=None)
            self.assertIn("install cf or Wrangler", str(caught.exception))
            self.assertIn("API token", str(caught.exception))

    def test_wrangler_timeout_is_redacted_and_cleans_private_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seen = []
            def runner(command, **options):
                seen.append(Path(command[4]))
                raise remote_publisher.subprocess.TimeoutExpired(command, 300, output="fixture-private-output")
            with self.assertRaises(ValueError) as caught:
                WranglerKv("space", None, root / "remote", root / ".scratch", runner=runner,
                           executable="fixture-wrangler").put_many({"k": "v"})
            self.assertIn("check remote state before retrying", str(caught.exception))
            self.assertNotIn("fixture-private-output", str(caught.exception))
            self.assertFalse(seen[0].exists())

    def test_wrangler_namespace_is_passed_as_one_option_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "remote").mkdir()
            (root / ".hub-store").mkdir()
            write(root / ".hub-store/remote.json", json.dumps({"catalog_namespace_id": "--remote"}))
            with self.assertRaises(ValueError) as caught:
                remote_publisher.transport(root, argparse.Namespace(config=None, via="wrangler"))
            self.assertIn("invalid catalog_namespace_id", str(caught.exception))


class CommandTests(PublisherCase):
    def test_redacted_wrangler_failure_reaches_the_cli(self):
        import hub
        kv = WranglerKv("space", None, self.root / "remote", self.root / ".scratch",
                        runner=lambda *args, **kwargs: argparse.Namespace(returncode=7),
                        executable="fixture-wrangler")
        errors = io.StringIO()
        with patch.object(hub, "ROOT", self.root), \
             patch.object(sys, "argv", ["hub.py", "publish-remote", "--via", "wrangler", "--yes"]), \
             patch.object(remote_publisher, "transport", return_value=kv), \
             redirect_stdout(io.StringIO()), redirect_stderr(errors):
            self.assertEqual(hub.main(), 1)
        self.assertIn("Wrangler", errors.getvalue())
        self.assertIn("exit 7", errors.getvalue())
        self.assertNotIn("invalid input, unsafe destination", errors.getvalue())

    def test_nonordinary_local_manifest_refuses_before_any_transport(self):
        self.local_hub()
        manifest = self.root / ".hub-store/catalog/manifest.json"
        manifest.rename(manifest.with_suffix(".preserved"))
        manifest.mkdir()
        for flags in ({}, {"shipped_only": True}, {"include_local": True}):
            with self.subTest(flags=flags), patch.object(remote_publisher, "transport", side_effect=AssertionError("transport called")):
                with self.assertRaises(ValueError):
                    self.run_command(yes=True, **flags)
        if hasattr(os, "mkfifo"):
            manifest.rmdir()
            os.mkfifo(manifest)
            with patch.object(remote_publisher, "transport", side_effect=AssertionError("transport called")):
                with self.assertRaises(ValueError):
                    self.run_command(yes=True)

    def test_missing_cli_tool_error_reaches_the_user_without_raw_exception(self):
        import hub
        def missing(*args, **kwargs):
            raise FileNotFoundError("fixture-private-path")
        write(self.root / ".hub-store/remote.json", json.dumps({"catalog_namespace_id": "space"}))
        for via, factory in (("cf", lambda: CfKv("space", None, self.root / ".scratch")),
                             ("wrangler", lambda: WranglerKv("space", None, self.root / "remote",
                                                             self.root / ".scratch", executable="fixture-wrangler"))):
            with self.subTest(via=via):
                kv = factory()
                kv.runner = missing
                errors = io.StringIO()
                with patch.object(hub, "ROOT", self.root), \
                     patch.object(sys, "argv", ["hub.py", "publish-remote", "--via", via, "--yes"]), \
                     patch.object(remote_publisher, "transport", return_value=kv), \
                     redirect_stdout(io.StringIO()), redirect_stderr(errors):
                    self.assertEqual(hub.main(), 1)
                self.assertIn("install cf or Wrangler", errors.getvalue())
                self.assertIn("API token", errors.getvalue())
                self.assertNotIn("fixture-private-path", errors.getvalue())

    def args(self, **overrides):
        values = {"dry_run": False, "include_local": False, "shipped_only": False,
                  "yes": False, "via": None, "config": None}
        values.update(overrides)
        return argparse.Namespace(**values)

    def run_command(self, **overrides):
        output = io.StringIO()
        with redirect_stdout(output):
            code = remote_publisher.run(self.args(**overrides), root=self.root)
        return code, output.getvalue()

    def test_register_adds_publish_remote_to_the_cli(self):
        from hub import parser
        parsed = parser().parse_args(["publish-remote", "--dry-run"])
        self.assertTrue(parsed.dry_run)
        self.assertIs(parsed.action, remote_publisher.run)
        self.assertEqual(parser().parse_args(["publish-remote", "--via", "wrangler"]).via, "wrangler")

    def test_selection_flags_are_mutually_exclusive(self):
        from hub import parser
        with self.assertRaises(SystemExit):
            parser().parse_args(["publish-remote", "--include-local", "--shipped-only"])

    def test_dry_run_needs_no_configuration_or_credentials(self):
        code, text = self.run_command(dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("Dry run", text)

    def test_publishing_without_confirmation_is_refused(self):
        with patch("sys.stdin.isatty", return_value=False):
            with self.assertRaises(ValueError) as caught:
                self.run_command()
        self.assertIn("--yes", str(caught.exception))

    def local_hub(self):
        local = self.root / ".hub-store/catalog"
        write(local / "rules/private-rule.md", "# Private\nOnly mine.\n")
        write(local / "manifest.json", json.dumps({"schema_version": 1, "name": "agentlocker", "license": "MIT",
              "skills": [], "rules": [entry("name", "private-rule", "rules/private-rule.md", local)],
              "mcp_servers": [], "routines": []}))
        write(self.root / ".hub-store/remote.json", json.dumps({"catalog_namespace_id": "space"}))

    def uploaded(self, **overrides):
        overrides.setdefault("via", "cf")
        created = {}

        class Recorder(FakeKv):
            def __init__(self, namespace, profile, scratch, **options):
                super().__init__()
                created["kv"] = self

        with patch.object(remote_publisher, "CfKv", Recorder):
            code, text = self.run_command(**overrides)
        return code, text, created.get("kv")

    def test_local_items_without_selection_are_refused_before_transport(self):
        self.local_hub()
        with patch.object(remote_publisher, "transport", side_effect=AssertionError("transport called")):
            with self.assertRaises(ValueError) as caught:
                self.run_command(yes=True)
        self.assertIn("1 local catalog item", str(caught.exception))
        self.assertIn("--include-local", str(caught.exception))
        self.assertIn("--shipped-only", str(caught.exception))
        self.assertNotIn("private-rule", str(caught.exception))
        self.assertNotIn(".hub-store", str(caught.exception))

    def test_ambiguous_dry_run_is_refused_with_count_only(self):
        self.local_hub()
        with self.assertRaises(ValueError) as caught:
            self.run_command(dry_run=True)
        self.assertIn("1 local catalog item", str(caught.exception))
        self.assertNotIn("private-rule", str(caught.exception))
        self.assertNotIn(".hub-store", str(caught.exception))

    def test_shipped_only_explicitly_reports_omission_count(self):
        self.local_hub()
        code, text, kv = self.uploaded(yes=True, shipped_only=True)
        self.assertEqual(code, 0)
        self.assertIn("--shipped-only selection; omitting 1 local catalog item", text)
        self.assertNotIn("private-rule", text)
        self.assertNotIn("# Private\nOnly mine.\n", [value for call in kv.calls for value in call.values()])

    def test_no_local_items_still_publish_by_default(self):
        write(self.root / ".hub-store/remote.json", json.dumps({"catalog_namespace_id": "space"}))
        code, text, kv = self.uploaded(yes=True)
        self.assertEqual(code, 0)
        self.assertIsNotNone(kv)
        self.assertNotIn("shipped-only", text)

    def test_include_local_with_yes_lists_and_uploads_the_local_items(self):
        self.local_hub()
        code, text, kv = self.uploaded(yes=True, include_local=True)
        self.assertEqual(code, 0)
        self.assertIn("rules/private-rule  .hub-store/catalog/rules/private-rule.md", text)
        self.assertIn("# Private\nOnly mine.\n", [value for call in kv.calls for value in call.values()])

    def test_include_local_dry_run_lists_the_items_and_uploads_nothing(self):
        self.local_hub()
        code, text = self.run_command(dry_run=True, include_local=True)
        self.assertEqual(code, 0)
        self.assertIn("Local catalog items included (1)", text)
        self.assertIn("rules/private-rule", text)

    def test_include_local_keeps_strict_validation_for_malformed_manifest(self):
        self.local_hub()
        write(self.root / ".hub-store/catalog/manifest.json", '{"schema_version": 1, "private": "never echo"}')
        with self.assertRaises(ValueError) as caught:
            self.run_command(include_local=True, dry_run=True)
        self.assertNotIn("never echo", str(caught.exception))

    def test_malformed_local_catalog_fails_closed_when_counting_for_selection(self):
        self.local_hub()
        write(self.root / ".hub-store/catalog/manifest.json", '{"schema_version": 1, "private": "never echo"}')
        with self.assertRaises(ValueError) as caught:
            self.run_command(yes=True)
        self.assertNotIn("never echo", str(caught.exception))

    def test_include_local_without_yes_or_a_terminal_is_refused(self):
        self.local_hub()
        with patch("sys.stdin.isatty", return_value=False):
            with self.assertRaises(ValueError) as caught:
                self.run_command(include_local=True)
        self.assertIn("--yes", str(caught.exception))

    def test_include_local_prompts_with_the_list_and_cancels_on_no(self):
        self.local_hub()
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="n"):
            code, text = self.run_command(include_local=True)
        self.assertEqual(code, 1)
        self.assertIn("rules/private-rule  .hub-store/catalog/rules/private-rule.md", text)
        self.assertIn("readable by every client you authorize", text)
        self.assertIn("nothing was uploaded", text)

    def test_missing_configuration_names_the_file_to_create(self):
        with self.assertRaises(ValueError) as caught:
            self.run_command(yes=True)
        self.assertIn("remote.json", str(caught.exception))

    def test_publishes_through_the_api_transport_with_the_stored_token(self):
        FileStore(self.root).put("cloudflare-api-token", TOKEN)
        write(self.root / ".hub-store/remote.json",
              json.dumps({"account_id": "acct", "catalog_namespace_id": "space"}))
        created = {}

        class Recorder(FakeKv):
            def __init__(self, account, namespace, token, **options):
                super().__init__()
                created.update(account=account, namespace=namespace, token=token, kv=self)

        with patch.object(remote_publisher, "ApiKv", Recorder):
            code, text = self.run_command(yes=True, via="api")
        self.assertEqual(code, 0)
        self.assertEqual((created["account"], created["namespace"], created["token"]), ("acct", "space", TOKEN))
        self.assertEqual(list(created["kv"].calls[-1]), ["current"])
        self.assertNotIn(TOKEN, text)

    def test_publishes_through_cf_with_the_configured_profile(self):
        write(self.root / ".hub-store/remote.json",
              json.dumps({"catalog_namespace_id": "space", "cf_profile": "personal"}))
        created = {}

        class Recorder(FakeKv):
            def __init__(self, namespace, profile, scratch, **options):
                super().__init__()
                created.update(namespace=namespace, profile=profile, kv=self)

        with patch.object(remote_publisher, "CfKv", Recorder):
            code, _ = self.run_command(yes=True, via="cf")
        self.assertEqual(code, 0)
        self.assertEqual((created["namespace"], created["profile"]), ("space", "personal"))

    def test_api_transport_requires_the_stored_token(self):
        write(self.root / ".hub-store/remote.json",
              json.dumps({"account_id": "acct", "catalog_namespace_id": "space"}))
        with self.assertRaises(ValueError) as caught:
            self.run_command(yes=True, via="api")
        self.assertIn("cloudflare-api-token", str(caught.exception))

    def test_automatic_transport_prefers_api_then_cf_then_wrangler(self):
        write(self.root / ".hub-store/remote.json", json.dumps({"catalog_namespace_id": "space",
                                                                  "account_id": "account"}))
        with patch("remote_publisher.shutil.which", side_effect=lambda name: "/bin/cf" if name == "cf" else None), \
             patch.object(remote_publisher, "resolve_wrangler", return_value="/repo/remote/node_modules/.bin/wrangler"):
            self.assertIsInstance(remote_publisher.transport(self.root, self.args()), CfKv)
        with patch("remote_publisher.shutil.which", return_value=None), \
             patch.object(remote_publisher, "resolve_wrangler", return_value="/repo/remote/node_modules/.bin/wrangler"):
            selected = remote_publisher.transport(self.root, self.args())
            self.assertIsInstance(selected, WranglerKv)
            self.assertEqual(selected.executable, "/repo/remote/node_modules/.bin/wrangler")
        FileStore(self.root).put("cloudflare-api-token", TOKEN)
        with patch("remote_publisher.shutil.which", return_value="/bin/cf"), \
             patch.object(remote_publisher, "resolve_wrangler", return_value="/repo/remote/node_modules/.bin/wrangler"):
            selected = remote_publisher.transport(self.root, self.args())
            self.assertIsInstance(selected, ApiKv)

    def test_no_automatic_transport_has_actionable_error(self):
        write(self.root / ".hub-store/remote.json", json.dumps({"catalog_namespace_id": "space"}))
        with patch("remote_publisher.shutil.which", return_value=None), \
             patch.object(remote_publisher, "resolve_wrangler", return_value=None):
            with self.assertRaises(ValueError) as caught:
                remote_publisher.transport(self.root, self.args())
        self.assertIn("install cf or Wrangler", str(caught.exception))
        self.assertIn("API token", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
