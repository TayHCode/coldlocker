import base64
import codecs
import json
import mimetypes
from pathlib import Path, PurePosixPath
import re
import sys
import time
from urllib.parse import quote, unquote

from portable import python_command
from store import ordinary_path


ROOT = Path(__file__).resolve().parent
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")
SECRET = re.compile(r"secret://[a-z0-9][a-z0-9._-]{0,62}\Z")
SENSITIVE = re.compile(r"token|secret|key|password|authorization|credential", re.I)
LOCAL = ".hub-store/catalog"
KINDS = {"skills": "skills", "rules": "rules", "mcp_servers": "mcp/servers", "routines": "routines", "plugins": "plugins", "connections": "connections"}
TOOLS = {"hub_list_" + kind: kind for kind in KINDS}
SEARCH_TOOL = "hub_search_skills"
SEARCH_QUERY_MAX = 500
SEARCH_LIMIT = 3
SEARCH_MIN_SCORE = 2
STOPWORDS = frozenset(
    "a an and are as at be been but by can could did do does for from get got had has have i if in into is it its me my "
    "of on or our please so than that the their then there these they this those to up us was we were what when where "
    "which who will with would you your".split())
CONCEPTS = {
    "robot": ("agent",), "bot": ("agent",), "ai": ("agent",), "assistant": ("agent",), "model": ("agent",),
    "remember": ("lesson", "encod"), "forget": ("lesson", "encod"), "mistak": ("lesson", "correction"),
    "error": ("failure",), "bug": ("failure",), "broken": ("failure",), "fail": ("failure",),
    "slow": ("performance",), "fast": ("performance",), "speed": ("performance",),
    "test": ("verif",), "check": ("verif",), "prove": ("verif", "evidence"), "proof": ("verif", "evidence"),
    "finish": ("done",), "complete": ("done",), "tri": ("prove", "verif"),
}
ITEM_KINDS = {"skill": "skills", "rule": "rules", "routine": "routines"}
MAX_ITEM_BYTES = 1024 * 1024
SNIFF_BYTES = 8192
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
URI_SCHEME = "agentlocker://"
PROMPT_HINT_LIMIT = 50
MIME_TYPES = {".md": "text/markdown", ".json": "application/json"}
TOOL_DESCRIPTIONS = {
    "skills": "List every skill in the agentlocker hub with name, description, owner, provenance and content hash. "
              "Use hub_search_skills first for a short list; call this to see every skill, then load one with hub_get_item "
              "(kind 'skill') only when its description matches the task. An entry with an 'error' field could not be fully read.",
    "rules": "List the hub's rule fragments (shared policy and project instructions) with name, owner, provenance and "
             "content hash. Read the applicable ones, including 'shared-policy', with hub_get_item (kind 'rule') before "
             "starting work.",
    "mcp_servers": "List nonsecret MCP server definitions indexed in the hub: transport, command and args or URL, and env "
                   "or header names with secret:// references. Secret values are never returned and listing never starts "
                   "a server. Use it to see which upstream servers exist and which credentials they need.",
    "routines": "List routine definitions (repeatable agent jobs) with id, description, owner and provenance. Routines are "
                "recorded definitions only: they are disabled and this endpoint never runs them. Read one with "
                "hub_get_item (kind 'routine').",
    "plugins": "List nonsecret plugin metadata records imported into the hub (an empty list when none were imported). "
               "Read-only: nothing is installed or executed.",
    "connections": "List nonsecret connection metadata records (provider and endpoint of a linked service) imported into "
                   "the hub, with secret:// references only. Read-only: nothing is contacted.",
}
RESOURCE_TEMPLATES = [
    {"uriTemplate": URI_SCHEME + "skills/{name}/{+path}", "name": "skill-file",
     "description": "A file inside a hub skill, such as SKILL.md or references/<file>."},
    {"uriTemplate": URI_SCHEME + "rules/{name}", "name": "rule", "description": "A hub rule fragment."},
    {"uriTemplate": URI_SCHEME + "routines/{id}/{file}", "name": "routine-file",
     "description": "A routine's routine.json or its prompt file."},
]
SEARCH_DESCRIPTION = (
    "Find skills by describing the task in a few words. Returns the best three matches (same fields as hub_list_skills), "
    "'searched' (skills considered) and 'remaining' (further matches not shown). An empty 'results' list means no skill "
    "matched well enough, not that the catalog is unavailable: fall back to hub_list_skills. Load a match with "
    "hub_get_item (kind 'skill') only when its description fits the task.")
