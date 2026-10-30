import json
import os
import re
import sys

STATUS = re.compile(r"Status: (DONE|PARTIAL|BLOCKED)")
FIELDS = ("Goal", "Done condition", "Evidence", "Remaining")
REASON = (
    "The done condition is unverified. Continue unfinished work, then end with Goal:, "
    "Done condition:, Evidence:, Remaining:, and Status: DONE or BLOCKED. DONE requires "
    "Remaining: none and evidence you observed. BLOCKED names the exact blocker. "
    "PARTIAL is never a final status. If the user explicitly stopped the work, do no more "
    "work and report BLOCKED."
)


def turn_used_tools(transcript_path):
    with open(transcript_path, encoding="utf-8", errors="replace") as lines:
        return any_tool_since_user(lines)


def any_tool_since_user(lines):
    used = False
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        payload = entry.get("payload") or {}
        if entry.get("type") == "event_msg" and payload.get("type") == "user_message":
            used = False
        if entry.get("type") == "response_item" and payload.get("type") in ("function_call", "custom_tool_call", "local_shell_call"):
            used = True
        message = entry.get("message") or (payload if payload.get("type") == "message" else {})
        content = message.get("content")
        typed_user_text = isinstance(content, str) or isinstance(content, list) and any(
            block.get("type") in ("text", "input_text", "image") for block in content)
        if (entry.get("type") == "user" or message.get("role") == "user") and typed_user_text:
            used = False
        elif entry.get("type") == "assistant" and isinstance(content, list):
            used = used or any(block.get("type") == "tool_use" for block in content)
    return used


def stop_decision(payload):
    last = (payload.get("last_assistant_message") or "").strip()
    final = STATUS.fullmatch(last.splitlines()[-1].strip()) if last else None
    status = final and final[1]
    fields = {name: re.findall(r"^" + name + r": (.+)$", last, re.MULTILINE) for name in FIELDS}
    if status and all(fields.values()):
        if status == "BLOCKED" or status == "DONE" and fields["Remaining"][-1].strip().lower().rstrip(".") == "none":
            return {}
    path = payload.get("transcript_path")
    if not (path and os.path.exists(path) and turn_used_tools(path)):
        return {}
    return {"decision": "block", "reason": REASON}


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    print(json.dumps(stop_decision(payload) if isinstance(payload, dict) else {}))


if __name__ == "__main__":
    main()
