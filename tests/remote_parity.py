import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mcp_endpoint
import remote_publisher
from tests.test_remote_publisher import build_hub, seal, write

GOLDEN = ROOT / "remote/test/golden/parity.json"
BIG_THRESHOLD = 4096


def repeated(unit, count):
    return {"$repeat": unit, "count": count}


def expand(spec):
    return spec["$repeat"] * spec["count"]


def extend_main(base):
    skill = base / "skills/demo-skill"
    write(skill / "references/guide.md", "# Guide\r\nMore detail.\rEnd\n")
    write(skill / "references/with space.md", "spaced\n")
    write(skill / "references/référence-naïve.md", "Unicode ☃ text\n")
    write(skill / "scripts/notes.txt", "plain notes\n")
    write(skill / "assets/NOTICE", "no extension\n")
    write(base / "skills/many-files/SKILL.md", "---\nname: many-files\ndescription: Has many supporting files\n---\n# Many\n")
    for number in range(53):
        write(base / f"skills/many-files/references/file-{number:02d}.md", f"file {number}\n")
    write(base / "rules/second-rule.md", "# Second\nMore rules.\n")
    seal(base)


def extend_edge(base):
    skill = base / "skills/edge-skill"
    write(skill / "SKILL.md", "---\nname: edge-skill\ndescription: Binary and oversized content\n---\n# Edge\n")
    (skill / "assets").mkdir(parents=True, exist_ok=True)
    (skill / "assets/blob.bin").write_bytes(b"\x00\xff\xfe binary \x01")
    for letter in "abc":
        write(skill / f"big/{letter}.txt", f"{letter}-line of padding text\n" * 19600)
    write(skill / "huge.txt", "h-line of padding text\n" * 70000)
    seal(base)


