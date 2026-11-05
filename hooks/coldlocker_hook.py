import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chat_mode import active, NOTICE, toggle
from publication_guard import publication_guard
from review_guard import dispatch_guard

CHAIN_BUDGET = 20


def block(text, name):
    start, end = f"<!-- {name}:begin -->", f"<!-- {name}:end -->"
    if start not in text or end not in text.split(start, 1)[1]:
        return ""
    return text.split(start, 1)[1].split(end, 1)[0].strip()


def handle(payload, root, briefing=None):
    event = payload.get("hook_event_name")
    briefing = briefing or root / "BRIEFING.md"
    home = briefing.parent if (briefing.parent / "models.yaml").is_file() else root
    if event == "PreToolUse":
        return publication_guard(payload) or dispatch_guard(payload, root, home / "models.yaml")
    if event not in ("SessionStart", "SubagentStart", "UserPromptSubmit"):
        return {}
    try:
        text = briefing.read_text(encoding="utf-8")
        context = block(text, "kiss")
        if event != "UserPromptSubmit":
            context = block(text, "session") or context
    except FileNotFoundError:
        context = f"briefing missing at {briefing}"
    if event == "UserPromptSubmit":
        context += ("\n\nState the goal and done condition before acting. End tool work with "
                    "Goal:/Done condition:/Evidence:/Remaining:/Status: DONE or BLOCKED. "
                    "DONE requires Remaining: none.")
    else:
        context += "\n\nFiles: " + ", ".join(str(path) for path in
                                            (briefing, home / "models.yaml", home / "skills/README.md", home / "lessons"))
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}


def kill_tree(child):
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(child.pid)], capture_output=True)
    else:
        os.killpg(child.pid, signal.SIGKILL)
    child.communicate()


def output_of(command, payload, timeout):
    try:
        child = subprocess.Popen(command, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                 start_new_session=os.name != "nt")
    except OSError:
        return {}
    try:
        stdout, _ = child.communicate(json.dumps(payload), timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(child)
        return None
    if child.returncode or not stdout.strip():
        return {}
    try:
        result = json.loads(stdout)
    except ValueError:
        result = None
    return result if isinstance(result, dict) else {"hookSpecificOutput": {"additionalContext": stdout.strip()}}


def context_of(result):
    return (result.get("hookSpecificOutput") or {}).get("additionalContext") or ""


def denies(result):
    return (result.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"


def with_context(event, text):
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}} if text else {}


def denied(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def dispatch(payload, root, runs, briefing=None):
    event = payload.get("hook_event_name")
    switched = toggle(payload)
    if switched == NOTICE or switched is None and active(payload):
        return with_context(event, switched or "")
    deadline = time.monotonic() + CHAIN_BUDGET
    results = [handle(payload, root, briefing)]
    if denies(results[0]):
        return results[0]
    for events, command in runs:
        remaining = deadline - time.monotonic()
        if event in events and remaining <= 0 and event == "PreToolUse":
            return denied("Chained hook time budget exhausted; tool call denied.")
        if event in events and remaining > 0:
            result = output_of(command, payload, remaining)
            if result is None and event == "PreToolUse":
                return denied("Chained hook timed out; tool call denied.")
            results.append(result or {})
    for result in results:
        if denies(result):
            return result
    texts = [switched] if switched else []
    return with_context(event, "\n\n".join(texts + [context_of(r) for r in results if context_of(r)]))


def run_spec(value):
    events, separator, command = value.partition("=")
    if not separator or not events or not command.strip():
        raise argparse.ArgumentTypeError("expected EVENT[,EVENT]=COMMAND")
    return frozenset(events.split(",")), command


def main(failure_reason="Coldlocker guard failed; tool call denied."):
    payload = None
    try:
        payload = json.load(sys.stdin)
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--root", required=True, type=Path)
        parser.add_argument("--run", action="append", default=[], type=run_spec)
        parser.add_argument("--briefing", type=Path)
        args = parser.parse_args()
        result = dispatch(payload, args.root, args.run, args.briefing)
    except (Exception, SystemExit):
        result = {}
        if isinstance(payload, dict) and payload.get('hook_event_name') == 'PreToolUse':
            result = denied(failure_reason)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