GET_ITEM_DESCRIPTION = (
    "Load one skill, rule or routine by name after finding it with hub_search_skills or the matching hub_list_* tool. For a skill, 'files' maps "
    "every relative path (SKILL.md plus references/, scripts/ and assets/) to its UTF-8 text. Files that are not text, "
    "symlinks and anything past the 1 MiB text cap are named in 'omitted' with their size and reason, and 'truncated' is "
    "true when the cap cut content. Fetch individual ordinary files within 1 MiB with resources/read. A rule is one file; a routine is its "
    "routine.json plus its prompt file. The indexed sha256 is verified before anything is returned.")
LEGACY_REASON = "stored copy has a credential in its args"


class LegacyCredential(ValueError):
    pass


def legacy_next_step():
    return python_command() + " hub.py scan --import"


def reject_legacy_credential(server):
    if isinstance(server, dict) and isinstance(server.get("args"), list):
        try:
            from checks.validators import credential_args
        except ImportError:
            return server
        if credential_args(server["args"]):
            raise LegacyCredential(LEGACY_REASON)
    return server


def repo_file(root, relative):
    if not isinstance(relative, str) or "\\" in relative or ":" in relative:
        raise ValueError("invalid repository path")
    parts = PurePosixPath(relative).parts
    if not parts or relative.startswith("/") or any(p in (".", "..") for p in relative.split("/")):
        raise ValueError("invalid repository path")
    path = root.joinpath(*parts)
    ordinary_path(path, root)
    path.resolve().relative_to(root.resolve())
    return path


def read_manifest(root, relative):
    manifest = json.loads(repo_file(root, relative).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("invalid manifest")
    return manifest


def load_manifest(root):
    manifest = read_manifest(root, "manifest.json")
    try:
        local = read_manifest(root, LOCAL + "/manifest.json")
    except FileNotFoundError:
        return manifest
    for kind in KINDS:
        values = manifest.get(kind, [])
        extra = local.get(kind, [])
        if not isinstance(values, list) or not isinstance(extra, list) or not all(isinstance(e, dict) and isinstance(e.get("path"), str) for e in extra):
            raise ValueError("invalid manifest entries")
        manifest[kind] = values + [{**entry, "path": LOCAL + "/" + entry["path"]} for entry in extra]
    return manifest


def entries(root, kind):
    values = load_manifest(root).get(kind, [])
    if not isinstance(values, list):
        raise ValueError("invalid manifest entries")
    result = []
    seen = set()
    for entry in values:
        key = "id" if kind == "routines" else "name"
        name = entry[key]
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in seen:
            raise ValueError("invalid entry name")
        seen.add(name)
        suffix = ".json" if kind in ("mcp_servers", "plugins", "connections") else ".md" if kind == "rules" else ""
        if kind == "routines" and entry["path"].endswith("/routine.json"):
            suffix = "/routine.json"
        if entry["path"].removeprefix(LOCAL + "/") != KINDS[kind] + "/" + name + suffix:
            raise ValueError("invalid entry path")
        repo_file(root, entry["path"])
        provenance = entry["provenance"]
        if not re.fullmatch(r"[a-z0-9-]{1,63}", entry["owner"]):
            raise ValueError("invalid owner")
        if not re.fullmatch(r"src-[a-f0-9]{12}", provenance["source_id"]):
            raise ValueError("invalid provenance")
        if not re.fullmatch(r"[a-z0-9-]{1,63}", provenance["origin"]):
            raise ValueError("invalid provenance")
        if not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]):
            raise ValueError("invalid digest")
        result.append({key: name, "path": entry["path"], "owner": entry["owner"],
                       "provenance": {k: provenance[k] for k in ("source_id", "origin")},
                       "sha256": entry["sha256"]})
    return sorted(result, key=lambda e: e.get("name", e.get("id")))


def validate_server(server):
    if not isinstance(server, dict):
        raise ValueError("invalid server")
    reject_legacy_credential(server)
    for field in ("env", "headers"):
        mapping = server.get(field, {})
        if not isinstance(mapping, dict):
            raise ValueError("invalid server fields")
        for key, value in mapping.items():
            if not isinstance(value, str) or len(value) > 1024:
                raise ValueError("invalid server value")
            if value.startswith("secret://"):
                if not SECRET.fullmatch(value):
                    raise ValueError("invalid secret reference")
            elif SENSITIVE.search(key):
                raise ValueError("sensitive field requires a secret reference")
    return server


