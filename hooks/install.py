import argparse
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import json
import os
import shlex
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from consistency_check import require_consistent, violations

HOOK = Path(__file__).resolve().parent / "keep_going.py"


def hook_command(python, powershell=False, hook=HOOK):
    if os.name != "nt":
        return f"{shlex.quote(python)} -I {shlex.quote(hook.as_posix())}"
    quote = "'" if powershell else '"'
    def quoted(value):
        return quote + str(value).replace(quote, quote * 2) + quote
    return ("& " if powershell else "") + f"{quoted(Path(python).as_posix())} -I {quoted(hook.as_posix())}"


def without_ours(groups, owned_commands):
    kept = []
    for group in groups:
        handlers = [h for h in group.get("hooks", []) if h.get("command") not in owned_commands]
        if handlers:
            kept.append({**group, "hooks": handlers})
    return kept


def merged(doc, command, owned_commands=()):
    hooks = doc.setdefault("hooks", {})
    owned = {*owned_commands, command}
    expected = {"hooks": [{"type": "command", "command": command, "timeout": 10}]}
    matches = [(group, handler) for group in hooks.get("Stop", []) for handler in group.get("hooks", [])
               if handler.get("command") in owned]
    if matches == [(expected, expected["hooks"][0])]:
        return doc
    hooks["Stop"] = without_ours(hooks.get("Stop", []), owned) + [expected]
    return doc


def targets(home):
    codex = Path(os.environ["CODEX_HOME"]) if home is None and os.environ.get("CODEX_HOME") else (home or Path.home()) / ".codex"
    return ((home or Path.home()) / ".claude" / "settings.json", False), (codex / "hooks.json", os.name == "nt")


def safe_path(path):
    for parent in (*reversed(path.parents), path):
        if parent.is_symlink():
            raise ValueError(f"refusing symlink {parent}")
        if parent.exists() and parent != path and not parent.is_dir():
            raise ValueError(f"refusing non-directory {parent}")
    if path.exists() and not path.is_file():
        raise ValueError(f"refusing non-regular file {path}")


def read(path):
    safe_path(path)
    return path.read_bytes() if path.exists() else None


def atomic_write(path, data, mode=0o600):
    safe_path(path)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def locked(paths):
    with ExitStack() as stack:
        for parent in sorted({path.parent for path, _ in paths}, key=str):
            safe_path(parent / "settings.json")
            if parent.is_dir():
                lock = parent / ".agentlocker-hook-lock"
                lock.mkdir(mode=0o700)
                stack.callback(lock.rmdir)
        yield


def plan(paths, python, hook=HOOK, owned_commands=()):
    changes = []
    for path, powershell in paths:
        old = read(path)
        if not path.parent.is_dir():
            print(f"skip {path}: client not installed")
            continue
        command = hook_command(python, powershell, hook)
        doc = json.loads(old.decode("utf-8")) if old and old.strip() else {}
        updated = merged(deepcopy(doc), command, owned_commands)
        new = old if updated == doc else (json.dumps(updated, indent=2) + "\n").encode("utf-8")
        if new != old:
            changes.append((path, old, new))
        else:
            print(f"unchanged {path}")
    return changes


def apply(changes, backup=True):
    for path, old, new in changes:
        if read(path) != old:
            raise ValueError(f"changed during installation: {path}")
        if old is not None and backup:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            destination = path.with_name(f"{path.name}.bak-agentlocker-{stamp}")
            with open(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
                f.write(old)
            print(f"backup {destination}")
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
        atomic_write(path, new, mode)
        print(f"wrote {path}")


def install(paths, python, write, hook=HOOK, owned_commands=()):
    require_consistent(HOOK.parent.parent)
    safe_path(hook)
    if list(violations(hook.read_text(encoding="utf-8"))):
        raise ValueError("supplied hook fails instruction consistency")
    paths = list(paths)
    if write:
        with locked(paths):
            apply(plan(paths, python, hook, owned_commands))
    else:
        for path, _, _ in plan(paths, python, hook, owned_commands):
            print(f"would write {path}: owned Stop hook only; other settings preserved")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install the keep-going Stop hook for Claude Code and Codex.")
    parser.add_argument("--home", type=Path, help="home directory; default is the current user's")
    parser.add_argument("--write", action="store_true", help="write changes; default prints a safe summary")
    parser.add_argument("--owned-command", action="append", default=[], help="exact prior owned command to replace when its interpreter or quoting changes")
    args = parser.parse_args(argv)
    try:
        install(targets(args.home), sys.executable, args.write, owned_commands=args.owned_command)
    except (ValueError, OSError, TypeError, AttributeError) as exc:
        parser.exit(1, f"install refused: {type(exc).__name__}\n")


if __name__ == "__main__":
    main()
