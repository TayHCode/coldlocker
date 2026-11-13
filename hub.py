import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import safety
from checks import checker, validators
from gateway_render import PATH_BINDINGS, _read_bindings, _references, store_source_path_bindings
from importers import skill_package, skill_package_texts
import lifecycle
import packs
from lifecycle import changed_targets, confirmed, record_links, retained
from scanner import home_pattern, located, texts
from store import FileStore, ordinary_path
from portable import lock


ROOT = Path(__file__).resolve().parent
VERSION = "0.1.0"
MARKDOWN_KINDS = {"rule": "rules", "subagent": "subagents", "command": "commands"}


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            validators.require(key not in result, "duplicate JSON field")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)


def write_json(path, value):
    path.write_bytes((json.dumps(value, indent=2) + "\n").encode("utf-8"))


def read_manifest(root=ROOT):
    return validators.layered(root, packs.visible(root, validators.shipped(root, read_json)), read_json)


def private_store(root):
    (root / ".hub-store").mkdir(mode=0o700, exist_ok=True)


def ordinary_parents(path):
    ordinary_path(path)
    for parent in (path, *path.parents):
        validators.require(not parent.exists() or parent.is_dir(), "destination parent is not a directory")


def record(value, collection, item_name, destination, owner, origin, root=ROOT):
    updated = import_manifest(value, collection, item_name, destination.relative_to(root).as_posix(),
                              owner, origin, validators.digest(destination))
    write_json(root / "manifest.json", updated)
    value.update(updated)
    print(f"Recorded {collection}: {item_name}")


def import_manifest(value, collection, item_name, relative, owner, origin, checksum):
    key = validators.COLLECTIONS[collection]
    entry = {key: item_name, "path": relative,
             "owner": owner, "provenance": {"source_id": f"src-{checksum[:12]}", "origin": origin},
             "sha256": checksum}
    entries = [item for item in value.get(collection, []) if item[key] != item_name] + [entry]
    return validators.manifest({**value, collection: sorted(entries, key=lambda item: item[key])})


class Refused(ValueError):
    pass


def known_hosts(manifest, root):
    hosts = set()
    for entry in manifest.get("mcp_servers", []):
        try:
            hosts.add(urlsplit(read_json(root / entry["path"]).get("url") or "").hostname or "")
        except (OSError, ValueError, AttributeError):
            continue
    return hosts


def review(kind, payload, files, root=ROOT):
    kind = {"mcp": "mcp_server"}.get(kind, kind)
    return safety.review(kind, payload, files, known_hosts(read_manifest(root), root),
                         label=files[0][0] if files else "definition")


def screen(kind, payload, files, approve=False, root=ROOT, name=None, record=True):
    found = review(kind, payload, files, root)
    high = [item for item in found if item["severity"] == "high"]
    blocked = [item for item in high if item["blocking"]]
    refused = bool(blocked or (high and not approve))
    outcome = "refused" if refused else "approved" if high else "allowed-with-warnings" if found else "allowed"
    timestamp = datetime.now(timezone.utc)
    name = name or (payload.get("name") or payload.get("id") if isinstance(payload, dict) else None)
    findings = []
    for item in found:
        location = re.fullmatch(r"(.*?):(\d+(?:,\d+)*)(?: \+more)?", item["location"])
        file = location[1] if location else item["location"]
        lines = [int(n) for n in location[2].split(",")] if location else [1]
        findings.extend({**{key: item[key] for key in ("check", "category", "severity", "reason")},
                         "file": file, "line": line} for line in lines)
    receipt = {"version": 1, "time": timestamp.isoformat(), "kind": kind, "outcome": outcome,
               "approved": bool(approve), "findings": findings}
    if name:
        receipt["name"] = name
    identity = name or hashlib.sha256(json.dumps([payload, files], sort_keys=True).encode()).hexdigest()[:12]
    identity = re.sub(r"[^a-zA-Z0-9-]", "-", identity)[:63]

    def save():
        directory = root / ".hub-store/import-receipts"
        ordinary_parents(directory)
        private_store(root)
        directory.mkdir(mode=0o700, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=directory)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(receipt, stream, indent=2)
                stream.write("\n")
            temporary_path.replace(directory / f"{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}-{kind}-{identity}.json")
        finally:
            temporary_path.unlink(missing_ok=True)

    if record is True or (record == "deferred" and refused):
        save()
    lines = "; ".join(f"{item['check']} ({item['file']}:{item['line']}): {item['reason']}" for item in findings)
    warnings = [f"WARNING: {item['check']} ({item['file']}:{item['line']}): {item['reason']}"
                for item in findings if item["severity"] == "warn"]
    for warning in warnings:
        print(warning, file=sys.stderr)
    if blocked:
        raise Refused(f"REFUSED: contains a secret; remove it and keep it in the private store. {lines}")
    if refused:
        raise Refused(f"REFUSED: needs review before import. {lines}. Inspect it, then re-run with --approve-risky to import it anyway.")
    return save if record == "deferred" else warnings


def private_text(text, label):
    text = home_pattern(Path.home(), sys.platform).sub("~", text)
    found = located([(label, text)])
    if found:
        raise Refused(f"REFUSED: definition contains {found}")
    return text


def copy_skill(source, destination):
    if source.resolve() == destination.resolve():
        return
    validators.require(not destination.exists() or destination.is_dir(), "skill destination must be a directory")
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staged = Path(temporary) / "package"
        shutil.copytree(source, staged, ignore=shutil.ignore_patterns("__pycache__"))
        previous = Path(temporary) / "previous"
        if destination.exists():
            destination.rename(previous)
        try:
            staged.rename(destination)
        except OSError:
            if previous.exists():
                previous.rename(destination)
            raise


def ordinary_file(path):
    ordinary_path(path)
    validators.require(not path.exists() or stat.S_ISREG(path.stat().st_mode),
                       "destination must be an ordinary file")


def import_preflight(value, collection, item_name, destination, relative, owner, origin, kind):
    key = validators.COLLECTIONS[collection]
    entry = next((item for item in value.get(collection, []) if item[key] == item_name), None)
    ordinary_parents(destination.parent)
    ordinary_path(destination)
    if entry is None:
        validators.require(not destination.exists() and (kind != "routine" or not destination.parent.exists()),
                           "unindexed destination exists; preserve it before importing")
        return None
    validators.require(validators.same_owner(entry["owner"], owner), f"existing entry is owned by {entry['owner']}; pass --owner {entry['owner']} to update it")
    validators.require(entry["provenance"]["origin"] == origin,
                       f"existing entry has different provenance origin {entry['provenance']['origin']}; pass --origin {entry['provenance']['origin']} to update it")
    validators.require(entry["path"] == relative, "existing entry path does not match destination")
    validators.require(entry["provenance"]["source_id"] == "src-" + entry["sha256"][:12],
                       "existing entry provenance does not match recorded content")
    validators.require(destination.exists(), "indexed destination is missing")
    if kind == "skill":
        validators.require(destination.is_dir(), "skill destination must be a directory")
        validators.require(not any(".git" in item.relative_to(destination).parts
                                   for item in destination.rglob("*")),
                           "skill destination contains unindexed .git content; preserve it before importing")
        validators.require(all(not item.is_symlink() and
                               (item.is_dir() or stat.S_ISREG(item.stat().st_mode))
                               for item in destination.rglob("*")), "skill destination contains nonordinary content")
    else:
        ordinary_file(destination)
    validators.require(validators.digest(destination) == entry["sha256"],
                       "destination differs from its recorded hash; preserve local changes before importing")
    return entry


