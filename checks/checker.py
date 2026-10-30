import json
import os
import re
import subprocess
from pathlib import Path

from consistency_check import require_consistent
from checks.scan import scan
from checks.validators import COLLECTIONS, digest, layered, manifest, metadata, require, routine


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
    value = layered(root, manifest(json.loads((root / "manifest.json").read_text(encoding="utf-8"))))
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


def check_scan(root):
    errors = scan(root)
    require(not errors, "\n".join(errors))


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
        base = os.environ.get("AGENTLOCKER_PR_BASE", "")
        head = os.environ.get("AGENTLOCKER_PR_HEAD", "")
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
    value = layered(root, manifest(json.loads((root / "manifest.json").read_text(encoding="utf-8"))))
    paths = {Path("manifest.json")}
    local = root / ".hub-store" / "catalog" / "manifest.json"
    if local.exists():
        ordinary_path(local)
        paths.add(local.relative_to(root))
    for collection, entries in ((kind, value.get(kind, [])) for kind in COLLECTIONS):
        for entry in entries:
            path = ordinary_path(root / entry["path"])
            selected = list(path.rglob("*")) if path.is_dir() else [path]
            if collection == "routines":
                definition = routine(json.loads(path.read_text(encoding="utf-8")))
                selected.append(ordinary_path(path.parent / definition["prompt"]))
            for item in selected:
                ordinary_path(item)
                require(item.is_file() or item.is_dir(), "catalog contains a nonordinary file")
                if item.is_file():
                    paths.add(item.relative_to(root))
    errors = scan(root, paths=paths)
    require(not errors, "\n".join(errors))


def doctor(root):
    return report([
        ("instruction consistency", lambda: require_consistent(root)),
        ("manifest schema", lambda: manifest(json.loads((root / "manifest.json").read_text(encoding="utf-8")))),
        ("MIT license", lambda: check_license(root)),
        ("manifest content hashes", lambda: check_content(root)),
        ("installation state", lambda: check_installation_state(root)),
        ("catalog secret and private path scan", lambda: check_catalog_scan(root)),
    ])


def run(root):
    checks = [
        ("instruction consistency", lambda: require_consistent(root)),
        ("manifest schema", lambda: manifest(json.loads((root / "manifest.json").read_text(encoding="utf-8")))),
        ("MIT license", lambda: check_license(root)),
        ("manifest content hashes", lambda: check_content(root)),
        ("local state ignored and untracked", lambda: check_ignored(root)),
        ("secret and private path scan", lambda: check_scan(root)),
        ("commit emails allowlisted", lambda: check_commit_emails(root)),
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
