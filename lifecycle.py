import contextlib
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from checks import validators
from store import ordinary_path


HISTORY = ".hub-store/history"
LINKS = ".hub-store/links.json"
KIND_NAMES = {"skill": "skills", "rule": "rules", "mcp": "mcp_servers", "mcp_server": "mcp_servers",
              "routine": "routines", "plugin": "plugins", "connection": "connections"}
SINGULAR = {"skills": "skill", "rules": "rule", "mcp_servers": "mcp_server", "routines": "routine",
            "plugins": "plugin", "connections": "connection"}


def collection_name(value):
    value = KIND_NAMES.get(value, value)
    if value not in validators.COLLECTIONS:
        raise ValueError(f"unknown kind {value!r}; use one of {', '.join(validators.COLLECTIONS)}")
    return value


def rows(hub, root):
    shipped = validators.manifest(hub.read_json(root / "manifest.json"))
    local = validators.read_local(root, hub.read_json)
    result = []
    for source, value in (("shipped", shipped), ("imported", local)):
        for collection, key in validators.COLLECTIONS.items():
            for entry in value.get(collection, []):
                result.append({"kind": collection, "name": entry[key], "owner": entry["owner"],
                               "origin": entry["provenance"]["origin"], "source": source})
    order = list(validators.COLLECTIONS)
    return sorted(result, key=lambda row: (order.index(row["kind"]), row["name"]))


def list_items(args, hub):
    found = [row for row in rows(hub, hub.ROOT) if args.kind in (None, row["kind"])]
    if args.json:
        print(json.dumps(found, indent=2))
        return 0
    if not found:
        print("No items")
        return 0
    header = {"kind": "KIND", "name": "NAME", "owner": "OWNER", "origin": "ORIGIN", "source": "SOURCE"}
    table = [header, *found]
    widths = {field: max(len(row[field]) for row in table) for field in header}
    for row in table:
        print("  ".join(row[field].ljust(widths[field]) for field in header).rstrip())
    return 0