def read_server(root, entry):
    server = validate_server(json.loads(repo_file(root, entry["path"]).read_text(encoding="utf-8")))
    if server.get("name") != entry["name"]:
        raise ValueError("server name mismatch")
    return server


def description(root, entry, kind):
    relative = entry["path"]
    if kind == "rules":
        return None
    if kind == "skills":
        text = repo_file(root, relative + "/SKILL.md").read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            return None
        if "\n---" not in text[4:]:
            return None
        lines = text[4:].split("\n---", 1)[0].splitlines()
        for index, line in enumerate(lines):
            if not line.startswith("description:"):
                continue
            value = line.partition(":")[2].strip()
            if value in (">", ">-", "|", "|-"):
                block = []
                for following in lines[index + 1:]:
                    if following and not following.startswith((" ", "\t")):
                        break
                    block.append(following.strip())
                return ("\n" if value.startswith("|") else " ").join(block).strip()
            if value.startswith('"'):
                return json.loads(value)
            return value.strip("'")
        return None
    if kind == "routines" and not relative.endswith("/routine.json"):
        relative += "/routine.json"
    data = json.loads(repo_file(root, relative).read_text(encoding="utf-8"))
    if kind == "mcp_servers":
        validate_server(data)
    value = data.get("description")
    return value if isinstance(value, str) else None


class HubError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def catalog_entries(root, kind):
    try:
        return entries(root, kind)
    except OSError:
        raise HubError(-32603, "Hub manifest unreadable") from None
    except (ValueError, KeyError, TypeError, AttributeError):
        raise HubError(-32603, "Invalid manifest") from None


def inventory(root, kind):
    result = catalog_entries(root, kind)
    for entry in result:
        try:
            value = description(root, entry, kind)
            if value is not None:
                entry["description"] = value
            if kind in ("plugins", "connections"):
                from checks.validators import metadata
                data = json.loads(repo_file(root, entry["path"]).read_text(encoding="utf-8"))
                metadata(data, "plugin" if kind == "plugins" else "connection")
                entry["metadata"] = data
            if kind == "mcp_servers":
                server = read_server(root, entry)
                entry["server"] = {key: server[key] for key in
                                   ("transport", "command", "args", "url", "env", "headers") if key in server}
        except LegacyCredential as error:
            entry.update(reason=str(error), next_step=legacy_next_step())
        except UnicodeError:
            entry["error"] = "invalid text encoding"
        except json.JSONDecodeError:
            entry["error"] = "invalid JSON"
        except OSError:
            entry["error"] = "unreadable file"
    return result