def import_snapshot(path):
    ordinary_path(path)
    if not path.exists():
        return None
    info = path.stat()
    validators.require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode),
                       "import destination contains nonordinary content")
    identity = (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    if path.is_dir():
        children = tuple((item.relative_to(path).as_posix(), import_snapshot(item))
                         for item in sorted(path.iterdir()))
        return identity, children
    return identity, validators.digest(path)


def stage_path_bindings(root, staged_root, item_name, bindings, indexed, snapshots, definition):
    store = FileStore(root)
    path = root / PATH_BINDINGS
    ordinary_file(path)
    snapshots[path] = import_snapshot(path)
    current = _read_bindings(root)
    validators.require(indexed or item_name not in current["servers"], "unindexed source path bindings exist")
    if bindings is None:
        previous = current["servers"].get(item_name, {})
        command_matches = "command" not in previous or previous["command"]["portable"] == definition.get("command")
        args = definition.get("args", [])
        args_match = all(int(index) < len(args) and args[int(index)] == binding["portable"]
                         for index, binding in previous.get("args", {}).items())
        if command_matches and args_match:
            return []
        bindings = {}
    staged_store = FileStore(staged_root)
    staged_path = staged_root / PATH_BINDINGS
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(staged_path, current)
    before = _references(current)
    for reference in before:
        source = store._file(reference)
        ordinary_file(source)
        snapshots[source] = import_snapshot(source)
        if source.exists():
            staged_store._directory()
            shutil.copyfile(source, staged_store._file(reference))
            staged_store._file(reference).chmod(0o600)
    store_source_path_bindings(staged_root, item_name, bindings)
    after = _references(_read_bindings(staged_root))
    operations = [(path, staged_path if staged_path.exists() else None)]
    for reference in sorted(before ^ after):
        target = store._file(reference)
        ordinary_file(target)
        snapshots.setdefault(target, import_snapshot(target))
        validators.require(reference in before or not target.exists(), "source path reference collision")
        operations.append((target, staged_store._file(reference) if reference in after else None))
    return operations


def publish_import(operations, snapshots, finish=None):
    prepared = []
    changed = []
    created = []
    keep_backups = False
    try:
        for target, staged in operations:
            ordinary_parents(target.parent)
            ordinary_path(target)
            missing = [parent for parent in (target.parent, *target.parent.parents) if not parent.exists()]
            for parent in reversed(missing):
                parent.mkdir(mode=0o700)
                created.append(parent)
            backup = Path(tempfile.mkdtemp(prefix=".import-", dir=target.parent))
            pending = backup / "pending"
            previous = backup / "previous"
            prepared.append((target, pending if staged else None, previous, backup))
            if staged:
                if staged.is_dir():
                    shutil.copytree(staged, pending)
                else:
                    shutil.copyfile(staged, pending)
                    pending.chmod(stat.S_IMODE(target.stat().st_mode if target.exists() else staged.stat().st_mode))
        for path, snapshot in snapshots.items():
            validators.require(import_snapshot(path) == snapshot, "import destination changed during preparation; retry after review")
        for target, pending, previous, backup in prepared:
            validators.require(import_snapshot(target) == snapshots[target], "import destination changed during publication; retry after review")
            changed.append((target, previous, target.exists()))
            if target.exists():
                target.replace(previous)
            if pending:
                pending.replace(target)
        if finish:
            finish()
    except BaseException:
        try:
            for target, previous, existed in reversed(changed):
                if previous.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    previous.replace(target)
                elif not existed and target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
        except OSError:
            keep_backups = True
            raise Refused("REFUSED: import rollback failed; prior state remains in .import-* directories for recovery")
        raise
    finally:
        if not keep_backups:
            for _, _, _, backup in prepared:
                try:
                    shutil.rmtree(backup)
                except OSError:
                    print("Import cleanup incomplete; preserve .import-* directories for recovery", file=sys.stderr)
            for parent in reversed(created):
                try:
                    if parent.exists() and not any(parent.iterdir()):
                        parent.rmdir()
                except OSError:
                    print("Import cleanup incomplete; an empty import directory may remain", file=sys.stderr)


GITHUB_URL = re.compile(r"https://github\.com/(?P<owner>[A-Za-z0-9-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?"
                        r"(?P<path>(?:/[A-Za-z0-9_.-]+)*)/?(?:@(?P<ref>[A-Za-z0-9_][A-Za-z0-9_./-]*))?")


def github_source(url):
    match = GITHUB_URL.fullmatch(url)
    parts = match["path"].strip("/").split("/") if match and match["path"] else []
    validators.require(match is not None and not match["repo"].startswith(".") and
                       not {".", "..", ".git"} & set(parts) and ".." not in (match["ref"] or ""),
                       "invalid GitHub URL; use https://github.com/<owner>/<repo>[/path][@ref]")
    return match["owner"], match["repo"], "/".join(parts), match["ref"]


def fetch_github(owner, repo, ref, checkout):
    checkout.mkdir()

    def git(*args):
        try:
            return subprocess.run(["git", "-C", str(checkout), *args], check=True, capture_output=True, text=True,
                                  env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
                                       "GIT_CONFIG_GLOBAL": os.devnull}, timeout=300).stdout.strip()
        except subprocess.CalledProcessError as error:
            raise ValueError(f"git {args[0]} failed: {(error.stderr.strip().splitlines() or [''])[0]}") from None
        except subprocess.TimeoutExpired:
            raise ValueError(f"git {args[0]} timed out") from None

    git("init", "-q", "--template=")
    (checkout / ".git/info").mkdir(exist_ok=True)
    (checkout / ".git/info/attributes").write_text("* -filter\n", encoding="utf-8")
    git("fetch", "-q", "--depth", "1", "--no-tags", f"https://github.com/{owner}/{repo}.git", ref or "HEAD")
    git("checkout", "-q", "FETCH_HEAD")
    commit = git("rev-parse", "HEAD")
    shutil.rmtree(checkout / ".git")
    return commit


