import re
from pathlib import Path

ON, OFF = "chat:", "work:"
SESSION = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
NOTICE = (
    "Chat mode is on for this session. Coldlocker hooks, guards and reminders are off. "
    "Ignore the workflow and reporting rules injected earlier: the briefing, the Goal/Status "
    "footer, vault-search, P3, Ponytail and ADHD formatting. Just talk. Still don't publish, "
    "push, delete or change live systems unless the user asks in this conversation. "
    f"The user can start a message with `{OFF}` to turn the hooks back on."
)
RESUMED = "Chat mode is off. Coldlocker hooks, guards and the injected workflow rules apply again."


def marker(payload):
    session = payload.get("session_id")
    if not isinstance(session, str) or not SESSION.match(session):
        return None
    return Path.home() / ".coldlocker" / "chat" / session


def active(payload):
    path = marker(payload)
    return bool(path and path.is_file())


def toggle(payload):
    if payload.get("hook_event_name") != "UserPromptSubmit":
        return None
    prompt = payload.get("prompt")
    word = prompt.lstrip()[:5].lower() if isinstance(prompt, str) else ""
    path = marker(payload)
    if path is None or word not in (ON, OFF):
        return None
    if word == ON:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.touch()
        return NOTICE
    path.unlink(missing_ok=True)
    return RESUMED
