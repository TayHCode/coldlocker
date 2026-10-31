import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import safety
from checks import checker, validators
from gateway_render import PATH_BINDINGS, _read_bindings, _references, store_source_path_bindings
from importers import skill_package, skill_package_texts
import lifecycle
from lifecycle import changed_targets, confirmed, record_links, retained
from scanner import home_pattern, located, texts
from store import FileStore, ordinary_path


ROOT = Path(__file__).resolve().parent


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
    return validators.layered(root, validators.manifest(read_json(root / "manifest.json")), read_json)


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
    return safety.review(kind, payload, files, known_hosts(read_manifest(root), root))


def screen(kind, payload, files, approve=False, root=ROOT):
    found = review(kind, payload, files, root)
    blocked = [item for item in found if item["blocking"]]
    if not found or (approve and not blocked):
        return
    lines = "; ".join(f"{item['reason']} ({item['location']})" for item in found)
    if blocked:
        raise Refused(f"REFUSED: contains a secret; remove it and keep it in the private store. {lines}")
    raise Refused(f"REFUSED: needs review before import. {lines}. Inspect it, then re-run with --approve-risky to import it anyway.")


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
        shutil.copytree(source, staged)
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
    validators.require(entry["owner"] == owner, f"existing entry is owned by {entry['owner']}; pass --owner {entry['owner']} to update it")
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


def publish_import(operations, snapshots):
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


def import_item(args, root=ROOT):
    ordinary_file(root / "manifest.json")
    ordinary_file(root / validators.LOCAL / "manifest.json")
    snapshots = {path: import_snapshot(path) for path in
                 (root / "manifest.json", root / validators.LOCAL / "manifest.json")}
    shipped = validators.manifest(read_json(root / "manifest.json"))
    value = validators.read_local(root, read_json)
    catalog = root / validators.LOCAL
    source = Path(args.path)
    validators.require(not source.is_symlink(), "input must not be a symlink")
    owner = validators.label(args.owner or "agentlocker")
    origin = validators.label(args.origin)
    kind = args.command.removeprefix("import-")
    content = None
    prompt = None
    stored = None
    skill_files = None
    if kind == "skill":
        item_name = validators.name(args.name or source.name)
        skill_files = skill_package_texts(source)
        collection, relative = "skills", f"skills/{item_name}"
    elif kind == "rule":
        validators.require(source.is_file() and source.suffix == ".md", "rule must be a Markdown file")
        item_name = (args.name or source.stem.lower())
        validators.require(isinstance(item_name, str) and validators.NAME.fullmatch(item_name),
                           f"invalid name or id: {item_name!r}; pass --name with a valid name")
        collection, relative = "rules", f"rules/{item_name}.md"
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
    if kind != "connection":
        files = skill_files if kind == "skill" else texts(source) if kind == "rule" else [("prompt.md", prompt.read_text(encoding="utf-8"))] if prompt else []
        screen(kind, content, files, getattr(args, "approve_risky", False), root)
    if kind == "skill":
        skill_package(source, item_name, skill_files)
    if kind == "rule":
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
    with tempfile.TemporaryDirectory() as temporary:
        staged_root = Path(temporary)
        staged = staged_root / "catalog" / relative
        staged.parent.mkdir(parents=True)
        if kind == "skill":
            shutil.copytree(source, staged, ignore=shutil.ignore_patterns(".git"))
        elif kind == "rule":
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
        with retained(root, collection, item_name, previous):
            publish_import(operations, snapshots)
    print(f"Recorded {collection}: {item_name}")
    return 0


def confirm_home_link(args, pending):
    if args.yes or args.dry_run:
        return True
    for source, destination in pending:
        print(f"Would link {destination} -> {os.path.relpath(source, destination.parent)}")
    return confirmed(args, f"Create {len(pending)} link(s) in your home skill directories?")


def link(args):
    from consistency_check import require_consistent
    require_consistent(ROOT)
    home = Path.home().resolve()
    from connect import SKILLS_REACH
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


def deliver_rules(args):
    from consistency_check import require_consistent
    withdrawing = getattr(args, "withdraw", False)
    if not withdrawing:
        require_consistent(ROOT)
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


def parser():
    result = argparse.ArgumentParser(description="Import, link and verify agentlocker content.")
    commands = result.add_subparsers(dest="command", required=True)
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
    import_help = {
        "skill": "Copy a skill directory into the local catalog.",
        "rule": "Copy a rule fragment into the local catalog.",
        "mcp": "Record a nonsecret MCP server definition (secret:// references only) that the gateway and listing endpoint serve. Never starts or contacts the server.",
        "routine": "Record a routine definition, disabled. Never schedules or runs it.",
        "plugin": "Record plugin metadata only. Never installs or runs it.",
        "connection": "Record connection metadata only. Never contacts the provider or stores credentials.",
    }
    for kind in ("skill", "rule", "mcp", "routine", "plugin", "connection"):
        stub = kind in {"mcp", "routine", "plugin", "connection"}
        help_text = import_help[kind] + " Re-importing a changed source updates it when owner and origin match; the previous copy is kept in .hub-store/history/."
        command = commands.add_parser(f"import-{kind}", help=help_text, description=help_text)
        command.add_argument("path")
        command.add_argument("--owner")
        command.add_argument("--origin", default="local-import")
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
        action=lambda args: checker.doctor(ROOT))
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