def stem(word):
    for suffix in ("ing", "ly", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            word = word[:-len(suffix)]
            break
    return word[:-1] if len(word) > 3 and word.endswith("e") else word


def terms(text):
    return {stem(word) for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in STOPWORDS}


def search_skills(root, query):
    wanted = terms(query)
    for word in list(wanted):
        wanted.update(stem(related) for related in CONCEPTS.get(word, ()))
    catalog = inventory(root, "skills")
    scored = []
    for entry in catalog:
        score = (3 * len(wanted & terms(entry["name"].replace("-", " ")))
                 + len(wanted & terms(entry.get("description", ""))))
        if score >= SEARCH_MIN_SCORE:
            scored.append((score, entry))
    scored.sort(key=lambda row: (-row[0], row[1]["name"]))
    return {"results": [entry for _, entry in scored[:SEARCH_LIMIT]], "searched": len(catalog),
            "remaining": max(0, len(scored) - SEARCH_LIMIT)}


def locate(root, relative):
    try:
        return repo_file(root, relative)
    except ValueError:
        raise HubError(-32603, "Unsafe path: " + relative) from None


def unreadable(relative):
    return HubError(-32603, "Unreadable file: " + relative)


def walk_directory(root, base, sub, files, omitted):
    prefix = base + "/" + sub if sub else base
    try:
        children = sorted(locate(root, prefix).iterdir(), key=lambda child: child.name)
    except OSError:
        raise unreadable(prefix) from None
    for child in children:
        inner = sub + "/" + child.name if sub else child.name
        relative = base + "/" + inner
        if child.name == ".git":
            omitted.append({"path": relative, "reason": "excluded"})
        elif child.is_symlink():
            omitted.append({"path": relative, "reason": "symlink"})
        else:
            try:
                repo_file(root, relative)
                if child.is_dir():
                    walk_directory(root, base, inner, files, omitted)
                elif child.is_file():
                    files.append({"path": relative, "sub": inner, "size": child.stat().st_size})
                else:
                    omitted.append({"path": relative, "reason": "special-file"})
            except ValueError:
                omitted.append({"path": relative, "reason": "unsafe-path"})
            except OSError:
                raise unreadable(relative) from None


def routine_files(root, relative, name):
    definition = relative if relative.endswith("/routine.json") else relative + "/routine.json"
    try:
        data = json.loads(locate(root, definition).read_text(encoding="utf-8"))
    except OSError:
        raise unreadable(definition) from None
    except ValueError:
        raise HubError(-32603, "Invalid routine definition: " + name) from None
    paths = [definition]
    prompt = data.get("prompt") if isinstance(data, dict) else None
    if prompt is not None:
        if not isinstance(prompt, str) or not prompt or "/" in prompt:
            raise HubError(-32603, "Invalid routine prompt: " + name)
        paths.append(definition.rpartition("/")[0] + "/" + prompt)
    return paths


def item_listing(root, kind, name, verify=True):
    from checks.validators import digest
    found = {e.get("name", e.get("id")): e for e in catalog_entries(root, ITEM_KINDS[kind])}
    if name not in found:
        raise HubError(-32602, f"Unknown {kind}: {name}")
    entry = found[name]
    relative = entry["path"]
    if verify:
        path = locate(root, relative)
        try:
            if not path.exists():
                raise unreadable(relative)
            matches = digest(path) == entry["sha256"]
        except OSError:
            raise unreadable(relative) from None
        if not matches:
            raise HubError(-32603, f"Content hash mismatch for {kind}: {name}")
    files, omitted = [], []
    if kind == "skill":
        walk_directory(root, relative, "", files, omitted)
        main = relative + "/SKILL.md"
        if not any(f["path"] == main for f in files):
            raise unreadable(main)
        files.sort(key=lambda f: (f["path"] != main, f["path"]))
    else:
        paths = [relative] if kind == "rule" else routine_files(root, relative, name)
        for path in paths:
            try:
                size = locate(root, path).stat().st_size
            except OSError:
                raise unreadable(path) from None
            files.append({"path": path, "sub": None if kind == "rule" else path.rpartition("/")[2], "size": size})
    return {"entry": entry, "files": files, "omitted": omitted}


def looks_like_text(data):
    try:
        decoded = codecs.getincrementaldecoder("utf-8")().decode(data, final=False)
    except UnicodeDecodeError:
        return False
    return "\0" not in decoded


def read_file(root, relative, remaining):
    path = locate(root, relative)
    try:
        with path.open("rb") as stream:
            data = stream.read(remaining + 1)
    except OSError:
        raise unreadable(relative) from None
    if len(data) > remaining:
        return ("size-cap" if looks_like_text(data[:SNIFF_BYTES]) else "non-text"), None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "non-text", data
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return ("text", text) if "\0" not in text else ("non-text", data)


def item_contents(root, kind, name):
    listing = item_listing(root, kind, name)
    contents, omitted, remaining = {}, list(listing["omitted"]), MAX_ITEM_BYTES
    for file in listing["files"]:
        status, data = read_file(root, file["path"], remaining)
        if status == "text":
            contents[file["path"]] = data
            remaining -= len(data.encode("utf-8"))
        else:
            omitted.append({"path": file["path"], "size": file["size"], "reason": status})
    return {"kind": kind, "name": name, "files": contents,
            "omitted": sorted(omitted, key=lambda item: item["path"]),
            "truncated": any(item["reason"] == "size-cap" for item in omitted), "max_bytes": MAX_ITEM_BYTES}


def resource_uri(kind, name, sub):
    base = URI_SCHEME + ITEM_KINDS[kind] + "/" + name
    return base + "/" + quote(sub, safe="/") if sub else base


def mime_type(sub):
    suffix = PurePosixPath(sub or "").suffix.lower()
    return MIME_TYPES.get(suffix) or mimetypes.guess_type("x" + suffix)[0]


def list_resources(root):
    resources = []
    for kind, plural in ITEM_KINDS.items():
        for entry in catalog_entries(root, plural):
            name = entry.get("name", entry.get("id"))
            try:
                listing = item_listing(root, kind, name, verify=False)
            except HubError:
                continue
            for file in listing["files"]:
                resource = {"uri": resource_uri(kind, name, file["sub"]),
                            "name": name + "/" + file["sub"] if file["sub"] else name, "size": file["size"]}
                mime = mime_type(file["sub"] or file["path"])
                if mime:
                    resource["mimeType"] = mime
                resources.append(resource)
    return resources


def parse_resource_uri(uri):
    invalid = HubError(-32602, "Invalid resource URI")
    if not isinstance(uri, str) or not uri.startswith(URI_SCHEME):
        raise invalid
    parts = uri[len(URI_SCHEME):].split("/")
    kind = next((k for k, plural in ITEM_KINDS.items() if plural == parts[0]), None)
    if kind is None or len(parts) < 2 or not NAME.fullmatch(parts[1]):
        raise invalid
    segments = [unquote(part) for part in parts[2:]]
    if any("/" in segment for segment in segments):
        raise invalid
    if kind == "rule" and segments or kind == "routine" and len(segments) != 1 or kind == "skill" and not segments:
        raise invalid
    return kind, parts[1], "/".join(segments) or None


def read_resource(root, uri):
    kind, name, sub = parse_resource_uri(uri)
    file = next((f for f in item_listing(root, kind, name)["files"] if f["sub"] == sub), None)
    if file is None:
        raise HubError(-32602, "Unknown resource")
    status, data = read_file(root, file["path"], MAX_ITEM_BYTES)
    mime = mime_type(sub or file["path"])
    if status == "text":
        return {"uri": uri, "mimeType": mime or "text/plain", "text": data}
    if data is None:
        raise HubError(-32602, "Resource exceeds size limit")
    return {"uri": uri, "mimeType": mime or "application/octet-stream", "blob": base64.b64encode(data).decode("ascii")}


def list_prompts(root):
    prompts = []
    for entry in inventory(root, "skills"):
        prompt = {"name": entry["name"]}
        if "description" in entry:
            prompt["description"] = entry["description"]
        prompt["arguments"] = []
        prompts.append(prompt)
    return prompts


def get_prompt(root, params):
    name = params.get("name")
    if set(params) - {"name", "arguments"} or not isinstance(name, str) or not NAME.fullmatch(name) \
            or params.get("arguments", {}) != {}:
        raise HubError(-32602, "Invalid prompt name or arguments")
    try:
        listing = item_listing(root, "skill", name)
    except HubError as failure:
        if failure.code == -32602:
            raise HubError(-32602, "Unknown prompt: " + name) from None
        raise
    main = listing["files"][0]
    status, text = read_file(root, main["path"], MAX_ITEM_BYTES)
    if status != "text":
        raise HubError(-32603, "File is not readable text within the size limit: " + main["path"])
    others = [resource_uri("skill", name, f["sub"]) for f in listing["files"][1:]]
    if others:
        text += "\n\n---\nSupporting files for this skill are available as MCP resources (resources/read):\n"
        text += "".join("- " + uri + "\n" for uri in others[:PROMPT_HINT_LIMIT])
        if len(others) > PROMPT_HINT_LIMIT:
            text += f"- and {len(others) - PROMPT_HINT_LIMIT} more (see resources/list)\n"
    result = {"messages": [{"role": "user", "content": {"type": "text", "text": text}}]}
    try:
        value = description(root, listing["entry"], "skills")
    except (OSError, ValueError):
        value = None
    if value is not None:
        result = {"description": value, **result}
    return result


def negotiate_version(params):
    requested = params.get("protocolVersion")
    return requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]


