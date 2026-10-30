import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

from mcp_endpoint import ROOT, SECRET, LegacyCredential, entries, read_server, repo_file
from store import FileStore, QuietParser, ordinary_path


PATH_BINDINGS = ".hub-store/source-path-bindings.json"
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")


def placeholder(ref):
    return "${AGENTLOCKER_SECRET_" + ref[9:].encode().hex().upper() + "}"


def render_value(value):
    if SECRET.fullmatch(value):
        return placeholder(value)
    if "${" in value or "%{env:" in value:
        raise ValueError("literal interpolation is forbidden")
    return value


def render_argument(value, root):
    prefix = "${AGENTLOCKER_ROOT}/"
    if value.startswith(prefix):
        relative = render_value(value[len(prefix):])
        return str(repo_file(root, relative).resolve())
    flag, equals, tail = value.partition("=")
    if equals and flag.startswith("-") and SECRET.fullmatch(tail):
        return flag + equals + placeholder(tail)
    return render_value(value)


def render_server(server, root=ROOT):
    transport = server.get("transport")
    if transport == "stdio":
        if not isinstance(server.get("command"), str) or not server["command"]:
            raise ValueError("invalid command")
        command = sys.executable if server["command"] == "python3" and sys.platform.startswith("win") else server["command"]
        args = server.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ValueError("invalid arguments")
        result = {"type": "stdio", "command": render_value(command),
                  "args": [render_argument(arg, root) for arg in args]}
    elif transport == "http":
        url = server.get("url")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise ValueError("invalid URL")
        if "@" in url or "?" in url or "#" in url:
            raise ValueError("URL credentials and queries are forbidden")
        result = {"type": "streamable-http", "url": render_value(url)}
    else:
        raise ValueError("unsupported transport")
    for field in ("env", "headers"):
        if field in server:
            result[field] = {key: render_value(value) for key, value in server[field].items()}
    return result


def _binding_path(root):
    return ordinary_path(Path(root) / PATH_BINDINGS)


def _read_bindings(root):
    path = _binding_path(root)
    if not path.exists():
        return {"version": 1, "servers": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("version") != 1
            or set(value) != {"version", "servers"} or not isinstance(value["servers"], dict)):
        raise ValueError("invalid source path bindings")
    for name, binding in value["servers"].items():
        if not NAME.fullmatch(name) or not isinstance(binding, dict) or set(binding) - {"command", "args"}:
            raise ValueError("invalid source path bindings")
        if "command" in binding:
            _validate_binding(binding["command"])
        if "args" in binding:
            if not isinstance(binding["args"], dict):
                raise ValueError("invalid source path bindings")
            for index, item in binding["args"].items():
                if not index.isdecimal():
                    raise ValueError("invalid source path bindings")
                _validate_binding(item)
    return value


def _validate_binding(value):
    if (not isinstance(value, dict) or set(value) != {"portable", "reference"}
            or not isinstance(value["portable"], str) or not SECRET.fullmatch(value["reference"])):
        raise ValueError("invalid source path bindings")


def _absolute_path(value):
    candidate = value.partition("=")[2] if value.startswith("-") and "=" in value else value
    return Path(candidate).is_absolute() or bool(re.match(r"^[A-Za-z]:[\\/]", candidate))


def _references(bindings):
    result = set()
    for binding in bindings.get("servers", {}).values():
        values = [binding["command"]] if "command" in binding else []
        values.extend(binding.get("args", {}).values())
        result.update(value["reference"][9:] for value in values)
    return result


def store_source_path_bindings(root, server, bindings):
    """Keep original home paths in the private store, separate from MCP definitions."""
    if not isinstance(server, str) or not NAME.fullmatch(server) or not isinstance(bindings, dict):
        raise ValueError("invalid source path binding")
    store = FileStore(root)
    path = _binding_path(root)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    current = _read_bindings(root)
    before = _references(current)
    updated = {"version": 1, "servers": dict(current["servers"])}
    updated["servers"].pop(server, None)
    created = set()
    value = {}

    def add(item):
        if item is None:
            return
        if (not isinstance(item, dict) or set(item) != {"portable", "resolved"}
                or not isinstance(item["portable"], str) or not isinstance(item["resolved"], str)
                or not _absolute_path(item["resolved"]) or "\x00" in item["resolved"]):
            raise ValueError("invalid source path binding")
        reference = "path-" + uuid.uuid4().hex
        store.put(reference, item["resolved"])
        created.add(reference)
        return {"portable": item["portable"], "reference": "secret://" + reference}

    try:
        command = add(bindings.get("command"))
        if command:
            value["command"] = command
        args = bindings.get("args", {})
        if not isinstance(args, dict):
            raise ValueError("invalid source path binding")
        bound_args = {}
        for index, item in args.items():
            if not isinstance(index, int) or index < 0:
                raise ValueError("invalid source path binding")
            if item is not None:
                bound_args[str(index)] = add(item)
        if bound_args:
            value["args"] = bound_args
        if value:
            updated["servers"][server] = value
        if updated["servers"]:
            write_output(path, json.dumps(updated, indent=2) + "\n")
            path.chmod(0o600)
        else:
            path.unlink(missing_ok=True)
        retained = _references(updated)
        for reference in before - retained:
            store.delete(reference)
    except (OSError, ValueError, TypeError, KeyError):
        for reference in created:
            store.delete(reference)
        raise


def source_path_bindings(root, server=None):
    value = _read_bindings(root)["servers"]
    if server is not None:
        return value.get(server, {})
    return value


def render(root=ROOT):
    servers = {}
    for entry in entries(root, "mcp_servers"):
        try:
            servers[entry["name"]] = render_server(read_server(root, entry), root)
        except LegacyCredential:
            continue
    return {"mcpServers": servers}


def output_path(root, output):
    target = ordinary_path(output)
    try:
        relative = target.relative_to(root.resolve())
    except ValueError:
        return target
    if relative.parts and relative.parts[0] in (".hub-store", ".scratch"):
        return target
    if ".git" in relative.parts:
        raise ValueError("protected output path")
    checked = subprocess.run(["git", "ls-files", "-z"], cwd=root,
                             capture_output=True, check=True)
    tracked = [Path(p) for p in checked.stdout.decode().split("\0") if p]
    if any(relative == path or relative in path.parents or path in relative.parents for path in tracked):
        raise ValueError("tracked output path")
    return target


def write_output(path, text):
    descriptor, temporary = tempfile.mkstemp(prefix=".render-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = QuietParser(description="Render nonsecret MCPHub settings")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        text = json.dumps(render(), indent=2) + "\n"
        if args.out:
            write_output(output_path(ROOT, args.out), text)
        else:
            print(text, end="")
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        print("gateway render failed: invalid configuration or output path", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
