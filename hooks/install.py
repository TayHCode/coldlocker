import argparse
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from hashlib import sha256
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
COLDLOCKER_HOOK = HOOK.with_name("coldlocker_hook.py")
HOOK_EVENTS = ("SessionStart", "SubagentStart", "UserPromptSubmit", "PreToolUse")


def hook_command(python, powershell=False, hook=HOOK, args=()):
    if os.name != "nt":
        return " ".join(shlex.quote(arg) for arg in (python, "-I", hook.as_posix(), *args))
    quote = "'" if powershell else '"'
    def quoted(value):
        return quote + str(value).replace(quote, quote * 2) + quote
    return (("& " if powershell else "") + f"{quoted(Path(python).as_posix())} -I {quoted(hook.as_posix())}"
            + "".join(" " + quoted(arg) for arg in args))


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


def recalled(doc, command, owned_commands, events=("UserPromptSubmit", "PreToolUse"), timeout=10, verified=False):
    hooks = doc.setdefault("hooks", {})
    for event in events:
        groups = hooks.setdefault(event, [])
        for owned in owned_commands:
            handler = {"type": "command", "command": owned, "timeout": timeout}
            matches = [group for group in groups if set(group) == {"hooks"}
                       for item in group["hooks"] if item == handler]
            if len(matches) > 1 and not verified:
                raise ValueError("ambiguous hook ownership")
            if matches:
                group = matches[-1]
                group["hooks"].remove(handler)
                if not group["hooks"]:
                    groups.remove(group)
        groups.append({"hooks": [{"type": "command", "command": command, "timeout": timeout}]})


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
                for name in (".agentlocker-hook-lock", ".coldlocker-hook-lock"):
                    lock = parent / name
                    lock.mkdir(mode=0o700)
                    stack.callback(lock.rmdir)
        yield


def client_of(path):
    return "claude" if path.name == "settings.json" else "codex"


def hook_run(value):
    client, separator, spec = value.partition(":")
    events, equals, command = spec.partition("=")
    if (not separator or client not in ("claude", "codex") or not equals or not command.strip()
            or not set(events.split(",")) <= set(HOOK_EVENTS)):
        raise ValueError(f"expected CLIENT:EVENT[,EVENT]=COMMAND with EVENT in {', '.join(HOOK_EVENTS)}")
    return client, spec


def plan(paths, python, hook=HOOK, owned_commands=(), recall_command=None, hook_root=None, remove_commands=(),
         hook_runs=(), hook_briefing=None):
    changes = []
    for path, powershell in paths:
        old = read(path)
        if not path.parent.is_dir():
            print(f"skip {path}: client not installed")
            continue
        command = hook_command(python, powershell, hook)
        doc = json.loads(old.decode("utf-8")) if old and old.strip() else {}
        updated = merged(deepcopy(doc), command, owned_commands)
        contexts = []
        if recall_command is not None:
            contexts.append(("recall", recall_command, ("UserPromptSubmit", "PreToolUse"), 10))
        if hook_root is not None:
            runs = [arg for client, spec in hook_runs if client == client_of(path) for arg in ("--run", spec)]
            briefing = ("--briefing", hook_briefing.as_posix()) if hook_briefing is not None else ()
            context_command = hook_command(python, powershell, COLDLOCKER_HOOK, ("--root", hook_root.as_posix(), *briefing, *runs))
            contexts.append(("coldlocker", context_command, HOOK_EVENTS, 30))
        receipts = []
        for kind, context_command, events, timeout in contexts:
            receipt = path.with_name(path.name + f".{kind}-receipt.json")
            prior = read(receipt)
            recorded = prior
            if kind == "coldlocker" and recorded is None:
                recorded = read(path.with_name(path.name + ".fleet-receipt.json"))
            state = json.loads(recorded.decode("utf-8")) if recorded else {"owned_commands": []}
            owned = state.get("owned_commands")
            if not isinstance(owned, list) or not all(isinstance(c, str) for c in owned):
                raise ValueError(f"invalid {kind} receipt")
            digest = sha256(old or b"").hexdigest()
            verified = digest == state.get("settings_sha256")
            if "pending_command" in state:
                if not isinstance(state["pending_command"], str):
                    raise ValueError(f"invalid pending {kind} command")
                if digest == state.get("settings_sha256"):
                    owned = list(dict.fromkeys([*owned, state["pending_command"]]))
                elif digest != state.get("previous_settings_sha256"):
                    raise ValueError(f"settings changed during pending {kind} installation")
                verified = True
            installed = {h.get("command") for groups in doc.get("hooks", {}).values() for g in groups
                         for h in g.get("hooks", [])}
            if kind == "coldlocker":
                legacy_command = context_command.replace(COLDLOCKER_HOOK.as_posix(), COLDLOCKER_HOOK.with_name("fleet_hook.py").as_posix())
                if not recorded and legacy_command in installed:
                    owned = [legacy_command]
            stale = {c for c in installed & set(owned) if "--run" in c
                     and c.replace("fleet_hook.py", "coldlocker_hook.py") != context_command} - set(remove_commands)
            if kind == "coldlocker" and stale:
                raise ValueError(f"{path}: changing a chained coldlocker hook needs --remove-command for the old command")
            recalled(updated, context_command, owned, events, timeout, verified)
            receipts.append((receipt, prior, owned, context_command))
        if remove_commands:
            for event, groups in updated["hooks"].items():
                updated["hooks"][event] = without_ours(groups, remove_commands)
        for client, spec in hook_runs if hook_root is not None else ():
            events, _, chained = spec.partition("=")
            for event in events.split(","):
                if client == client_of(path) and event in updated["hooks"]:
                    updated["hooks"][event] = without_ours(updated["hooks"][event], (chained,))
        new = old if updated == doc else (json.dumps(updated, indent=2) + "\n").encode("utf-8")
        final_receipts = []
        for receipt, prior, owned, context_command in receipts:
            final_receipt = (json.dumps({"owned_commands": [context_command],
                                        "settings_sha256": sha256(new or b"").hexdigest()}, indent=2) + "\n").encode("utf-8")
            new_receipt = (json.dumps({"owned_commands": owned, "pending_command": context_command,
                                      "previous_settings_sha256": sha256(old or b"").hexdigest(),
                                      "settings_sha256": sha256(new or b"").hexdigest()}, indent=2) + "\n").encode("utf-8")
            if prior == final_receipt:
                new_receipt = prior
            if prior != new_receipt:
                changes.append((receipt, prior, new_receipt))
            if new_receipt != final_receipt:
                final_receipts.append((receipt, new_receipt, final_receipt))
        if new != old:
            changes.append((path, old, new))
        else:
            print(f"unchanged {path}")
        changes.extend(final_receipts)
    return changes


