import json
import os
import re
import subprocess
from pathlib import Path

from portable import product_env
from checks.scan import scan
from checks.validators import COLLECTIONS, LOCAL, POLICY_PACK, digest, layered, metadata, policy_file, require, routine, shipped


def hooks_registered(root):
    home = Path.home()
    configs = (home / ".claude" / "settings.json", Path(os.environ.get("CODEX_HOME") or home / ".codex") / "hooks.json")
    folders = {f"{Path(path).as_posix()}/hooks/" for path in (root, os.path.realpath(root))}
    for config in configs:
        try:
            text = config.read_text(encoding="utf-8").replace("\\\\", "/").replace("\\", "/")
        except OSError:
            continue
        if any(folder in text for folder in folders):
            return True
    return False


def instruction_consistency(root):
    if not (root / POLICY_PACK).is_file():
        require(not hooks_registered(root), f"{POLICY_PACK} is missing but agent configs still run this checkout's hooks; restore it")
        return "policy pack not installed"
    from consistency_check import require_consistent
    require_consistent(root)


def check_license(root):
    text = " ".join((root / "LICENSE").read_text(encoding="utf-8").split())
    grant = ('Permission is hereby granted, free of charge, to any person obtaining a copy '
             'of this software and associated documentation files (the "Software"), to deal '
             'in the Software without restriction, including without limitation the rights '
             'to use, copy, modify, merge, publish, distribute, sublicense, and/or sell '
             'copies of the Software, and to permit persons to whom the Software is '
             'furnished to do so, subject to the following conditions:')
    require(text.startswith("MIT License") and grant in text, "MIT title and permission grant required")


def check_content(root):
    value = layered(root, shipped(root))
    for collection in COLLECTIONS:
        for entry in value.get(collection, []):
            path = root / entry["path"]
            require(path.exists() and not path.is_symlink(), "manifest path is missing or symlinked")
            require(path.resolve().is_relative_to(root.resolve()), "manifest path escapes repository")
            require(path.is_dir() if collection == "skills" else path.is_file(), "incorrect content type")
            require(digest(path) == entry["sha256"], "content hash mismatch")
            if collection == "routines":
                routine(json.loads(path.read_text(encoding="utf-8")))
            if collection in ("plugins", "connections"):
                metadata(json.loads(path.read_text(encoding="utf-8")), "plugin" if collection == "plugins" else "connection")


def check_ignored(root):
    for path in ("config.json", ".hub-store/"):
        ignored = subprocess.run(["git", "check-ignore", "--no-index", "-q", "--", path],
                                 cwd=root, capture_output=True)
        require(ignored.returncode == 0, "local config and store must be gitignored")
    tracked = subprocess.run(["git", "ls-files", "-z", "--", "config.json", ".hub-store"],
                             cwd=root, capture_output=True)
    require(tracked.returncode == 0 and not tracked.stdout, "local config or store is tracked")


def check_packs(root, installed=True):
    import packs
    if not (root / packs.DEFINITIONS).exists():
        return "no packs.json, so every shipped skill is listed"
    (packs.visible if installed else packs.covered)(root, shipped(root))


def check_scan(root):
    errors = scan(root)
    require(not errors, "\n".join(errors))


MARKDOWN_LINK = re.compile(r"\]\(<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\)|^ {0,3}\[[^\]]+\]:\s*<?([^\s>]+)", re.M)


def missing_doc_links(root):
    pack_absent = not (root / POLICY_PACK).is_file()
    missing = []
    for document in sorted([*root.glob("*.md"), *root.glob("docs/**/*.md")]):
        text = re.sub(r"(```|~~~).*?\1|`[^`\n]*`", "", document.read_text(encoding="utf-8"), flags=re.S)
        for inline, reference in MARKDOWN_LINK.findall(text):
            target = inline or reference
            path = target.split("#", 1)[0]
            if not path or re.match(r"[a-z][a-z0-9+.-]*:", path, re.I):
                continue
            target_path = (document.parent / path).resolve()
            in_pack = target_path.is_relative_to(root.resolve()) and policy_file(target_path.relative_to(root.resolve()).as_posix())
            if not target_path.exists() and not (pack_absent and in_pack):
                missing.append(f"{document.relative_to(root).as_posix()} -> {target}")
    return missing


def check_doc_links(root):
    missing = missing_doc_links(root)
    require(not missing, "missing link targets: " + ", ".join(missing))


