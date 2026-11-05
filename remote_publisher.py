from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

from checks import validators
from checks.scan import findings
import mcp_endpoint
from store import FileStore, ordinary_path


SCHEMA = 1
MAX_FILE_BYTES = mcp_endpoint.MAX_ITEM_BYTES
BATCH_BYTES = 50 * 1024 * 1024
BATCH_KEYS = 10000
TOKEN_SECRET = "cloudflare-api-token"
CONFIG_FILE = "remote.json"
KINDS = tuple(validators.COLLECTIONS)
SERVER_FIELDS = ("transport", "command", "args", "url", "env", "headers")
ABSOLUTE = re.compile(r"(?:/|~|[A-Za-z]:[\\/]|\\\\)")


@dataclass
class Artifact:
    revision: str
    index: dict
    blobs: dict


def load_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            validators.require(key not in result, "duplicate JSON field")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def inside_root(root, relative):
    path = Path(root)
    for part in Path(relative).parts:
        path = path / part
        validators.require(not path.is_symlink(), f"symlink in catalog path: {relative}")
    validators.require(path.exists(), f"missing catalog content: {relative}")
    return path


def entry_files(directory):
    files, excluded = [], []
    for item in sorted(directory.rglob("*")):
        relative = item.relative_to(directory)
        screen("catalog filename", None, relative.as_posix())
        if ".git" in relative.parts:
            if item.name == ".git":
                excluded.append(relative.as_posix())
            continue
        validators.require(not item.is_symlink(), f"symlink inside catalog item: {relative.as_posix()}")
        if item.is_dir():
            continue
        validators.require(item.is_file(), f"special file inside catalog item: {relative.as_posix()}")
        files.append((relative.as_posix(), item))
    return files, excluded


def screen(label, path, text):
    problems = findings(text, path)
    validators.require(not problems, "refusing " + label + ": " + ", ".join(
        f"{category} at line {number}" for number, category in problems))


def screen_metadata(value):
    if isinstance(value, dict):
        for key, item in value.items():
            screen_metadata(key)
            screen_metadata(item)
    elif isinstance(value, list):
        for item in value:
            screen_metadata(item)
    elif isinstance(value, str):
        screen("catalog metadata", None, value)


def classify(data):
    if len(data) > MAX_FILE_BYTES:
        return None, ("size-cap" if mcp_endpoint.looks_like_text(data[:mcp_endpoint.SNIFF_BYTES]) else "non-text")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, "non-text"
    if "\0" in text.replace("\r\n", "\n").replace("\r", "\n"):
        return None, "non-text"
    return text, None


def collect(label, files, blobs, base, subs=True, excluded=()):
    records = []
    omitted = [{"path": base + "/" + relative, "reason": "excluded"} for relative in excluded]
    for relative, path in files:
        data = path.read_bytes()
        full = base + "/" + relative if subs else base
        text, reason = classify(data)
        if text is None:
            omitted.append({"path": full, "size": len(data), "reason": reason})
            continue
        screen(f"{label}/{relative}", full, text)
        checksum = hashlib.sha256(data).hexdigest()
        blobs[checksum] = text
        records.append({"path": full, "sub": relative if subs else None, "sha256": checksum, "size": len(data)})
    return records, sorted(omitted, key=lambda item: item["path"])


def absolute_literal(value):
    if not isinstance(value, str) or value.startswith("secret://"):
        return False
    return bool(ABSOLUTE.match(value.partition("=")[2] if value.startswith("-") and "=" in value else value))


def server_entry(definition, name):
    validators.mcp(definition)
    validators.require(definition["name"] == name, "server name does not match its manifest entry")
    literals = [definition.get("command"), *definition.get("args", []),
                *definition.get("env", {}).values(), *definition.get("headers", {}).values()]
    validators.require(not any(absolute_literal(value) for value in literals),
                       f"refusing {name}: absolute path in server definition")
    return {field: definition[field] for field in SERVER_FIELDS if field in definition}


