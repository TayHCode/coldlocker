import hashlib
import json
import re
from pathlib import PurePosixPath

from checks.scan import PATTERNS, findings


NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}\Z")
SECRET = re.compile(r"secret://[a-z0-9][a-z0-9._-]{0,62}\Z")
CREDENTIAL_FLAG = re.compile(r"--(?:api[-_]?key|access[-_]?token|auth[-_]?token|bearer[-_]?token|token|client[-_]?secret|secret|password|passwd|authorization|auth)", re.I)
SECRET_SHAPES = [pattern for rule, pattern in PATTERNS.items() if rule != "home-path"]
COLLECTIONS = {"skills": "name", "rules": "name", "mcp_servers": "name", "routines": "id", "plugins": "name", "connections": "name"}
LOCAL = ".hub-store/catalog"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def name(value):
    require(isinstance(value, str) and NAME.fullmatch(value), "invalid name or id")
    return value


def label(value):
    require(isinstance(value, str) and LABEL.fullmatch(value), "invalid owner or origin label")
    return value


def relative_path(value):
    require(isinstance(value, str) and value and "\\" not in value and ":" not in value,
            "path must be relative POSIX")
    path = PurePosixPath(value)
    require(not path.is_absolute() and all(part not in {"", ".", ".."} for part in value.split("/")),
            "path must be relative POSIX without traversal")
    return path


def digest(path):
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    require(path.is_dir(), "content path is missing")
    entries = []
    for item in path.rglob("*"):
        relative = item.relative_to(path)
        if ".git" in relative.parts or item.is_symlink() or not item.is_file():
            continue
        entries.append((relative.as_posix(), item))
    data = "".join(f"{relative}\0{digest(item)}\n" for relative, item in sorted(entries))
    return hashlib.sha256(data.encode()).hexdigest()