def apply(changes, backup=True):
    for path, old, new in changes:
        if read(path) != old:
            raise ValueError(f"changed during installation: {path}")
        if old is not None and backup:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            destination = path.with_name(f"{path.name}.bak-coldlocker-{stamp}")
            with open(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
                f.write(old)
            print(f"backup {destination}")
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
        atomic_write(path, new, mode)
        print(f"wrote {path}")


def install(paths, python, write, hook=HOOK, owned_commands=(), recall_command=None, hook_root=None, remove_commands=(),
            hook_runs=(), hook_briefing=None):
    require_consistent(HOOK.parent.parent)
    safe_path(hook)
    if list(violations(hook.read_text(encoding="utf-8"))):
        raise ValueError("supplied hook fails instruction consistency")
    if recall_command is not None and not recall_command.strip():
        raise ValueError("empty recall command")
    hook_runs = [hook_run(value) for value in hook_runs]
    if hook_runs and hook_root is None:
        raise ValueError("--hook-run needs --hook-root")
    if hook_root is not None:
        hook_root = Path(hook_root).expanduser().resolve()
    if hook_briefing is not None:
        hook_briefing = Path(hook_briefing).expanduser().resolve()
    paths = list(paths)
    if write:
        with locked(paths):
            apply(plan(paths, python, hook, owned_commands, recall_command, hook_root, remove_commands, hook_runs, hook_briefing))
    else:
        scope = "owned Stop hook only" if recall_command is None else "owned Stop and recall hooks only"
        if hook_root is not None or remove_commands:
            scope = "owned hooks and exact requested removals"
        for path, _, _ in plan(paths, python, hook, owned_commands, recall_command, hook_root, remove_commands, hook_runs, hook_briefing):
            print(f"would write {path}: {scope}; other settings preserved")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install Coldlocker hooks for Claude Code and Codex.")
    parser.add_argument("--home", type=Path, help="home directory; default is the current user's")
    parser.add_argument("--write", action="store_true", help="write changes; default prints a safe summary")
    parser.add_argument("--owned-command", action="append", default=[], help="exact prior owned command to replace when its interpreter or quoting changes")
    parser.add_argument("--recall-command", help="also install a context command on UserPromptSubmit and PreToolUse")
    parser.add_argument("--hook-root", type=Path, help="also install the Coldlocker hook using this private routing root")
    parser.add_argument("--hook-run", action="append", default=[], help="chain CLIENT:EVENT[,EVENT]=COMMAND through the Coldlocker hook; repeatable")
    parser.add_argument("--hook-briefing", type=Path, help="briefing path for the Coldlocker hook; requires --hook-root")
    parser.add_argument("--fleet-root", dest="hook_root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--fleet-run", dest="hook_run", action="append", help=argparse.SUPPRESS)
    parser.add_argument("--fleet-briefing", dest="hook_briefing", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--remove-command", action="append", default=[], help="remove this exact command from every event; repeatable")
    args = parser.parse_args(argv)
    if args.hook_briefing is not None and args.hook_root is None:
        parser.error("--hook-briefing requires --hook-root")
    try:
        install(targets(args.home), sys.executable, args.write, owned_commands=args.owned_command,
                recall_command=args.recall_command, hook_root=args.hook_root, remove_commands=args.remove_command,
                hook_runs=args.hook_run, hook_briefing=args.hook_briefing)
    except (ValueError, OSError, TypeError, AttributeError) as exc:
        parser.exit(1, f"install refused: {type(exc).__name__}\n")


if __name__ == "__main__":
    main()