def build_entry(root, collection, key, entry, blobs):
    name = entry[key]
    path = entry["path"]
    target = inside_root(root, path)
    validators.require(validators.digest(target) == entry["sha256"], f"hash mismatch for {collection}/{name}")
    result = {key: name, "path": path, "owner": entry["owner"],
              "provenance": {field: entry["provenance"][field] for field in ("source_id", "origin")},
              "sha256": entry["sha256"]}
    label = f"{collection}/{name}"
    if collection == "skills":
        validators.skill(target, name)
        files, excluded = entry_files(target)
        records, omitted = collect(label, files, blobs, path, excluded=excluded)
        main = path + "/SKILL.md"
        records.sort(key=lambda record: (record["path"] != main, record["path"]))
        result.update(files=records, omitted=omitted)
    elif collection == "rules":
        records, omitted = collect(label, [(f"{name}.md", target)], blobs, path, subs=False)
        result.update(files=records, omitted=omitted)
    elif collection == "mcp_servers":
        result["server"] = server_entry(load_json(target), name)
    elif collection == "routines":
        definition = validators.routine(load_json(target))
        validators.require(definition["id"] == name, "routine id does not match its manifest entry")
        validators.relative_path(definition["prompt"])
        validators.require("/" not in definition["prompt"], "routine prompt must be a file in the routine directory")
        prompt = inside_root(target.parent, definition["prompt"])
        validators.require(prompt.is_file(), "routine prompt must be a file")
        directory = path.rpartition("/")[0]
        records, omitted = [], []
        for relative, file_path in (("routine.json", target), (definition["prompt"], prompt)):
            one, none = collect(label, [(relative, file_path)], blobs, directory)
            records += one
            omitted += none
        result.update(files=records, omitted=omitted)
    else:
        kind = "plugin" if collection == "plugins" else "connection"
        metadata = validators.metadata(load_json(target), kind)
        validators.require(metadata["name"] == name, "metadata name does not match its manifest entry")
        result["metadata"] = metadata
    description = mcp_endpoint.description(root, entry, collection)
    if description is not None:
        result["description"] = description
    return result


def build(root, include_local=False):
    root = Path(root)
    shipped = load_json(inside_root(root, "manifest.json"))
    screen_metadata(shipped)
    shipped = validators.manifest(shipped)
    validators.require(not any(entry["path"].startswith(validators.LOCAL + "/")
                               for kind in KINDS for entry in shipped.get(kind, [])),
                       "shipped manifest must not reference the local catalog")
    manifest = validators.layered(root, shipped, load_json) if include_local else shipped
    screen_metadata(manifest)
    blobs = {}
    kinds = {collection: sorted((build_entry(root, collection, key, entry, blobs) for entry in manifest.get(collection, [])),
                                key=lambda built: built[key])
             for collection, key in validators.COLLECTIONS.items()}
    index = {"schema": SCHEMA, "revision": "", "kinds": kinds}
    index["revision"] = hashlib.sha256(canonical(index).encode()).hexdigest()[:20]
    return Artifact(index["revision"], index, blobs)


def key_value_pairs(artifact):
    pairs = {"blob:" + digest: text for digest, text in sorted(artifact.blobs.items())}
    return pairs, {"rev:" + artifact.revision: canonical(artifact.index)}, \
        {"current": canonical({"revision": artifact.revision})}


def batches(pairs):
    batch, size = {}, 0
    for key, value in pairs.items():
        weight = len(key) + len(value.encode())
        if batch and (size + weight > BATCH_BYTES or len(batch) >= BATCH_KEYS):
            yield batch
            batch, size = {}, 0
        batch[key] = value
        size += weight
    if batch:
        yield batch


def local_items(artifact):
    prefix = validators.LOCAL + "/"
    return [(kind, entry[validators.COLLECTIONS[kind]], entry["path"]) for kind, entries in artifact.index["kinds"].items()
            for entry in entries if entry["path"].startswith(prefix)]


def describe_local(artifact):
    items = local_items(artifact)
    if not items:
        return
    print(f"Local catalog items included ({len(items)}), from {validators.LOCAL}:")
    for kind, name, path in items:
        print(f"  {kind}/{name}  {path}")


def describe(artifact):
    total = sum(len(text.encode()) for text in artifact.blobs.values())
    print(f"Revision: {artifact.revision}")
    for kind, entries in artifact.index["kinds"].items():
        key = validators.COLLECTIONS[kind]
        print(f"{kind}: " + (", ".join(entry[key] for entry in entries) or "none"))
        for entry in entries:
            for record in entry.get("files", []):
                print(f"  {record['path']}  {record['size']} bytes  sha256:{record['sha256'][:12]}")
            for record in entry.get("omitted", []):
                print(f"  {record['path']}  omitted ({record['reason']})")
    print(f"Blobs: {len(artifact.blobs)} ({total} bytes), plus 1 revision index and 1 current pointer")
    describe_local(artifact)


def publish(artifact, kv, dry_run=False):
    describe(artifact)
    if dry_run:
        print("Dry run: nothing was uploaded.")
        return
    blobs, index, pointer = key_value_pairs(artifact)
    for batch in batches(blobs):
        kv.put_many(batch)
    kv.put_many(index)
    kv.put_many(pointer)
    print(f"Published revision {artifact.revision}: {len(blobs) + 2} keys written.")