MAIN_REQUESTS = [
    ("initialize latest", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}),
    ("initialize middle", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}),
    ("initialize oldest", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}),
    ("initialize unknown", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}),
    ("initialize bare", {"jsonrpc": "2.0", "id": "a", "method": "initialize"}),
    ("ping", {"jsonrpc": "2.0", "id": 2, "method": "ping"}),
    ("tools list", {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}),
    ("list skills", {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "hub_list_skills", "arguments": {}}}),
    ("list skills without arguments", {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "hub_list_skills"}}),
    ("list rules", {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "hub_list_rules", "arguments": {}}}),
    ("list mcp servers", {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "hub_list_mcp_servers", "arguments": {}}}),
    ("list routines", {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "hub_list_routines", "arguments": {}}}),
    ("list plugins", {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "hub_list_plugins", "arguments": {}}}),
    ("list connections", {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "hub_list_connections", "arguments": {}}}),
    ("list with arguments", {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "hub_list_skills", "arguments": {"x": 1}}}),
    ("unknown tool", {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "hub_nope", "arguments": {}}}),
    ("tool name not a string", {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": ["hub_list_skills"], "arguments": {}}}),
    ("tool without name", {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {}}),
    ("get skill", {"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {"name": "hub_get_item", "arguments": {"kind": "skill", "name": "demo-skill"}}}),
    ("get many-files skill", {"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {"name": "hub_get_item", "arguments": {"kind": "skill", "name": "many-files"}}}),
    ("get rule", {"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": {"name": "hub_get_item", "arguments": {"kind": "rule", "name": "shared-policy"}}}),
    ("get routine", {"jsonrpc": "2.0", "id": 14, "method": "tools/call", "params": {"name": "hub_get_item", "arguments": {"kind": "routine", "name": "demo-routine"}}}),
    ("get unknown skill", {"jsonrpc": "2.0", "id": 15, "method": "tools/call", "params": {"name": "hub_get_item", "arguments": {"kind": "skill", "name": "nothing-here"}}}),
    ("get unknown rule", {"jsonrpc": "2.0", "id": 15, "method": "tools/call", "params": {"name": "hub_get_item", "arguments": {"kind": "rule", "name": "nothing-here"}}}),
    ("get with extra key", {"jsonrpc": "2.0", "id": 16, "method": "tools/call", "params": {"name": "hub_get_item", "arguments": {"kind": "skill", "name": "demo-skill", "x": 1}}}),
    ("get with missing name", {"jsonrpc": "2.0", "id": 16, "method": "tools/call", "params": {"name": "hub_get_item", "arguments": {"kind": "skill"}}}),
    ("get with bad name", {"jsonrpc": "2.0", "id": 17, "method": "tools/call", "params": {"name": "hub_get_item", "arguments": {"kind": "skill", "name": "../etc"}}}),
    ("get with uppercase name", {"jsonrpc": "2.0", "id": 17, "method": "tools/call", "params": {"name": "hub_get_item", "arguments": {"kind": "skill", "name": "Demo-Skill"}}}),
    ("get with non-string name", {"jsonrpc": "2.0", "id": 17, "method": "tools/call", "params": {"name": "hub_get_item", "arguments": {"kind": "skill", "name": 5}}}),
    ("get with bad kind", {"jsonrpc": "2.0", "id": 18, "method": "tools/call", "params": {"name": "hub_get_item", "arguments": {"kind": "plugin", "name": "demo-skill"}}}),
    ("get with plural kind", {"jsonrpc": "2.0", "id": 18, "method": "tools/call", "params": {"name": "hub_get_item", "arguments": {"kind": "skills", "name": "demo-skill"}}}),
    ("get with object kind", {"jsonrpc": "2.0", "id": 19, "method": "tools/call", "params": {"name": "hub_get_item", "arguments": {"kind": {"a": 1}, "name": "demo-skill"}}}),
    ("get with list kind", {"jsonrpc": "2.0", "id": 19, "method": "tools/call", "params": {"name": "hub_get_item", "arguments": {"kind": ["skill"], "name": "demo-skill"}}}),
    ("get with list arguments", {"jsonrpc": "2.0", "id": 20, "method": "tools/call", "params": {"name": "hub_get_item", "arguments": ["skill", "demo-skill"]}}),
    ("get without arguments", {"jsonrpc": "2.0", "id": 20, "method": "tools/call", "params": {"name": "hub_get_item"}}),
    ("prompts list", {"jsonrpc": "2.0", "id": 21, "method": "prompts/list"}),
    ("prompt with supporting files", {"jsonrpc": "2.0", "id": 22, "method": "prompts/get", "params": {"name": "demo-skill"}}),
    ("prompt with empty arguments", {"jsonrpc": "2.0", "id": 22, "method": "prompts/get", "params": {"name": "demo-skill", "arguments": {}}}),
    ("prompt over the hint limit", {"jsonrpc": "2.0", "id": 23, "method": "prompts/get", "params": {"name": "many-files"}}),
    ("prompt unknown", {"jsonrpc": "2.0", "id": 24, "method": "prompts/get", "params": {"name": "nothing-here"}}),
    ("prompt with arguments", {"jsonrpc": "2.0", "id": 24, "method": "prompts/get", "params": {"name": "demo-skill", "arguments": {"a": 1}}}),
    ("prompt with extra key", {"jsonrpc": "2.0", "id": 24, "method": "prompts/get", "params": {"name": "demo-skill", "x": 1}}),
    ("prompt with bad name", {"jsonrpc": "2.0", "id": 24, "method": "prompts/get", "params": {"name": "Bad Name"}}),
    ("prompt without name", {"jsonrpc": "2.0", "id": 24, "method": "prompts/get", "params": {}}),
    ("resources list", {"jsonrpc": "2.0", "id": 25, "method": "resources/list"}),
    ("resource templates", {"jsonrpc": "2.0", "id": 26, "method": "resources/templates/list"}),
    ("read skill main", {"jsonrpc": "2.0", "id": 27, "method": "resources/read", "params": {"uri": "agentlocker://skills/demo-skill/SKILL.md"}}),
    ("read skill reference with CRLF", {"jsonrpc": "2.0", "id": 27, "method": "resources/read", "params": {"uri": "agentlocker://skills/demo-skill/references/guide.md"}}),
    ("read skill file with space", {"jsonrpc": "2.0", "id": 27, "method": "resources/read", "params": {"uri": "agentlocker://skills/demo-skill/references/with%20space.md"}}),
    ("read skill file with unicode", {"jsonrpc": "2.0", "id": 27, "method": "resources/read", "params": {"uri": "agentlocker://skills/demo-skill/references/r%C3%A9f%C3%A9rence-na%C3%AFve.md"}}),
    ("read skill text file", {"jsonrpc": "2.0", "id": 27, "method": "resources/read", "params": {"uri": "agentlocker://skills/demo-skill/scripts/notes.txt"}}),
    ("read skill file without extension", {"jsonrpc": "2.0", "id": 27, "method": "resources/read", "params": {"uri": "agentlocker://skills/demo-skill/assets/NOTICE"}}),
    ("read rule", {"jsonrpc": "2.0", "id": 28, "method": "resources/read", "params": {"uri": "agentlocker://rules/shared-policy"}}),
    ("read routine definition", {"jsonrpc": "2.0", "id": 29, "method": "resources/read", "params": {"uri": "agentlocker://routines/demo-routine/routine.json"}}),
    ("read routine prompt", {"jsonrpc": "2.0", "id": 29, "method": "resources/read", "params": {"uri": "agentlocker://routines/demo-routine/prompt.md"}}),
    ("read unknown file", {"jsonrpc": "2.0", "id": 30, "method": "resources/read", "params": {"uri": "agentlocker://skills/demo-skill/missing.md"}}),
    ("read unknown item", {"jsonrpc": "2.0", "id": 30, "method": "resources/read", "params": {"uri": "agentlocker://skills/nothing-here/SKILL.md"}}),
    ("read rule with path", {"jsonrpc": "2.0", "id": 31, "method": "resources/read", "params": {"uri": "agentlocker://rules/shared-policy/extra"}}),
    ("read skill without path", {"jsonrpc": "2.0", "id": 31, "method": "resources/read", "params": {"uri": "agentlocker://skills/demo-skill"}}),
    ("read routine without file", {"jsonrpc": "2.0", "id": 31, "method": "resources/read", "params": {"uri": "agentlocker://routines/demo-routine"}}),
    ("read encoded slash", {"jsonrpc": "2.0", "id": 31, "method": "resources/read", "params": {"uri": "agentlocker://skills/demo-skill/references%2Fguide.md"}}),
    ("read other scheme", {"jsonrpc": "2.0", "id": 31, "method": "resources/read", "params": {"uri": "file:///etc/passwd"}}),
    ("read unknown collection", {"jsonrpc": "2.0", "id": 31, "method": "resources/read", "params": {"uri": "agentlocker://plugins/x/y"}}),
    ("read non-string uri", {"jsonrpc": "2.0", "id": 31, "method": "resources/read", "params": {"uri": 5}}),
    ("read with extra key", {"jsonrpc": "2.0", "id": 32, "method": "resources/read", "params": {"uri": "agentlocker://rules/shared-policy", "x": 1}}),
    ("read without uri", {"jsonrpc": "2.0", "id": 32, "method": "resources/read", "params": {}}),
    ("unknown method", {"jsonrpc": "2.0", "id": 33, "method": "nope/nothing"}),
    ("params not an object", {"jsonrpc": "2.0", "id": 34, "method": "ping", "params": [1]}),
    ("null id", {"jsonrpc": "2.0", "id": None, "method": "ping"}),
    ("string id", {"jsonrpc": "2.0", "id": "abc", "method": "ping"}),
    ("boolean id", {"jsonrpc": "2.0", "id": True, "method": "ping"}),
    ("object id", {"jsonrpc": "2.0", "id": {"a": 1}, "method": "ping"}),
    ("notification", {"jsonrpc": "2.0", "method": "notifications/initialized"}),
    ("wrong version", {"jsonrpc": "1.0", "id": 1, "method": "ping"}),
    ("method not a string", {"jsonrpc": "2.0", "id": 1, "method": 5}),
    ("not an object", [1, 2]),
]

EDGE_REQUESTS = [
    ("list skills", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "hub_list_skills", "arguments": {}}}),
    ("get skill with omitted and capped files", {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "hub_get_item", "arguments": {"kind": "skill", "name": "edge-skill"}}}),
    ("read within the cap", {"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "agentlocker://skills/edge-skill/big/a.txt"}}),
    ("read skill main", {"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": "agentlocker://skills/edge-skill/SKILL.md"}}),
]

BIG_TEXTS = {}


def register_big(text):
    if len(text) >= BIG_THRESHOLD:
        unit = text[: text.index("\n") + 1]
        spec = repeated(unit, len(text) // len(unit))
        assert expand(spec) == text
        BIG_TEXTS[text] = spec


def compact(value):
    if isinstance(value, str):
        return BIG_TEXTS.get(value, value)
    if isinstance(value, list):
        return [compact(item) for item in value]
    if isinstance(value, dict):
        return {key: compact(item) for key, item in value.items()}
    return value


def unwrap_tool_text(response):
    if isinstance(response, dict):
        content = response.get("result", {}).get("content") if isinstance(response.get("result"), dict) else None
        if isinstance(content, list) and content and content[0].get("type") == "text" and "isError" in response["result"]:
            response = json.loads(json.dumps(response))
            response["result"]["content"][0]["text"] = json.loads(content[0]["text"])
    return response


def run_cases(root, requests):
    cases = []
    for name, request in requests:
        response = mcp_endpoint.dispatch(json.loads(json.dumps(request)), root=root)
        cases.append({"name": name, "request": request, "expected": compact(unwrap_tool_text(response))})
    return cases


def fixture(name, prepare, requests):
    with tempfile.TemporaryDirectory(dir=ROOT / ".scratch") as temp:
        root = Path(temp)
        build_hub(root)
        prepare(root)
        artifact = remote_publisher.build(root)
        for blob in artifact.blobs.values():
            register_big(blob)
        return {"name": name, "revision": artifact.revision, "index": artifact.index,
                "blobs": {digest: compact(text) for digest, text in artifact.blobs.items()},
                "cases": run_cases(root, requests)}


def generate():
    (ROOT / ".scratch").mkdir(exist_ok=True)
    return {"fixtures": [fixture("main", extend_main, MAIN_REQUESTS), fixture("edge", extend_edge, EDGE_REQUESTS)]}


def render():
    return json.dumps(generate(), indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main():
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(render(), encoding="utf-8")
    print(f"wrote {GOLDEN.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