def check_commit_emails(root):
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root, capture_output=True, text=True, encoding="utf-8")
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root.resolve():
        return "not a git checkout"
    allowlist = root / "config" / "commit-email-allowlist.txt"
    require(allowlist.is_file(), "config/commit-email-allowlist.txt is missing")
    allowed = {line.strip().lower() for line in allowlist.read_text(encoding="utf-8").splitlines() if line.strip()}
    head_exists = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=root, capture_output=True)
    if head_exists.returncode != 0:
        unborn = subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=root, capture_output=True)
        require(unborn.returncode == 0, "cannot resolve checked-out history")
        return
    revisions = ["HEAD"]
    if os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
        base = product_env("COLDLOCKER_PR_BASE", "")
        head = product_env("COLDLOCKER_PR_HEAD", "")
        parents = subprocess.run(["git", "rev-list", "--parents", "-n", "1", "HEAD"],
                                 cwd=root, capture_output=True, text=True, encoding="utf-8")
        fields = parents.stdout.split()
        if parents.returncode == 0:
            revisions.append("HEAD")
        if parents.returncode == 0 and len(fields) == 3 and fields[1:] == [base, head]:
            revisions = [base + ".." + head]
    log = subprocess.run(["git", "log", *revisions, "--format=%ae%x00%ce"],
                         cwd=root, capture_output=True, text=True, encoding="utf-8")
    require(log.returncode == 0, "git log failed")
    rejected = set()
    for line in log.stdout.splitlines():
        author, committer = line.split("\0", 1)
        if author.lower() not in allowed:
            rejected.add(author)
        if committer.lower() not in allowed and not (
                author.lower() in allowed and re.fullmatch(r"noreply[@]github[.]com", committer)):
            rejected.add(committer)
    require(not rejected, "commit emails not on the allowlist: " + ", ".join(sorted(rejected)))


def check_installation_state(root):
    from store import FileStore, ordinary_path
    ordinary_path(root / ".hub-store")
    ordinary_path(root / "config.json")
    if (root / "config.json").exists():
        settings = json.loads((root / "config.json").read_text(encoding="utf-8"))
        require(isinstance(settings, dict), "invalid installation configuration")
    FileStore(root)
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root,
                         capture_output=True, text=True, encoding="utf-8")
    if top.returncode == 0 and Path(top.stdout.strip()).resolve() == root.resolve():
        check_ignored(root)


def check_catalog_scan(root):
    from store import ordinary_path
    base = shipped(root)
    require(not any(entry["path"].startswith(LOCAL + "/") for kind in COLLECTIONS for entry in base.get(kind, [])),
            "shipped manifest must not reference the local catalog")
    value = layered(root, base)
    owner_store = Path(os.path.abspath(root / LOCAL))
    paths, private, shipped_reach = {Path("manifest.json")}, set(), set()
    if (root / POLICY_PACK).is_file():
        paths.add(Path(POLICY_PACK))
    local = root / LOCAL / "manifest.json"
    if local.exists():
        ordinary_path(local)
        paths.add(local.relative_to(root))
        private.add(local.relative_to(root).as_posix())
    for collection, entries in ((kind, value.get(kind, [])) for kind in COLLECTIONS):
        for entry in entries:
            owned = entry["path"].startswith(LOCAL + "/")
            path = ordinary_path(root / entry["path"])
            selected = ([item for item in path.rglob("*") if "__pycache__" not in item.relative_to(path).parts]
                        if path.is_dir() else [path])
            if collection == "routines":
                definition = routine(json.loads(path.read_text(encoding="utf-8")))
                selected.append(ordinary_path(path.parent / definition["prompt"]))
            for item in selected:
                ordinary_path(item)
                require(item.is_file() or item.is_dir(), "catalog contains a nonordinary file")
                if item.is_file():
                    paths.add(item.relative_to(root))
                    if owned and item.is_relative_to(owner_store):
                        private.add(item.relative_to(root).as_posix())
                    elif not owned:
                        shipped_reach.add(item.relative_to(root).as_posix())
    errors = scan(root, paths=paths)
    expected = [error for error in errors
                if error.endswith(": private-pattern") and error.rsplit(":", 2)[0] in private - shipped_reach]
    if expected:
        print(f"NOTE: {len(expected)} line(s) of private catalog content match your private patterns "
              "(expected; publish-remote sends local items only with --include-local)")
    errors = [error for error in errors if error not in expected]
    require(not errors, "\n".join(errors))


def doctor(root):
    return report([
        ("instruction consistency", lambda: instruction_consistency(root)),
        ("manifest schema", lambda: shipped(root)),
        ("MIT license", lambda: check_license(root)),
        ("manifest content hashes", lambda: check_content(root)),
        ("skill packs", lambda: check_packs(root)),
        ("installation state", lambda: check_installation_state(root)),
        ("catalog secret and private path scan", lambda: check_catalog_scan(root)),
    ])


def run(root):
    checks = [
        ("instruction consistency", lambda: instruction_consistency(root)),
        ("manifest schema", lambda: shipped(root)),
        ("MIT license", lambda: check_license(root)),
        ("manifest content hashes", lambda: check_content(root)),
        ("skill packs", lambda: check_packs(root, installed=False)),
        ("local state ignored and untracked", lambda: check_ignored(root)),
        ("secret and private path scan", lambda: check_scan(root)),
        ("commit emails allowlisted", lambda: check_commit_emails(root)),
        ("doc link targets exist", lambda: check_doc_links(root)),
    ]
    return report(checks)


def report(checks):
    passed = True
    for title, action in checks:
        try:
            skipped = action()
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
            print(f"FAIL: {title}: {error}")
            passed = False
        else:
            print(f"SKIP: {title}: {skipped}" if isinstance(skipped, str) else f"PASS: {title}")
    return 0 if passed else 1