class ApiKv:
    def __init__(self, account_id, namespace_id, token, opener=urllib.request.urlopen):
        self.url = ("https://api.cloudflare.com/client/v4/accounts/" + account_id
                    + "/storage/kv/namespaces/" + namespace_id + "/bulk")
        self.token = token
        self.opener = opener

    def put_many(self, pairs):
        body = json.dumps([{"key": key, "value": value} for key, value in pairs.items()]).encode()
        request = urllib.request.Request(self.url, data=body, method="PUT", headers={
            "Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with self.opener(request, timeout=60) as response:
                payload = json.loads(response.read())
        except (urllib.error.URLError, ValueError):
            raise OSError("Cloudflare KV write failed") from None
        if not (isinstance(payload, dict) and payload.get("success") is True):
            raise OSError("Cloudflare rejected the KV write")


class CfKv:
    def __init__(self, namespace_id, profile, scratch, runner=subprocess.run):
        self.namespace_id = namespace_id
        self.profile = profile
        self.scratch = Path(scratch)
        self.runner = runner

    def put_many(self, pairs):
        self.scratch.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="publish-", dir=self.scratch))
        try:
            payload = directory / "batch.json"
            payload.write_text(json.dumps([{"key": key, "value": value} for key, value in pairs.items()]),
                               encoding="utf-8")
            payload.chmod(0o600)
            profile = ["--profile", self.profile] if self.profile else []
            command = ["cf", *profile, "kv", "bulk", "put", self.namespace_id, "--body", "@" + str(payload)]
            try:
                result = self.runner(command, capture_output=True, text=True, timeout=300)
            except FileNotFoundError:
                raise ValueError("cf is unavailable; install cf or Wrangler, or store the Cloudflare API token") from None
            if result.returncode != 0:
                raise OSError(f"cf kv bulk put failed (exit {result.returncode})")
        finally:
            shutil.rmtree(directory, ignore_errors=True)


class WranglerKv:
    def __init__(self, namespace_id, profile, remote_dir, scratch, runner=subprocess.run, executable=None):
        self.namespace_id = namespace_id
        self.profile = profile
        self.remote_dir = Path(remote_dir)
        self.scratch = Path(scratch)
        self.runner = runner
        self.executable = executable or resolve_wrangler(self.remote_dir)
        validators.require(self.executable is not None, "Wrangler is not installed; install cf or Wrangler, or store the Cloudflare API token")
        self.launcher = [self.executable] if isinstance(self.executable, str) else list(self.executable)

    def put_many(self, pairs):
        self.scratch.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="publish-", dir=self.scratch))
        try:
            payload = directory / "batch.json"
            with payload.open("x", encoding="utf-8") as stream:
                json.dump([{"key": key, "value": value} for key, value in pairs.items()], stream)
            payload.chmod(0o600)
            profile = ["--profile", self.profile] if self.profile else []
            command = [*self.launcher, *profile, "kv", "bulk", "put", str(payload),
                       "--namespace-id=" + self.namespace_id, "--remote", "--config", "wrangler.local.jsonc"]
            try:
                result = self.runner(command, cwd=self.remote_dir, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, timeout=300)
            except FileNotFoundError:
                raise ValueError("Wrangler is unavailable; install cf or Wrangler, or store the Cloudflare API token") from None
            except subprocess.TimeoutExpired:
                raise ValueError("Wrangler KV write timed out; check remote state before retrying") from None
            if result.returncode != 0:
                raise ValueError(f"Wrangler KV bulk put failed (exit {result.returncode}); check your private login, permissions and remote/wrangler.local.jsonc")
        finally:
            shutil.rmtree(directory, ignore_errors=True)


def resolve_wrangler(remote_dir, platform=None):
    installed = shutil.which("wrangler")
    if (platform or sys.platform) == "win32":
        node = shutil.which("node")
        candidates = [Path(remote_dir) / "node_modules" / "wrangler" / "bin" / "wrangler.js"]
        if installed:
            candidates.append(Path(installed).parent / "node_modules" / "wrangler" / "bin" / "wrangler.js")
        if node:
            for entry in candidates:
                if entry.is_file():
                    return [node, str(entry.resolve())]
        return installed if installed and Path(installed).suffix.lower() in (".exe", ".com") else None
    if installed:
        return installed
    local = Path(remote_dir) / "node_modules" / ".bin" / "wrangler"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return None