def tool_definitions():
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    tools = [{"name": name, "description": TOOL_DESCRIPTIONS[kind], "inputSchema": schema,
              "annotations": {"readOnlyHint": True}} for name, kind in TOOLS.items()]
    tools.append({"name": SEARCH_TOOL, "description": SEARCH_DESCRIPTION, "inputSchema": {
        "type": "object", "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": SEARCH_QUERY_MAX,
                      "description": "The task in a few plain words, such as 'monitor pull request CI'."}},
        "required": ["query"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True}})
    tools.append({"name": "hub_get_item", "description": GET_ITEM_DESCRIPTION, "inputSchema": {
        "type": "object", "properties": {
            "kind": {"type": "string", "enum": list(ITEM_KINDS),
                     "description": "What to load: 'skill', 'rule' or 'routine'."},
            "name": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,62}$",
                     "description": "Entry name exactly as returned by the matching hub_list_* tool (a routine's id)."}},
        "required": ["kind", "name"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True}})
    return tools


def error(code, message):
    return {"error": {"code": code, "message": message}}


def dispatch(request, root=ROOT):
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
        return {"jsonrpc": "2.0", "id": None, **error(-32600, "Invalid Request")}
    method = request["method"]
    if "id" not in request:
        return None
    identity = request["id"]
    if isinstance(identity, (dict, list, bool)):
        return {"jsonrpc": "2.0", "id": None, **error(-32600, "Invalid Request")}
    params = request.get("params", {})
    response = {"jsonrpc": "2.0", "id": identity}
    try:
        if not isinstance(params, dict):
            response.update(error(-32602, "Invalid params"))
        elif method == "initialize":
            response["result"] = {"protocolVersion": negotiate_version(params),
                                  "capabilities": {"tools": {}, "prompts": {}, "resources": {}},
                                  "serverInfo": {"name": "agentlocker", "version": "1.0.0"}}
        elif method == "ping":
            response["result"] = {}
        elif method == "tools/list":
            response["result"] = {"tools": tool_definitions()}
        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {})
            if name == "hub_get_item":
                valid = (isinstance(arguments, dict) and set(arguments) == {"kind", "name"}
                         and arguments["kind"] in ITEM_KINDS and isinstance(arguments["name"], str)
                         and NAME.fullmatch(arguments["name"]))
            elif name == SEARCH_TOOL:
                valid = (isinstance(arguments, dict) and set(arguments) == {"query"}
                         and isinstance(arguments["query"], str) and 0 < len(arguments["query"]) <= SEARCH_QUERY_MAX)
            else:
                valid = isinstance(name, str) and name in TOOLS and arguments == {}
            if not valid:
                response.update(error(-32602, "Invalid tool or arguments"))
            else:
                if name == "hub_get_item":
                    values = item_contents(root, arguments["kind"], arguments["name"])
                elif name == SEARCH_TOOL:
                    values = search_skills(root, arguments["query"])
                else:
                    values = inventory(root, TOOLS[name])
                response["result"] = {"content": [{"type": "text", "text": json.dumps(values)}], "isError": False}
        elif method == "prompts/list":
            response["result"] = {"prompts": list_prompts(root)}
        elif method == "prompts/get":
            response["result"] = get_prompt(root, params)
        elif method == "resources/list":
            response["result"] = {"resources": list_resources(root)}
        elif method == "resources/read":
            if set(params) != {"uri"}:
                raise HubError(-32602, "Invalid params")
            response["result"] = {"contents": [read_resource(root, params["uri"])]}
        elif method == "resources/templates/list":
            response["result"] = {"resourceTemplates": RESOURCE_TEMPLATES}
        else:
            response.update(error(-32601, "Method not found"))
    except HubError as failure:
        response.update(error(failure.code, failure.message))
    except OSError:
        response.update(error(-32603, "Unreadable file in hub catalog"))
    except (json.JSONDecodeError, UnicodeError):
        response.update(error(-32603, "Malformed file in hub catalog"))
    except ValueError:
        response.update(error(-32603, "Invalid hub content"))
    except (KeyError, TypeError, AttributeError):
        response.update(error(-32603, "Hub catalog unavailable"))
    return response


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    client = 'unknown'
    for line in sys.stdin:
        started = time.monotonic()
        request = None
        try:
            request = json.loads(line)
            if isinstance(request, dict) and request.get('method') == 'initialize':
                params = request.get('params', {})
                info = params.get('clientInfo', {}) if isinstance(params, dict) else {}
                client = info.get('name', 'unknown') if isinstance(info, dict) else 'unknown'
            response = dispatch(request)
        except (ValueError, UnicodeError):
            response = {"jsonrpc": "2.0", "id": None, **error(-32700, "Parse error")}
        if isinstance(request, dict):
            params = request.get('params', {})
            tool = params.get('name') if isinstance(params, dict) and request.get('method') == 'tools/call' else None
            try:
                import calllog
                calllog.append(ROOT, client, request.get('method'), tool, (time.monotonic() - started) * 1000)
            except (ImportError, OSError, ValueError, TypeError):
                pass
        if response is not None:
            print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
