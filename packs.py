import json
import os
import tempfile
from pathlib import Path

from store import ordinary_path


DEFINITIONS = "packs.json"
STATE = ".hub-store/packs.json"
EARLIER_INSTALL = (".hub-store/catalog/manifest.json", ".hub-store/links.json", ".hub-store/calls.jsonl")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(ordinary_path(path).read_text(encoding="utf-8"))


def definitions(root):
    value = read(Path(root) / DEFINITIONS)
    require(isinstance(value, dict) and set(value) == {"schema_version", "always", "packs"}
            and value["schema_version"] == 1, "invalid packs.json fields")
    groups = [value["always"], *value["packs"].values()] if isinstance(value["packs"], dict) else []
    require(groups and all(isinstance(group, list) and all(isinstance(name, str) for name in group) for group in groups),
            "packs.json lists must hold skill names")
    return value["always"], value["packs"]


def covered(root, shipped):
    shipped_names = [entry["name"] for entry in shipped.get("skills", [])]
    if not ordinary_path(Path(root) / DEFINITIONS).exists():
        return shipped_names, {}
    always, packs = definitions(root)
    policy_pack = ordinary_path(Path(root) / "policy-pack.json")
    if policy_pack.exists():
        packs = {**packs, "pstack": packs.get("pstack", []) + [entry["name"] for entry in read(policy_pack)["skills"]]}
    members = always + [name for names in packs.values() for name in names]
    require(len(members) == len(set(members)) and set(members) == set(shipped_names),
            "packs.json must list every shipped skill exactly once")
    return always, packs


def enabled(root, packs):
    path = ordinary_path(Path(root) / STATE)
    if not path.exists():
        earlier = any(ordinary_path(Path(root) / marker).exists() for marker in EARLIER_INSTALL)
        return sorted(packs) if earlier else []
    value = read(path)
    require(isinstance(value, dict) and set(value) == {"version", "enabled"} and value["version"] == 1
            and isinstance(value["enabled"], list) and value["enabled"] == sorted(set(value["enabled"]))
            and set(value["enabled"]) <= set(packs),
            f"invalid {STATE}; fix or delete it, then run python3 hub.py pack list")
    return value["enabled"]


def visible(root, shipped):
    always, packs = covered(root, shipped)
    keep = set(always).union(*(packs[name] for name in enabled(root, packs)))
    return {**shipped, "skills": [entry for entry in shipped.get("skills", []) if entry["name"] in keep]}


def save(root, names, replace=True):
    path = ordinary_path(Path(root) / STATE)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".packs-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "enabled": sorted(set(names))}, stream, indent=2)
            stream.write("\n")
        if replace:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
    finally:
        Path(temporary).unlink(missing_ok=True)


def pin(root):
    if ordinary_path(Path(root) / DEFINITIONS).exists() and not ordinary_path(Path(root) / STATE).exists():
        save(root, enabled(root, definitions(root)[1]), replace=False)


def change(args, hub):
    root = hub.ROOT
    _, packs = covered(root, hub.validators.shipped(root, hub.read_json))
    require(args.name in packs, f"unknown pack {args.name}; run python3 hub.py pack list")
    current = set(enabled(root, packs))
    wanted = current | {args.name} if args.action_name == "add" else current - {args.name}
    if wanted == current:
        print(f"Pack {args.name} is already {'on' if args.name in current else 'off'}; nothing changed")
        return 0
    save(root, wanted)
    count = len(packs[args.name])
    if args.action_name == "add":
        print(f"Added pack {args.name}: {count} shipped skills are listed again")
        return 0
    print(f"Removed pack {args.name}: its {count} shipped skills are no longer listed; their files stay in skills/")
    removed = {str(root / "skills" / name) for name in packs[args.name]}
    for link in (item["link"] for item in hub.lifecycle.read_links(root) if item["skill"] in removed):
        print(f"Link left in place (created by hub.py link): {link}")
    return 0


def show(args, hub):
    root = hub.ROOT
    always, packs = covered(root, hub.validators.shipped(root, hub.read_json))
    on = set(enabled(root, packs))
    print(f"Always listed: {', '.join(always)}")
    for name in sorted(packs):
        print(f"{name}  {'on' if name in on else 'off'}  {len(packs[name])} skills")
    return 0


def register(commands, hub):
    command = commands.add_parser(
        "pack", help="List, add or remove packs of shipped skills.",
        description="Shipped skills come in opt-in packs. A new install lists only hub-discover and your own imports. "
                    "An install that already had imports, links or MCP calls before packs existed keeps every pack on. "
                    "Adding or removing a pack changes only what is listed and linked; no skill files are deleted.")
    actions = command.add_subparsers(dest="action_name", required=True)
    actions.add_parser("list", help="Show each pack and whether it is on.").set_defaults(action=lambda args: show(args, hub))
    for name, text in (("add", "List a pack's skills."), ("remove", "Stop listing a pack's skills.")):
        action = actions.add_parser(name, help=text)
        action.add_argument("name")
        action.set_defaults(action=lambda args: change(args, hub))