def load_config(root, override):
    path = Path(override) if override else Path(root) / ".hub-store" / CONFIG_FILE
    validators.require(path.is_file(), f"create {path} with the catalog_namespace_id (see docs/remote.md)")
    ordinary_path(path)
    config = load_json(path)
    validators.require(isinstance(config, dict) and isinstance(config.get("catalog_namespace_id"), str)
                       and bool(config["catalog_namespace_id"]), f"{CONFIG_FILE} needs catalog_namespace_id")
    validators.require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", config["catalog_namespace_id"]) is not None,
                       f"{CONFIG_FILE} has an invalid catalog_namespace_id")
    if "account_id" in config:
        validators.require(isinstance(config["account_id"], str)
                           and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", config["account_id"]) is not None,
                           f"{CONFIG_FILE} has an invalid account_id")
    if "cf_profile" in config:
        validators.require(isinstance(config["cf_profile"], str)
                           and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}", config["cf_profile"]) is not None,
                           f"{CONFIG_FILE} has an invalid cf_profile")
    return config


def transport(root, args):
    config = load_config(root, args.config)
    store = FileStore(root)
    remote_dir = Path(root) / "remote"
    via = args.via
    if via is None:
        if store.has(TOKEN_SECRET):
            via = "api"
        elif shutil.which("cf"):
            via = "cf"
        elif resolve_wrangler(remote_dir):
            via = "wrangler"
        else:
            raise ValueError("no publish transport found; install cf or Wrangler, or store the Cloudflare API token")
    if via == "cf":
        return CfKv(config["catalog_namespace_id"], config.get("cf_profile"), Path(root) / ".scratch")
    if via == "wrangler":
        return WranglerKv(config["catalog_namespace_id"], config.get("cf_profile"), remote_dir,
                          Path(root) / ".scratch")
    validators.require(store.has(TOKEN_SECRET), f"store the API token first: python3 store.py set {TOKEN_SECRET}")
    validators.require(isinstance(config.get("account_id"), str) and bool(config["account_id"]),
                       f"{CONFIG_FILE} needs account_id for the api transport")
    return ApiKv(config["account_id"], config["catalog_namespace_id"], store.resolve("secret://" + TOKEN_SECRET).strip())


def confirmed(args, artifact):
    if args.yes:
        return True
    validators.require(sys.stdin.isatty(), "publishing writes to Cloudflare; rerun with --yes to approve")
    if local_items(artifact):
        describe_local(artifact)
        print("These private local items will be readable by every client you authorize.")
    answer = input(f"Publish revision {artifact.revision} to the remote catalog? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


def run(args, root=None):
    if root is None:
        from hub import ROOT as root
    local_count = local_item_count(root)
    if local_count and not args.include_local and not getattr(args, "shipped_only", False):
        raise ValueError(f"refusing to omit {local_count} local catalog item(s); choose --include-local or --shipped-only")
    artifact = build(root, include_local=args.include_local)
    if local_count and getattr(args, "shipped_only", False):
        print(f"Explicit --shipped-only selection; omitting {local_count} local catalog item(s).")
    if args.dry_run:
        publish(artifact, None, dry_run=True)
        return 0
    if not confirmed(args, artifact):
        print("Cancelled; nothing was uploaded.")
        return 1
    publish(artifact, transport(root, args))
    return 0


def local_item_count(root):
    path = ordinary_path(Path(root) / validators.LOCAL / "manifest.json")
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return 0
    validators.require(stat.S_ISREG(mode), "local catalog manifest must be an ordinary file")
    local = validators.read_local(Path(root), load_json)
    return sum(len(local.get(collection, [])) for collection in KINDS)


def register(commands):
    command = commands.add_parser(
        "publish-remote", help="Publish the catalog to the remote MCP server's Workers KV store.",
        description="Build the manifest-listed catalog, verify it, and upload an immutable revision to Workers KV.")
    command.add_argument("--dry-run", action="store_true", help="Show what would be published; upload nothing.")
    selection = command.add_mutually_exclusive_group()
    selection.add_argument("--include-local", action="store_true", help="Also publish your private local .hub-store catalog imports (listed and confirmed first; off by default).")
    selection.add_argument("--shipped-only", action="store_true", help="Explicitly publish shipped content and omit any local catalog items.")
    command.add_argument("--yes", action="store_true", help="Approve the upload without a prompt.")
    command.add_argument("--via", choices=("cf", "wrangler", "api"), help="Upload with cf, Wrangler, or the stored API token.")
    command.add_argument("--config", help="Path to the remote configuration (default .hub-store/remote.json).")
    command.set_defaults(action=run)