def import_github_skill(args, root):
    owner, repo, path, ref = github_source(args.path)
    url = f"https://github.com/{owner}/{repo}"
    record_path = root / ".hub-store/git-sources.json"
    ordinary_file(record_path)
    private_store(root)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(root / ".hub-store/git-sources.lock", flags, 0o600), "r+b") as stream:
        lock(stream)
        sources = read_json(record_path) if record_path.exists() else {}
        validators.require(isinstance(sources, dict), "invalid .hub-store/git-sources.json")
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary) / repo
            commit = fetch_github(owner, repo, ref, checkout)
            source = checkout.joinpath(*path.split("/")) if path else checkout
            validators.require(source.is_dir() and source.resolve() == checkout.resolve().joinpath(*path.split("/")),
                               f"{path or '/'} is not a directory in the repository")
            name = validators.name(args.name or source.name)
            sources[name] = {"url": url, "path": path, "ref": ref, "commit": commit}
            descriptor, staged = tempfile.mkstemp(dir=record_path.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as staged_stream:
                    staged_stream.write(json.dumps(sources, indent=2) + "\n")
                import_item(argparse.Namespace(**{**vars(args), "path": str(source), "name": name,
                                                  "origin": args.origin or "github"}), root)
                os.replace(staged, record_path)
            finally:
                Path(staged).unlink(missing_ok=True)
    print(f"Imported {url}{'/' + path if path else ''} at commit {commit}")
    return 0


def import_item(args, root=ROOT):
    if args.command == "import-skill" and "://" in args.path:
        return import_github_skill(args, root)
    ordinary_file(root / "manifest.json")
    ordinary_file(root / validators.LOCAL / "manifest.json")
    snapshots = {path: import_snapshot(path) for path in
                 (root / "manifest.json", root / validators.LOCAL / "manifest.json")}
    shipped = validators.shipped(root, read_json)
    value = validators.read_local(root, read_json)
    catalog = root / validators.LOCAL
    source = Path(args.path)
    validators.require(not source.is_symlink(), "input must not be a symlink")
    owner = validators.label(args.owner or "coldlocker")
    origin = validators.label(args.origin or "local-import")
    kind = args.command.removeprefix("import-")
    content = None
    prompt = None
    stored = None
    skill_files = None
    if kind == "skill":
        item_name = validators.name(args.name or source.name)
        skill_files = skill_package_texts(source)
        collection, relative = "skills", f"skills/{item_name}"
    elif kind in MARKDOWN_KINDS:
        validators.require(source.is_file() and source.suffix == ".md", f"{kind} must be a Markdown file")
        item_name = (args.name or source.stem.lower())
        validators.require(isinstance(item_name, str) and validators.NAME.fullmatch(item_name),
                           f"invalid name or id: {item_name!r}; pass --name with a valid name")
        collection = MARKDOWN_KINDS[kind]
        relative = f"{collection}/{item_name}.md"
    else:
        content = read_json(source)
        if kind == "mcp":
            validators.mcp(content)
            item_name = content["name"]
            collection, relative = "mcp_servers", f"mcp/servers/{item_name}.json"
        elif kind in ("plugin", "connection"):
            from importers import parse_metadata
            content = parse_metadata(json.dumps(content), kind)
            item_name = content["name"]
            collection = "plugins" if kind == "plugin" else "connections"
            relative = f"{collection}/{item_name}.json"
        else:
            validators.routine(content)
            item_name = content["id"]
            validators.require(args.owner in (None, content["owner"]), "owner flag does not match routine owner")
            owner = content["owner"]
            collection, relative = "routines", f"routines/{item_name}/routine.json"
            prompt_name = content["prompt"]
            validators.require(len(Path(prompt_name).parts) == 1 and prompt_name not in {".", ".."},
                               "prompt must be an ordinary file next to the routine")
            candidate = source.parent / prompt_name
            validators.require(candidate.is_file() and not candidate.is_symlink(), "prompt must be an ordinary file")
            validators.require(prompt_name != "routine.json", "prompt conflicts with routine definition")
            prompt = candidate
    record_receipt = None
    if kind != "connection":
        files = skill_files if kind == "skill" else texts(source) if kind in MARKDOWN_KINDS else [("prompt.md", prompt.read_text(encoding="utf-8"))] if prompt else texts(source)
        record_receipt = screen(kind, content, files, getattr(args, "approve_risky", False), root, name=item_name,
                                record="deferred")
    if kind == "skill":
        skill_package(source, item_name, skill_files)
    if kind in MARKDOWN_KINDS:
        stored = private_text(source.read_bytes().decode("utf-8"), source.name)
    elif prompt:
        stored = private_text(prompt.read_bytes().decode("utf-8"), prompt.name)
    validators.require(item_name not in {item[validators.COLLECTIONS[collection]] for item in shipped.get(collection, [])},
                       "name belongs to a shipped entry")
    destination = catalog / relative
    validators.require(source.resolve() not in destination.resolve().parents, "input contains destination")
    snapshots[destination] = import_snapshot(destination)
    entry = import_preflight(value, collection, item_name, destination, relative, owner, origin, kind)
    if prompt:
        target = destination.parent / prompt.name
        ordinary_file(target)
        snapshots[target] = import_snapshot(target)
        validators.require(not target.exists() or
                           (entry is not None and read_json(destination).get("prompt") == prompt.name),
                           "unindexed prompt destination exists")
    packs.pin(root)
    with tempfile.TemporaryDirectory() as temporary:
        staged_root = Path(temporary)
        staged = staged_root / "catalog" / relative
        staged.parent.mkdir(parents=True)
        if kind == "skill":
            shutil.copytree(source, staged, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        elif kind in MARKDOWN_KINDS:
            staged.write_bytes(stored.encode("utf-8"))
        else:
            write_json(staged, content)
        operations = [(destination, staged)]
        if prompt:
            staged_prompt = staged.parent / prompt.name
            staged_prompt.write_bytes(stored.encode("utf-8"))
            operations.append((destination.parent / prompt.name, staged_prompt))
        previous = changed_targets(operations)
        updated = import_manifest(value, collection, item_name, relative, owner, origin, validators.digest(staged))
        staged_manifest = staged_root / "catalog" / "manifest.json"
        write_json(staged_manifest, updated)
        if kind == "mcp":
            snapshots[root / "config.json"] = import_snapshot(root / "config.json")
            operations.extend(stage_path_bindings(root, staged_root, item_name,
                                                  getattr(args, "path_bindings", None), entry is not None, snapshots, content))
        operations.append((catalog / "manifest.json", staged_manifest))
        unchanged = all(target.exists() == (staged is not None) and
                        (staged is None or validators.digest(target) == validators.digest(staged))
                        for target, staged in operations)
        with retained(root, collection, item_name, previous):
            publish_import(operations, snapshots, None if unchanged else record_receipt)
    print(f"Recorded {collection}: {item_name}")
    return 0


def confirm_home_link(args, pending):
    if args.yes or args.dry_run:
        return True
    for source, destination in pending:
        print(f"Would link {destination} -> {os.path.relpath(source, destination.parent)}")
    return confirmed(args, f"Create {len(pending)} link(s) in your home skill directories?")


def link(args):
    checker.instruction_consistency(ROOT)
    home = Path.home().resolve()
    from connect import SKILLS_REACH, scratch_part
    scratch = scratch_part(ROOT)
    if scratch:
        location = 'uses Git directory indirection (.git file)' if scratch == '.git' else f'is under {scratch}'
        print(f'WARNING: {ROOT} {location}; deleting it breaks linked skills. Clone Coldlocker somewhere permanent and rerun.')
    if args.home:
        root = home
    else:
        root = Path(args.target).resolve()
        validators.require(root != home, "refusing home directory target; to link into ~/.claude/skills and "
                           "~/.agents/skills use --home. " + SKILLS_REACH)
        if root.is_relative_to(home):
            inside_project = any((parent / ".git").exists() for parent in (root, *root.parents) if parent != home and parent.is_relative_to(home))
            validators.require(inside_project, "refusing target inside your home directory that is not in a project directory containing .git")
        for config in (".claude", ".codex", ".agents", ".cursor"):
            protected = (home / config).resolve()
            validators.require(not root.is_relative_to(protected), "refusing home config target")
    value = read_manifest()
    pending = []
    conflicts = []
    for agent in (".claude", ".agents"):
        parent = root / agent / "skills"
        ordinary_parents(parent)
        for entry in value["skills"]:
            source = ROOT / entry["path"]
            validators.require(source.is_dir() and not source.is_symlink(), "skill source is missing or symlinked")
            destination = parent / entry["name"]
            if destination.is_symlink() and destination.resolve() == source.resolve():
                continue
            if destination.is_symlink() or destination.exists():
                reason = "conflicting destination link" if destination.is_symlink() else "conflicting destination exists"
                validators.require(args.home, reason)
                conflicts.append(destination)
            else:
                pending.append((source, destination))
    if pending and args.home and not confirm_home_link(args, pending):
        print("Cancelled; nothing changed")
        return 1
    if pending and not args.dry_run:
        ordinary_parents((ROOT / lifecycle.LINKS).parent)
        ordinary_file(ROOT / lifecycle.LINKS)
    created = []
    for source, destination in pending:
        relative = os.path.relpath(source, destination.parent)
        if not args.dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(relative, target_is_directory=True)
            created.append((destination, source))
        print(f"{'Would link' if args.dry_run else 'Linked'} {destination} -> {relative}")
    record_links(sys.modules[__name__], ROOT, created)
    for destination in conflicts:
        print(f"Conflict (kept as is): {destination} exists and is not a link to this skill")
    if not pending and not conflicts:
        print("Nothing to do")
    return 0


RULES_BEGIN = "<!-- coldlocker:rules:begin -->"
RULES_END = "<!-- coldlocker:rules:end -->"


def delivery_hash(content):
    return hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest()


def recover_delivery_files(target):
    history = target / ".coldlocker" / "backups"
    ordinary_parents(history)
    if not history.exists():
        return
    intents = []
    for intent in sorted(history.glob("*.json")):
        ordinary_file(intent)
        row = read_json(intent)
        validators.require(isinstance(row, dict) and set(row) in ({"path", "backup"}, {"path", "candidate"}), "invalid delivery recovery record")
        intents.append((intent, row))
    # Resolve installation attempts before restoring any previous inode.
    for intent, row in sorted(intents, key=lambda item: "candidate" not in item[1]):
        destination = target / row["path"]
        backup = history / row.get("backup", row.get("candidate"))
        validators.require(destination == target / "AGENTS.md" or destination.parent in
                           (target / ".coldlocker", target / ".coldlocker/rules", target / ".coldlocker/archive"),
                           "invalid delivery recovery destination")
        validators.require(backup.parent == history and backup.name.endswith(".previous"), "invalid delivery backup")
        ordinary_file(destination)
        ordinary_file(backup)
        if "candidate" in row:
            validators.require(not destination.exists() or backup.exists() and os.path.samefile(destination, backup),
                               "concurrent rule creation preserved; resolve the foreign file before retry")
        elif not destination.exists() and backup.exists():
            os.link(backup, destination)
        intent.unlink()


def preserve_delivery_file(path, expected):
    """Move the current inode to retained history before a no-clobber install."""
    ordinary_file(path)
    if not path.exists():
        validators.require(expected is None, "rule destination changed before replacement; preserved")
        return None
    root = next((parent for parent in path.parents if parent.name == ".coldlocker"), path.parent / ".coldlocker")
    history = root / "backups"
    ordinary_parents(history)
    history.mkdir(parents=True, exist_ok=True)
    ignore = history / ".gitignore"
    ordinary_file(ignore)
    if not ignore.exists():
        atomic_delivery_write(ignore, b"*\n", None)
    validators.require(ignore.read_text(encoding="utf-8") == "*\n", "backup ignore file differs; preserved")
    fd, name = tempfile.mkstemp(prefix=path.name + "-", dir=history)
    backup = Path(name + ".previous")
    intent = Path(name + ".json")
    target = root.parent
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(dict(path=path.relative_to(target).as_posix(), backup=backup.name), stream)
    Path(name).replace(intent)
    path.replace(backup)
    actual = import_snapshot(backup)
    matches = (backup.read_bytes() == expected if isinstance(expected, bytes)
               else expected is not None and actual[0][:-1] == expected[0][:-1] and actual[1] == expected[1])
    if not matches:
        try:
            os.link(backup, path)
        except FileExistsError:
            pass
        raise ValueError("rule destination changed before replacement; preserved in " + str(backup))
    return backup, intent


def atomic_delivery_write(path, content, expected):
    ordinary_file(path)
    if path.exists() and path.read_bytes() == content:
        matches = path.read_bytes() == expected if isinstance(expected, bytes) else import_snapshot(path) == expected
        validators.require(matches, "rule destination changed before replacement; preserved")
        return
    fd, temporary = tempfile.mkstemp(prefix=".coldlocker-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        retained = preserve_delivery_file(path, expected)
        if retained:
            os.chmod(temporary, stat.S_IMODE(retained[0].stat().st_mode))
        installation = None
        if path.parent.name == "rules" and path.parent.parent.name == ".coldlocker":
            history = path.parent.parent / "backups"
            ordinary_parents(history)
            history.mkdir(exist_ok=True)
            ignore = history / ".gitignore"
            ordinary_file(ignore)
            if not ignore.exists():
                atomic_delivery_write(ignore, b"*\n", None)
            validators.require(ignore.read_text(encoding="utf-8") == "*\n", "backup ignore file differs; preserved")
            fd, name = tempfile.mkstemp(prefix="install-", dir=history)
            candidate, installation = Path(name + ".previous"), Path(name + ".json")
            os.link(temporary, candidate)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(dict(path=path.relative_to(path.parent.parent.parent).as_posix(), candidate=candidate.name), stream)
            Path(name).replace(installation)
        # link fails if another writer created the destination after the move.
        os.link(temporary, path)
        if installation:
            installation.unlink()
        if retained:
            retained[1].unlink()
    finally:
        Path(temporary).unlink(missing_ok=True)


def current_lessons(folder):
    briefed = set(re.findall(r"(?m)^- \*\*(L[0-9]+)\b", (folder / "briefing.md").read_text(encoding="utf-8")))
    validators.require(briefed, "briefing.md lists no lessons; remove coldfix rules with hub.py remove instead")
    candidates = {}
    for path in sorted(folder.glob("L[0-9]*-*.md")):
        text = path.read_text(encoding="utf-8")
        header, separator, _ = text[4:].partition("\n---\n")
        if not text.startswith("---\n") or not separator:
            continue
        fields = {key.strip(): value.strip() for key, _, value in
                  (line.partition(":") for line in header.splitlines())}
        if (fields.get("id") in briefed and fields.get("status") in ("active", "enforced")
                and fields.get("verification", "").startswith("proven")):
            candidates.setdefault(fields["id"], []).append((path, fields.get("slug", "")))
    found, refused = {}, []
    for lesson_id, matches in sorted(candidates.items()):
        name = f"coldfix-{lesson_id.lower()}-{matches[0][1]}"[:63].rstrip("-")
        if (len(matches) != 1 or matches[0][0].name.split("-", 1)[0] != lesson_id
                or not validators.NAME.fullmatch(name)):
            refused += [path.name for path, _ in matches]
            continue
        found[name] = matches[0][0]
    return found, refused


def sync_lessons(args, root=ROOT):
    folder = args.lessons or os.environ.get("COLDLOCKER_LESSONS")
    validators.require(folder, "pass --lessons DIR or set COLDLOCKER_LESSONS")
    wanted, refused = current_lessons(Path(folder))
    held = {entry["name"] for entry in validators.read_local(root, read_json).get("rules", [])
            if entry["owner"] == "coldfix" and entry["provenance"]["origin"] == "coldfix"
            and entry["name"].startswith("coldfix-")}
    for name in sorted(held - wanted.keys()):
        lifecycle.remove(argparse.Namespace(kind="rules", name=name, owner="coldfix", yes=True),
                         sys.modules[__name__], root)
    for name, path in sorted(wanted.items()):
        import_item(argparse.Namespace(command="import-rule", path=str(path), owner="coldfix", origin="coldfix",
                                       name=name, approve_risky=False), root)
    print(f"Coldfix lessons in catalog: {len(wanted)}")
    for name in refused:
        print(f"FAIL: {name}: lesson id must match its file name and be unique; not served", file=sys.stderr)
    return 1 if refused else 0


def deliver_rules(args):
    withdrawing = getattr(args, "withdraw", False)
    if not withdrawing:
        checker.instruction_consistency(ROOT)
    target = Path(args.target).absolute()
    ordinary_parents(target)
    validators.require((target / ".git").exists(), "target must be a repository root")
    if not args.dry_run:
        recover_delivery_files(target)
    instructions = target / "AGENTS.md"
    ordinary_file(instructions)
    snapshots = {instructions: import_snapshot(instructions)}
    original = instructions.read_text(encoding="utf-8") if instructions.exists() else ""
    validators.require(original.count(RULES_BEGIN) == original.count(RULES_END) <= 1, "invalid managed rule section")
    if RULES_BEGIN in original:
        validators.require(original.index(RULES_BEGIN) < original.index(RULES_END), "invalid managed rule section")
    directory = target / ".coldlocker" / "rules"
    ordinary_parents(directory)
    registry_path = directory.parent / "rules.json"
    ordinary_file(registry_path)
    snapshots[registry_path] = import_snapshot(registry_path)
    pending_path = directory.parent / "pending"
    ordinary_file(pending_path)
    validators.require(not pending_path.exists() or pending_path.read_text(encoding="utf-8") ==
                       "Rule delivery incomplete; retry deliver-rules.\n", "foreign pending marker preserved")
    registry = read_json(registry_path) if registry_path.exists() else {}
    validators.require(isinstance(registry, dict) and all(validators.NAME.fullmatch(key)
                       and isinstance(value, dict) and set(value) == {"hashes", "lesson", "pending"}
                       and type(value["pending"]) is bool and type(value["lesson"]) is bool and isinstance(value["hashes"], list) and value["hashes"]
                       and all(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) for digest in value["hashes"])
                       for key, value in registry.items()), "invalid rule ownership registry")
    final_registry = dict(registry)
    entries = {} if withdrawing else {entry["name"]: entry for entry in read_manifest()["rules"]}
    selected = {}
    withdrawn = {}
    for name in sorted(set(args.rule)):
        validators.name(name)
        if withdrawing:
            destination = directory / (name + ".md")
            ordinary_file(destination)
            entry = registry.get(name)
            if entry is None:
                validators.require(not destination.exists(), "unregistered rule preserved; withdrawal incomplete")
                continue
            snapshots[destination] = import_snapshot(destination)
            archive = None
            if destination.exists():
                checksum = delivery_hash(destination.read_bytes())
                validators.require(checksum in entry["hashes"], "owned rule was modified; preserved")
                archive = directory.parent / "archive" / (name + "-" + checksum + ".md")
                ordinary_parents(archive.parent)
                ordinary_file(archive)
                snapshots[archive] = import_snapshot(archive)
                validators.require(not archive.exists() or delivery_hash(archive.read_bytes()) == checksum,
                                   "archive conflict; preserved")
            registry[name] = dict(entry, pending=True)
            final_registry.pop(name, None)
            withdrawn[destination] = archive
            continue
        validators.require(name in entries, "unknown rule name")
        source = ROOT / entries[name]["path"]
        ordinary_file(source)
        content = source.read_bytes()
        validators.require(validators.digest(source) == entries[name]["sha256"], "rule hash mismatch")
        text = content.decode("utf-8")
        marker = "<!-- coldlocker:lesson -->\n"
        lines = text.splitlines()
        marked = text.replace("\r\n", "\n").startswith(marker)
        validators.require(not any(line.startswith("Proposal: coldfix:proposal:") for line in lines),
                           "unproven proposals remain pending")
        references = [line for line in lines if re.fullmatch(r"Lesson: coldfix:lesson:[^\s:]+:[^\s:]+", line)]
        lesson_metadata = (any(line in ("Status: active", "Status: enforced", "Status: retired") for line in lines)
                           and "Verification: proven" in lines
                           and any(line in ("Scope: global", "Scope: repository-specific") for line in lines))
        is_lesson = (marked or "## Lesson record" in lines or any(line.startswith("Lesson:") for line in lines)
                     or bool(references) or lesson_metadata
                     or any(line.startswith("Replay: coldfix:replay:") for line in lines))
        if is_lesson:
            validators.require(len(references) == 1, "Coldfix lesson requires one valid lesson reference")
            import runpy
            validate_lesson = runpy.run_path(str(Path(__file__).resolve().parent / "skills/verify-loop/verify_loop.py"))["lesson_check"]
            validators.require(validate_lesson(text.replace("\r\n", "\n")) is not None, "retired lessons are not delivered")
            if not marked:
                content = marker.encode("utf-8") + content
        destination = directory / (name + ".md")
        ordinary_file(destination)
        snapshots[destination] = import_snapshot(destination)
        validators.require(not destination.exists()
                           or delivery_hash(destination.read_bytes()) in registry.get(name, {}).get("hashes", []),
                           "rule destination differs from owned copy; preserved")
        digest = delivery_hash(content)
        hashes = [delivery_hash(destination.read_bytes())] if destination.exists() else []
        registry[name] = dict(hashes=sorted(set([*hashes, digest])), lesson=is_lesson, pending=True)
        final_registry[name] = dict(hashes=[digest], lesson=is_lesson, pending=False)
        selected[destination] = content
    paths = {path.relative_to(target).as_posix() for path in selected}
    if RULES_BEGIN in original:
        start, rest = original.split(RULES_BEGIN, 1)
        previous, end = rest.split(RULES_END, 1)
        for line in previous.splitlines():
            if line.startswith("- Read `.coldlocker/rules/") and line.endswith("`."):
                paths.add(line[len("- Read `"):-2])
    else:
        start, end = original.rstrip() + "\n\n", "\n"
    paths.difference_update(path.relative_to(target).as_posix() for path in withdrawn)
    if not selected and not withdrawn:
        pending_path = directory.parent / "pending"
        ordinary_file(pending_path)
        if not args.dry_run and pending_path.exists() and not any(entry["pending"] for entry in registry.values()):
            validators.require(pending_path.read_text(encoding="utf-8") == "Rule delivery incomplete; retry deliver-rules.\n",
                               "foreign pending marker preserved")
            pending_path.unlink()
        print("No owned rules changed")
        return 0
    section = (RULES_BEGIN + "\nApply only rules whose `.coldlocker/rules.json` registration has `pending: false`.\n"
               "Read these rules before working in this repository.\n")
    section += "\n".join(f"- Read `{path}`." for path in sorted(paths)) + "\n" + RULES_END
    updated = start + section + end
    if args.dry_run:
        for path in selected:
            print(f"Would copy rule to {path}")
        for path, archive in withdrawn.items():
            print(f"Would withdraw owned rule {path}; archive {archive}")
        print(f"Would manage {instructions}:\n{section}")
    if not args.dry_run:
        if (not withdrawn and all(path.exists() and path.read_bytes() == content for path, content in selected.items())
                and original == updated and registry_path.exists() and read_json(registry_path) == final_registry
                and not (directory.parent / "pending").exists()):
            print("Delivered rules (unchanged)")
            return 0
        validators.require(all(import_snapshot(path) == snapshot for path, snapshot in snapshots.items()),
                           "rule destination changed during preparation; preserved")
        directory.mkdir(parents=True, exist_ok=True)
        pending_path = directory.parent / "pending"
        ordinary_file(pending_path)
        if not pending_path.exists():
            atomic_delivery_write(pending_path, b"Rule delivery incomplete; retry deliver-rules.\n", None)
        provisional = (json.dumps(registry, indent=2) + "\n").encode("utf-8")
        atomic_delivery_write(registry_path, provisional, snapshots[registry_path])
        for path, content in selected.items():
            atomic_delivery_write(path, content, snapshots[path])
        for path, archive in withdrawn.items():
            if path.exists():
                validators.require(import_snapshot(path) == snapshots[path]
                                   and delivery_hash(path.read_bytes()) in registry[path.stem]["hashes"],
                                   "owned rule changed during withdrawal; preserved")
                archive.parent.mkdir(parents=True, exist_ok=True)
                if archive.exists():
                    validators.require(delivery_hash(archive.read_bytes()) == delivery_hash(path.read_bytes()),
                                       "archive changed during withdrawal; preserved")
                else:
                    atomic_delivery_write(archive, path.read_bytes(), snapshots[archive])
                validators.require(import_snapshot(path) == snapshots[path], "owned rule changed before withdrawal; preserved")
                retained = preserve_delivery_file(path, snapshots[path])
                if retained:
                    retained[1].unlink()
        atomic_delivery_write(instructions, updated.encode("utf-8"), snapshots[instructions])
        atomic_delivery_write(registry_path, (json.dumps(final_registry, indent=2) + "\n").encode("utf-8"), provisional)
        pending_path.unlink()
    print(("Would withdraw rules" if args.dry_run else "Withdrew rules") if withdrawing
          else ("Would deliver rules" if args.dry_run else "Delivered rules"))
    return 0


def serve(args):
    from hub_ui import serve as start
    return start(ROOT, args.port, args.scan_home, args.scan_platform, args.scan_project)


def import_executor(args):
    from executor_sources import import_source
    return import_source(args)


HOOK_PROBES = (
    ("owner state write is denied", "Bash", {"command": "rm -rf ~/.coldlocker/guards.json"}, "Only the owner changes"),
    ("public push is denied", "Bash", {"command": "git push origin main; true"}, "Public publication is blocked"),
    ("unrouted dispatch is denied", "mcp__t3-code__delegate_task", {"task": "review this"},
     "Dispatch requires readable coldlocker-role metadata"),
    ("ordinary command is allowed", "Bash", {"command": "git status"}, None),
)


def git_in(root, *command):
    return subprocess.run(["git", "-C", str(root), *command], capture_output=True, text=True, encoding="utf-8")


def probe_passes(root, env, tool, tool_input, reason):
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}
    try:
        result = subprocess.run([sys.executable, "-I", str(root / "hooks/coldlocker_hook.py"), "--root", str(root)],
                                input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8",
                                timeout=60, env=env)
        output = json.loads(result.stdout).get("hookSpecificOutput", {})
        if reason is None:
            return result.returncode == 0 and "permissionDecision" not in output
        return (result.returncode == 0 and output.get("permissionDecision") == "deny"
                and output.get("permissionDecisionReason", "").startswith(reason))
    except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
        return False


def hook_self_test(root):
    passed = True
    with tempfile.TemporaryDirectory() as home:
        env = {**os.environ, "HOME": home, "USERPROFILE": home, "PYTHONDONTWRITEBYTECODE": "1"}
        for title, tool, tool_input, reason in HOOK_PROBES:
            ok = probe_passes(root, env, tool, tool_input, reason)
            print(("PASS" if ok else "FAIL") + ": hook self-test: " + title)
            passed = passed and ok
        expected = Path(home) / ".local/state/coldlocker/decisions.jsonl"
        actual = {p.relative_to(home).as_posix() for p in Path(home).rglob("*")}
        untouched = actual == {".local", ".local/state", ".local/state/coldlocker", expected.relative_to(home).as_posix(), ".local/state/coldlocker/decisions.lock"}
        if untouched:
            try:
                rows = [json.loads(line) for line in expected.read_text(encoding="utf-8").splitlines()]
                untouched = [r.get("verdict") for r in rows] == ["deny", "deny", "deny", "allow"]
            except (OSError, ValueError):
                untouched = False
        print(("PASS" if untouched else "FAIL") + ": hook self-test: wrote only the decision log to its home")
    return passed and untouched


def installed_hook_lessons():
    if not (ROOT / validators.POLICY_PACK).is_file():
        return
    from hooks.install import client_of, command_args, read, targets
    for path, _ in targets(None):
        try:
            raw = read(path)
            if raw is None:
                continue
            doc = json.loads(raw)
            lessons = []
            parser = argparse.ArgumentParser(add_help=False, exit_on_error=False)
            parser.add_argument("--lessons", type=Path)
            for event in ("SessionStart", "SubagentStart"):
                for group in doc.get("hooks", {}).get(event, []):
                    for handler in group.get("hooks", []):
                        if handler.get("type") != "command":
                            continue
                        try:
                            args = command_args(handler.get("command", ""))
                        except ValueError:
                            continue
                        if not any(arg.replace("\\", "/").rsplit("/", 1)[-1] in
                                   ("coldlocker_hook.py", "fleet_hook.py") for arg in args):
                            continue
                        options, _ = parser.parse_known_args(args)
                        directory = os.environ.get("COLDLOCKER_LESSONS") or options.lessons
                        lessons.append(str(directory) if directory is not None else "")
            if lessons:
                for directory in dict.fromkeys(lessons):
                    yield client_of(path), directory
        except (OSError, ValueError, TypeError, AttributeError, argparse.ArgumentError):
            print(f"WARN: {client_of(path)} hook lessons configuration could not be read")


def doctor(args):
    result = checker.doctor(ROOT)
    for client, directory in installed_hook_lessons():
        if directory:
            print(f"PASS: {client} hook injects lessons from {directory}")
        else:
            print(f"WARN: {client} hook injects no lessons. "
                  "Reinstall with hooks/install.py … --hook-lessons <DIR>")
    return result


def post_update_checks(had_pack):
    doctor = subprocess.run([sys.executable, str(ROOT / "hub.py"), "doctor"], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8")
    print(doctor.stdout + doctor.stderr, end="")
    if not had_pack and not (ROOT / validators.POLICY_PACK).is_file():
        print("SKIP: hook self-test: policy pack not installed")
        return doctor.returncode == 0
    return hook_self_test(ROOT) and doctor.returncode == 0


def guard_stats(args):
    if args.days < 1:
        raise ValueError("--days must be positive")
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=args.days - 1)
    today = datetime.now(timezone.utc).date()
    groups = {}
    path = Path.home() / ".local/state/coldlocker/decisions.jsonl"
    snapshots = []
    if path.parent.is_dir():
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(path.parent / "decisions.lock", flags, 0o600), "r+b") as stream:
            lock(stream)
            for source in (path.with_name(path.name + ".1"), path):
                try:
                    snapshots.append(source.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    continue
    for snapshot in snapshots:
        for line in snapshot.splitlines():
            try:
                row = json.loads(line)
                day = datetime.fromisoformat(row["ts"]).date()
                if cutoff <= day <= today:
                    groups.setdefault(day.isoformat(), []).append(row)
            except (ValueError, KeyError, TypeError):
                continue
    if not groups:
        print("no decisions recorded")
        return 0
    def report(label, rows):
        denied = [r for r in rows if r.get("verdict") == "deny"]
        guards = Counter(r.get("guard", "chain") for r in denied)
        print(f"{label}: {len(rows)} calls, {len(denied)} denials ({len(denied) / len(rows):.1%})")
        print("  " + ", ".join(f"{guard}={guards[guard]}" for guard in ("owner", "publication", "dispatch", "chain")))
        reasons = Counter(re.sub(r"(['\"])(?:\\.|(?!\1).)*?\1", "…", r.get("reason", "")) for r in denied)
        for reason, count in reasons.most_common(10):
            print(f"  {count}: {reason}")
    for day, rows in sorted(groups.items()):
        report(day, rows)
    report("Total", [row for rows in groups.values() for row in rows])
    return 0


def update(args):
    branch = git_in(ROOT, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
    if branch != "main":
        raise Refused(f"Update refused: {ROOT} is on {branch or 'a detached HEAD'}, not main. Nothing changed.")
    status = git_in(ROOT, "status", "--porcelain", "--untracked-files=all")
    if status.returncode or status.stdout.strip():
        raise Refused(f"Update refused: {ROOT} has uncommitted changes or git status failed. Nothing changed.")
    old = git_in(ROOT, "rev-parse", "HEAD").stdout.strip()
    had_pack = (ROOT / validators.POLICY_PACK).is_file()
    folder = subprocess.list2cmdline([str(ROOT)]) if os.name == "nt" else shlex.quote(str(ROOT))
    rollback = f"git -C {folder} reset --keep {old}"
    print(f"Rollback SHA: {old}")
    for step in (("fetch", "origin", "main"), ("merge", "--ff-only", "FETCH_HEAD")):
        result = git_in(ROOT, *step)
        if result.returncode:
            print(result.stdout + result.stderr, end="", file=sys.stderr)
            raise Refused(f"Update refused: git {step[0]} failed. HEAD is still {old}.")
    new = git_in(ROOT, "rev-parse", "HEAD").stdout.strip()
    passed = rolled_back = False
    try:
        passed = post_update_checks(had_pack)
    except Exception as error:
        print(f"FAIL: post-update checks raised {type(error).__name__}: {error}")
    finally:
        if not passed:
            rolled_back = git_in(ROOT, "reset", "--keep", old).returncode == 0
    if passed:
        print(f"Updated {old} -> {new}.\nRoll back with: {rollback}")
        return 0
    if not rolled_back:
        raise Refused(f"Update failed checks at {new} and could not roll back. Run: {rollback}")
    raise Refused(f"Update failed checks at {new}; rolled back to {old}.")


MODEL_ENTRY = re.compile(r"^([^#\n]+?): \{provider: ([^,]+), model: ([^,]+), effort: ([^}]+)\}$")
REVIEW_ROLES = {
    "reviewer": ("reviewer", "auditor/verifier", "second-frontier"),
    "reviewer-for-codex": ("reviewer-for-codex",),
    "auditor/verifier": ("auditor/verifier", "reviewer", "second-frontier"),
    "auditor-high-stakes": ("auditor-high-stakes",),
    "gate-verifier": ("gate-verifier", "reviewer", "auditor/verifier", "second-frontier"),
    "gate-high-risk": ("gate-high-risk",),
}
EFFORT_OPTION = {"codex": "reasoningEffort", "claudeAgent": "effort", "grok": "reasoningEffort"}


def canonical_model(name):
    return re.sub(r"[\s._]+", "-", name.strip().lower().rsplit("/", 1)[-1]).strip("-")


def model_route(args):
    try:
        text = Path(args.models).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        return incomplete(f"cannot read {args.models} ({type(error).__name__})")
    routes = {match[1]: dict(provider=match[2], model=match[3], effort=match[4])
              for line in text.splitlines() if (match := MODEL_ENTRY.fullmatch(line))}
    if args.role not in routes and args.role not in REVIEW_ROLES:
        return incomplete("unknown role: " + args.role)
    route = routes.get(args.role)
    if args.builder_model or (args.model_action == "target" and args.role in REVIEW_ROLES):
        if args.role not in REVIEW_ROLES:
            return incomplete("--builder-model applies only to review roles: " + ", ".join(REVIEW_ROLES))
        if not args.builder_model:
            return incomplete("review targets require --builder-model")
        builders = {canonical_model(b) for b in args.builder_model}
        def independent(model):
            model = canonical_model(model)
            return not any(model == b or model.endswith("-" + b) for b in builders)
        candidates = REVIEW_ROLES[args.role]
        if args.role in ("reviewer", "reviewer-for-codex"):
            families = set()
            for builder in builders:
                if re.fullmatch(r"(?:claude-)?(?:fable|opus|sonnet|haiku)(?:-.*)?", builder):
                    families.add("claude")
                elif re.fullmatch(r"(?:gpt-(?:[0-9]+-)*|gpt-)?(?:astra|sol|terra|luna)(?:-.*)?", builder):
                    families.add("codex")
                else:
                    return incomplete("no independent reviewer for unrecognized builder: " + builder)
            if len(families) > 1:
                return incomplete("no independent reviewer for mixed Claude and Codex builders")
            if families:
                candidates = ("reviewer-for-codex",) if "codex" in families else ("reviewer",)
        route = next((routes[role] for role in candidates
                      if role in routes and independent(routes[role]["model"])), None)
    if route is None:
        return incomplete("no independent reviewer for " + args.role if args.builder_model else "unknown role: " + args.role)
    if args.model_action == "get":
        print(json.dumps(route))
        return 0
    option = EFFORT_OPTION.get(route["provider"])
    if route["effort"] != "default" and not option:
        return incomplete(f"provider {route['provider']} does not support explicit effort")
    print(json.dumps(dict(providerInstanceId=route["provider"], model=route["model"],
                          options=[] if route["effort"] == "default" else [{"id": option, "value": route["effort"]}])))
    return 0


def incomplete(reason):
    print("INCOMPLETE: " + reason, file=sys.stderr)
    return 1


POLICY_PACK_COMMANDS = ("sync-lessons", "deliver-rules", "model", "guard-stats")


def parser():
    result = argparse.ArgumentParser(description="Import, link and verify coldlocker content.",
                                     usage="%(prog)s [-h] [--version] <command> ...")
    result.add_argument("--version", action="version", version=f"coldlocker {VERSION}")
    commands = result.add_subparsers(dest="command", required=True, metavar="<command>")
    from connect import register as register_connect
    register_connect(commands, ROOT)
    from scanner import register as register_scan
    register_scan(commands)
    from composio_connector import register
    register(commands)
    __import__("routine_enable").register(commands)
    from remote_publisher import register as register_remote
    register_remote(commands)
    lifecycle.register(commands, sys.modules[__name__])
    packs.register(commands, sys.modules[__name__])
    import_help = {
        "skill": "Copy a skill directory into the local catalog.",
        "rule": "Copy a rule fragment into the local catalog.",
        "subagent": "Copy a subagent definition (one Markdown file) into the local catalog.",
        "command": "Copy a slash command or saved prompt (one Markdown file) into the local catalog.",
        "mcp": "Record a nonsecret MCP server definition (secret:// references only) that the gateway and listing endpoint serve. Never starts or contacts the server.",
        "routine": "Record a routine definition, disabled. Never schedules or runs it.",
        "plugin": "Record plugin metadata only. Never installs or runs it.",
        "connection": "Record connection metadata only. Never contacts the provider or stores credentials.",
    }
    for kind in ("skill", "rule", "subagent", "command", "mcp", "routine", "plugin", "connection"):
        stub = kind in {"mcp", "routine", "plugin", "connection"}
        help_text = import_help[kind] + " Re-importing a changed source updates it when owner and origin match; the previous copy is kept in .hub-store/history/."
        command = commands.add_parser(f"import-{kind}", help=help_text, description=help_text)
        command.add_argument("path", help="Local path" + (", or https://github.com/<owner>/<repo>[/path][@ref]" if kind == "skill" else ""))
        command.add_argument("--owner")
        command.add_argument("--origin", help="Provenance label (default local-import, or github for a GitHub URL)")
        command.add_argument("--approve-risky", action="store_true", help="Import even though the safety scan flagged it for review. Embedded secrets are always refused.")
        if not stub:
            command.add_argument("--name")
        command.set_defaults(action=import_item)
    command = commands.add_parser("import-executor", help="Optional nonsecret source copy from the selected live Executor.")
    command.add_argument("slug")
    command.add_argument("--apply", action="store_true")
    command.add_argument("--approve-risky", action="store_true", help="Import even though the safety scan flagged it for review. Embedded secrets are always refused.")
    command.set_defaults(action=import_executor)
    if (ROOT / "agents_sync.py").is_file():
        from agents_sync import register
        register(commands, ROOT)
    command = commands.add_parser("serve", help="Open the local setup wizard.")
    command.add_argument("--port", type=int, default=0)
    command.add_argument("--scan-home", type=Path)
    command.add_argument("--scan-platform", choices=("linux", "darwin", "win32"))
    command.add_argument("--scan-project", type=Path, action="append", default=[])
    command.set_defaults(action=serve)
    command = commands.add_parser(
        "link", help="Symlink shipped skills into a project (--target) or your home skill dirs (--home).",
        description="Create relative symlinks from the hub's skills into <root>/.claude/skills and <root>/.agents/skills. "
                    "--target links into a project directory inside a git checkout; --home links into ~/.claude/skills and "
                    "~/.agents/skills and needs --yes or a terminal confirmation. Existing files are never replaced: "
                    "in --home mode they are reported as conflicts and skipped. --dry-run prints the plan only.")
    where = command.add_mutually_exclusive_group(required=True)
    where.add_argument("--target", help="Project directory to link into.")
    where.add_argument("--home", action="store_true", help="Link into ~/.claude/skills and ~/.agents/skills.")
    command.add_argument("--yes", action="store_true", help="With --home, create the links without asking.")
    command.add_argument("--dry-run", action="store_true", help="Show what would be linked without changing anything.")
    command.set_defaults(action=link)
    command = commands.add_parser(
        "sync-lessons", help="Mirror the proven, briefed Coldfix lessons into the local catalog as coldfix-owned rules.",
        description="Import each lesson listed in briefing.md whose status is active or enforced and whose verification "
                    "is proven as a rule owned by coldfix, and remove coldfix rules for lessons that were retired, "
                    "withdrawn or unlisted. hub_list_rules and publish-remote --include-local then serve them.")
    command.add_argument("--lessons", help="Coldfix lessons folder (default $COLDLOCKER_LESSONS).")
    command.set_defaults(action=sync_lessons)
    command = commands.add_parser("deliver-rules", help="Deliver rules and proof-gated Coldfix lessons to a repository.")
    command.add_argument("--target", required=True)
    command.add_argument("--rule", action="append", required=True, help="Catalog rule name; repeat to select more.")
    command.add_argument("--reviewed", action="store_true", help=argparse.SUPPRESS)
    command.add_argument("--withdraw", action="store_true", help="Archive selected owned rules and remove their managed instruction links.")
    command.add_argument("--dry-run", action="store_true")
    command.set_defaults(action=deliver_rules)
    commands.add_parser("check", help="Verify schema, license, hashes, ignores and tracked content.").set_defaults(
        action=lambda args: checker.run(ROOT))
    commands.add_parser("doctor", help="Check an installed catalog without enforcing maintainer commit identities.").set_defaults(
        action=doctor)
    model = commands.add_parser("model", help="Print a role's configured route from models.yaml, without cold.")
    model_actions = model.add_subparsers(dest="model_action", required=True)
    for action, help_text in (("get", "Print the role's provider, model and effort."),
                              ("target", "Print the explicit dispatch target for the role.")):
        command = model_actions.add_parser(action, help=help_text, description=help_text +
                                           " Review roles exclude every --builder-model. No usage telemetry is read.")
        command.add_argument("role")
        command.add_argument("--builder-model", action="append", default=[],
                             help="A model that wrote the change; repeat for each builder.")
        command.add_argument("--models", type=Path, default=ROOT / "models.yaml", help="Routing file (default: %(default)s).")
        command.set_defaults(action=model_route)
    stats = commands.add_parser("guard-stats", help="Summarize local hook decisions by day and guard.")
    stats.add_argument("--days", type=int, default=7)
    stats.set_defaults(action=guard_stats)
    commands.add_parser("update", help="Fast-forward main from origin, then run doctor and a hook self-test; roll back if either fails.").set_defaults(
        action=update)
    for title, in_pack in (("core commands", False),
                           ("policy pack commands (optional; see docs/core-vs-policy.md)", True)):
        result.add_argument_group(title)._group_actions.extend(
            choice for choice in commands._choices_actions if (choice.dest in POLICY_PACK_COMMANDS) == in_pack)
    commands.help = argparse.SUPPRESS
    return result


def main():
    args = parser().parse_args()
    try:
        return args.action(args)
    except Refused as refusal:
        print(refusal, file=sys.stderr)
        return 1
    except ValueError as error:
        print("FAIL: " + str(error), file=sys.stderr)
        return 1
    except (OSError, TypeError, KeyError, RecursionError):
        print("FAIL: invalid input, unsafe destination, or filesystem operation failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