def confirmed(args, question):
    if args.yes:
        return True
    validators.require(sys.stdin.isatty(), "confirmation needed: re-run with --yes when not at a terminal")
    try:
        return input(question + " [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        raise ValueError("confirmation needed: re-run with --yes when input is unavailable") from None


def changed_targets(operations):
    return [target for target, staged in operations
            if target.exists() and (staged is None or validators.digest(target) != validators.digest(staged))]


def history_base(root, collection, item_name):
    base = root / HISTORY / collection / item_name
    ordinary_path(base)
    for directory in (base, *base.parents):
        validators.require(not directory.exists() or directory.is_dir(), "history parent must be a directory")
    return base


def keep(root, collection, item_name, held):
    base = history_base(root, collection, item_name)
    directory = root
    for part in base.relative_to(root).parts:
        directory = directory / part
        directory.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    shutil.copytree(held, base / stamp)


@contextlib.contextmanager
def retained(root, collection, item_name, paths):
    paths = list(paths)
    if not paths:
        yield
        return
    history_base(root, collection, item_name)
    catalog = root / validators.LOCAL
    with tempfile.TemporaryDirectory() as temporary:
        held = Path(temporary)
        for path in paths:
            copy = held / path.relative_to(catalog)
            copy.parent.mkdir(parents=True, exist_ok=True)
            if path.is_dir():
                shutil.copytree(path, copy)
            else:
                shutil.copyfile(path, copy)
        yield
        try:
            keep(root, collection, item_name, held)
        except OSError as error:
            print(f"Warning: published, but could not keep the previous copy under {HISTORY}/: {error}", file=sys.stderr)


def read_links(root):
    path = root / LINKS
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return [item for item in value["links"] if isinstance(item, dict) and set(item) == {"link", "skill"}
                and all(isinstance(field, str) for field in item.values())]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def record_links(hub, root, created):
    if not created:
        return
    current = {item["link"]: item for item in read_links(root)}
    for link, skill in created:
        current[str(link)] = {"link": str(link), "skill": str(skill)}
    hub.ordinary_parents((root / LINKS).parent)
    hub.ordinary_file(root / LINKS)
    hub.private_store(root)
    hub.write_json(root / LINKS, {"version": 1, "links": sorted(current.values(), key=lambda item: item["link"])})


def dangling_links(root, skill_path):
    return [item["link"] for item in read_links(root)
            if item["skill"] == str(skill_path) and Path(item["link"]).is_symlink()]


def remove(args, hub, root=None):
    root = hub.ROOT if root is None else root
    collection = collection_name(args.kind)
    name = validators.name(args.name)
    key = validators.COLLECTIONS[collection]
    singular = SINGULAR[collection]
    shipped = validators.manifest(hub.read_json(root / "manifest.json"))
    validators.require(not any(item[key] == name for item in shipped.get(collection, [])),
                       f"{name} is a shipped {singular}; shipped items cannot be removed")
    hub.ordinary_file(root / "manifest.json")
    hub.ordinary_file(root / validators.LOCAL / "manifest.json")
    value = validators.read_local(root, hub.read_json)
    entry = next((item for item in value.get(collection, []) if item[key] == name), None)
    validators.require(entry is not None, f"no imported {singular} named {name}; run hub.py list")
    owner = validators.label(args.owner or "agentlocker")
    validators.require(entry["owner"] == owner,
                       f"{name} is owned by {entry['owner']}; only that owner may remove it (--owner {entry['owner']})")
    catalog = root / validators.LOCAL
    destination = catalog / entry["path"]
    hub.import_preflight(value, collection, name, destination, entry["path"], owner,
                         entry["provenance"]["origin"], singular if singular in ("skill", "routine") else "file")
    targets = [destination]
    if singular == "routine":
        prompt = hub.read_json(destination).get("prompt")
        sibling = destination.parent / prompt if isinstance(prompt, str) else None
        if sibling is not None and len(Path(prompt).parts) == 1 and (sibling.exists() or sibling.is_symlink()):
            hub.ordinary_file(sibling)
            validators.require(sibling.is_file(), "prompt must be an ordinary file")
            targets.append(sibling)
    skill_links = dangling_links(root, destination) if singular == "skill" else []
    print(f"Remove {singular.replace('_', ' ')} {name} (owner {entry['owner']}, origin {entry['provenance']['origin']}); "
          f"a copy is kept under {HISTORY}/")
    for link in skill_links:
        print(f"Dangling link (created by hub.py link, left in place): {link}")
    if not confirmed(args, "Remove it?"):
        print("Cancelled; nothing changed")
        return 1
    snapshots = {path: hub.import_snapshot(path) for path in (root / "manifest.json", catalog / "manifest.json", *targets)}
    with tempfile.TemporaryDirectory() as temporary:
        staged_root = Path(temporary)
        remaining = [item for item in value[collection] if item[key] != name]
        staged_manifest = staged_root / "catalog" / "manifest.json"
        staged_manifest.parent.mkdir(parents=True)
        hub.write_json(staged_manifest, validators.manifest({**value, collection: remaining}))
        operations = [(path, None) for path in targets]
        if singular == "mcp_server":
            snapshots[root / "config.json"] = hub.import_snapshot(root / "config.json")
            if name in hub._read_bindings(root)["servers"]:
                operations.extend(hub.stage_path_bindings(root, staged_root, name, {}, True, snapshots,
                                                          hub.read_json(destination)))
        operations.append((catalog / "manifest.json", staged_manifest))
        with retained(root, collection, name, changed_targets(operations[:len(targets)])):
            hub.publish_import(operations, snapshots)
    print(f"Removed {collection}: {name}")
    if singular == "routine":
        left = sorted(item.name for item in destination.parent.iterdir())
        if left:
            print(f"Left in place (not created by import): {', '.join(left)} in {destination.parent}")
        else:
            destination.parent.rmdir()
    if skill_links:
        print("Delete the dangling links yourself; hub.py never removes files in other directories.")
    return 0


def register(commands, hub):
    command = commands.add_parser("list", help="List shipped and imported items with kind, name, owner, origin and source.")
    command.add_argument("--kind", type=collection_name, choices=list(validators.COLLECTIONS), metavar="KIND",
                         help="Only this kind: " + ", ".join(validators.COLLECTIONS) + ".")
    command.add_argument("--json", action="store_true", help="Print the rows as JSON.")
    command.set_defaults(action=lambda args: list_items(args, hub))
    command = commands.add_parser(
        "remove", help="Remove an imported item and its catalog entry; shipped items are refused.",
        description="Remove an imported item. Deletes its files and local manifest entry with the same staged, "
                    "rollback-safe publish as import, keeps a copy under .hub-store/history/, refuses shipped items "
                    "and items owned by another owner, and reports links made by hub.py link that would now dangle.")
    command.add_argument("kind", type=collection_name, metavar="KIND",
                         help="One of: " + ", ".join(validators.COLLECTIONS) + " (singular names also work).")
    command.add_argument("name")
    command.add_argument("--owner", help="The owner acting on the item (default agentlocker); must match the item's owner.")
    command.add_argument("--yes", action="store_true", help="Remove without asking; required when not at a terminal.")
    command.set_defaults(action=lambda args: remove(args, hub))