def manifest(value):
    require(isinstance(value, dict), "manifest must be an object")
    require({"schema_version", "name", "license", "skills", "rules", "mcp_servers", "routines"} <= set(value)
            <= {"schema_version", "name", "license", *COLLECTIONS}, "invalid manifest fields")
    require(type(value["schema_version"]) is int and value["schema_version"] == 1,
            "schema_version must be 1")
    require(value["name"] == "agentlocker" and value["license"] == "MIT", "invalid hub name or license")
    for collection, key in COLLECTIONS.items():
        entries = value.get(collection, [])
        require(isinstance(entries, list), "manifest collections must be lists")
        names = []
        for entry in entries:
            require(isinstance(entry, dict) and set(entry) == {key, "path", "owner", "provenance", "sha256"},
                    "invalid manifest entry fields")
            names.append(name(entry[key]))
            relative_path(entry["path"])
            expected = {"skills": f"skills/{entry[key]}", "rules": f"rules/{entry[key]}.md",
                        "mcp_servers": f"mcp/servers/{entry[key]}.json",
                        "routines": f"routines/{entry[key]}/routine.json",
                        "plugins": f"plugins/{entry[key]}.json", "connections": f"connections/{entry[key]}.json"}[collection]
            require(entry["path"].removeprefix(LOCAL + "/") == expected, "entry path does not match layout")
            label(entry["owner"])
            provenance = entry["provenance"]
            require(isinstance(provenance, dict) and set(provenance) == {"source_id", "origin"},
                    "invalid provenance")
            require(isinstance(provenance["source_id"], str) and
                    re.fullmatch(r"src-[0-9a-f]{12}", provenance["source_id"]), "invalid source_id")
            label(provenance["origin"])
            require(isinstance(entry["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]),
                    "invalid sha256")
        require(names == sorted(set(names)), "manifest lists must be sorted and unique")
    return value


def empty_manifest():
    return {"schema_version": 1, "name": "agentlocker", "license": "MIT",
            "skills": [], "rules": [], "mcp_servers": [], "routines": []}


def read_local(root, load=lambda path: json.loads(path.read_text(encoding="utf-8"))):
    path = root / LOCAL / "manifest.json"
    require(not any(item.is_symlink() for item in (path, path.parent, path.parent.parent)),
            "local catalog must not be symlinked")
    return manifest(load(path)) if path.is_file() else empty_manifest()


def layered(root, shipped, load=lambda path: json.loads(path.read_text(encoding="utf-8"))):
    local = read_local(root, load)
    merged = dict(shipped)
    for collection, key in COLLECTIONS.items():
        extra = [{**entry, "path": f"{LOCAL}/{entry['path']}"} for entry in local.get(collection, [])]
        combined = shipped.get(collection, []) + extra
        names = [entry[key] for entry in combined]
        require(len(names) == len(set(names)), "local catalog repeats a shipped name")
        if combined:
            merged[collection] = sorted(combined, key=lambda entry: entry[key])
    return merged


def skill(path, expected):
    require(path.is_dir() and not path.is_symlink(), "skill must be an ordinary directory")
    require(not any(item.is_symlink() for item in path.rglob("*")), "skill contains symlinks")
    lines = (path / "SKILL.md").read_text(encoding="utf-8").splitlines()
    require(lines and lines[0].strip() == "---", "SKILL.md requires frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise ValueError("SKILL.md frontmatter is not closed") from None
    fields = {}
    for line in lines[1:end]:
        if not line.strip() or line[:1].isspace() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        require(key not in fields, "duplicate frontmatter field")
        fields[key] = value.strip().strip("\"'")
    require(fields.get("name") == expected, "frontmatter name must match package name")
    require(bool(fields.get("description")), "frontmatter requires description")


def _safe_fragment(value, structured_identifier=False):
    if isinstance(value, dict):
        for key, item in value.items():
            _safe_fragment(key)
            _safe_fragment(item)
    elif isinstance(value, list):
        for item in value:
            _safe_fragment(item)
    elif isinstance(value, str):
        require(not findings(value, structured_identifier=structured_identifier),
                "definition contains a secret or private path/name")


def safe_fragment(value, identifier_fields=()):
    """Check free text, optionally treating named root fields as identifiers."""
    if isinstance(value, dict) and identifier_fields:
        for key, item in value.items():
            _safe_fragment(key)
            _safe_fragment(item, isinstance(key, str) and key in identifier_fields)
    else:
        _safe_fragment(value)


def safe_identifier(value):
    require(isinstance(value, str), "invalid identifier")
    _safe_fragment(value, structured_identifier=True)


def literal_credential(value):
    return (bool(value) and not SECRET.fullmatch(value) and not value.startswith(("$", "-"))
            and not re.fullmatch(r"<[^<>]+>", value))


def credential_args(args):
    found = {}
    for index, arg in enumerate(args):
        if not isinstance(arg, str):
            continue
        prefix, tail = "", arg
        if arg.startswith("-") and "=" in arg:
            flag, _, tail = arg.partition("=")
            prefix = flag + "="
        flagged = bool(prefix) and bool(CREDENTIAL_FLAG.fullmatch(flag))
        if literal_credential(tail) and (flagged or any(pattern.search(tail) for pattern in SECRET_SHAPES)):
            found[index] = (prefix, tail)
        elif CREDENTIAL_FLAG.fullmatch(arg) and index + 1 < len(args):
            following = args[index + 1]
            if isinstance(following, str) and literal_credential(following):
                found.setdefault(index + 1, ("", following))
    return found


def mcp(value):
    require(isinstance(value, dict), "MCP definition must be an object")
    name(value.get("name"))
    require(isinstance(value.get("description"), str) and bool(value["description"].strip()), "description required")
    transport = value.get("transport")
    require(transport in {"stdio", "http"}, "invalid MCP transport")
    allowed = {"name", "description", "transport", "env"}
    if transport == "stdio":
        allowed |= {"command", "args"}
        require(isinstance(value.get("command"), str) and bool(value["command"].strip()), "command required")
        require(isinstance(value.get("args", []), list) and
                all(isinstance(arg, str) for arg in value.get("args", [])), "args must be strings")
        require(not credential_args(value.get("args", [])), "credential in command-line args")
    else:
        allowed |= {"url", "headers"}
        require(isinstance(value.get("url"), str) and re.match(r"https?://[^\s]+\Z", value["url"]), "HTTP URL required")
    require(set(value) <= allowed, "unknown MCP fields")
    for field in ("env", "headers"):
        mapping = value.get(field, {})
        require(isinstance(mapping, dict), "env and headers must be objects")
        for key, literal in mapping.items():
            require(isinstance(literal, str), "env/header values must be strings")
            if literal and re.search(r"token|secret|key|password|authorization|credential", key, re.I):
                require(SECRET.fullmatch(literal), "sensitive keys require secret references")
            if literal.startswith("secret:"):
                require(SECRET.fullmatch(literal), "invalid secret reference")
            else:
                require(len(literal) <= 256 and "\n" not in literal, "literal must be short")
    safe_fragment(value, identifier_fields={"name"})
    return value


def routine(value):
    require(isinstance(value, dict), "routine must be an object")
    required = {"id", "description", "owner", "runner", "schedule", "timezone", "prompt",
                "overlap", "timeout_seconds", "enabled"}
    require(set(value) == required, "invalid routine fields")
    name(value["id"])
    label(value["owner"])
    require(value["runner"] in ("t3-scheduler", "systemd-timer", "cron", "launchd", "n8n", "grok-bot", "claude-code", "codex", "dots", "muse", "mausbot", "schtasks"), "invalid runner")
    require(value["enabled"] is False, "portable routine definitions must be disabled; enable per runner in the local store")
    require(value["overlap"] in ("skip", "queue", "allow"), "invalid overlap")
    require(type(value["timeout_seconds"]) is int and value["timeout_seconds"] > 0, "invalid timeout")
    for field in ("description", "timezone", "prompt"):
        require(isinstance(value[field], str) and bool(value[field].strip()), f"{field} required")
    schedule = value["schedule"]
    require(isinstance(schedule, dict), "schedule must be an object")
    kind = schedule.get("type")
    if kind == "interval":
        require(set(schedule) == {"type", "every_seconds"} and type(schedule["every_seconds"]) is int
                and schedule["every_seconds"] > 0, "invalid interval")
    elif kind == "fixed_time":
        require({"type", "time_of_day"} <= set(schedule) <= {"type", "time_of_day", "weekdays"}, "invalid fixed_time")
        require(isinstance(schedule["time_of_day"], str) and
                re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", schedule["time_of_day"]), "invalid time")
        days = schedule.get("weekdays", [])
        require(isinstance(days, list) and all(type(day) is int and 1 <= day <= 7 for day in days)
                and len(days) == len(set(days)), "invalid weekdays")
    elif kind == "cron":
        require(set(schedule) == {"type", "expression"} and isinstance(schedule["expression"], str)
                and bool(schedule["expression"].strip()), "invalid cron")
    else:
        require(kind == "webhook" and set(schedule) == {"type"}, "invalid schedule")
    if value["description"] == value["id"]:
        safe_fragment({key: item for key, item in value.items() if key != "description"},
                      identifier_fields={"id"})
    else:
        safe_fragment(value, identifier_fields={"id"})
    return value


def metadata(value, kind):
    require(isinstance(value, dict), "metadata object required")
    name(value.get("name"))
    if kind == "plugin":
        require(set(value) == {"name", "agent", "source", "version", "enabled", "install_hint"}, "invalid plugin fields")
        require(type(value["enabled"]) is bool, "invalid enabled flag")
        fields = ("agent", "source", "version", "install_hint")
    else:
        require(kind == "connection" and set(value) == {"name", "provider", "endpoint", "auth_status"}, "invalid connection fields")
        require(value["auth_status"] in ("unknown", "configured", "needs-auth", "connected"), "invalid auth status")
        fields = ("provider", "endpoint")
        require(isinstance(value["endpoint"], str) and value["endpoint"].startswith(("http://", "https://")), "HTTP endpoint required")
    for field in fields:
        require(isinstance(value[field], str) and bool(value[field]) and len(value[field]) <= 2048, "invalid metadata string")
    from urllib.parse import urlsplit
    for field in ("source", "endpoint"):
        text = value.get(field, "")
        if "://" in text:
            require(not any(c.isspace() or ord(c) < 32 for c in text), "unsafe metadata URL")
            parsed = urlsplit(text)
            require(parsed.scheme in ("http", "https") and bool(parsed.hostname) and not parsed.username
                    and not parsed.password and not parsed.query and not parsed.fragment, "unsafe metadata URL")
    safe_fragment(value, identifier_fields={"name"})
    return value
