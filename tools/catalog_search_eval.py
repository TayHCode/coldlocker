import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mcp_endpoint
from tools import catalog_search

FIXTURE = ROOT / "tests/fixtures/search_eval.json"
REPEATS = 5


def call(root, name, arguments):
    response = mcp_endpoint.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": name, "arguments": arguments}}, root)
    assert "error" not in response, response
    assert response["result"]["isError"] is False, response
    return response["result"]["content"][0]["text"]


def prototype(catalog, query):
    words = set(re.findall(r"[a-z0-9]+", query.lower()))
    scored = []
    for entry in catalog:
        names = set(re.findall(r"[a-z0-9]+", entry["name"].lower()))
        description = set(re.findall(r"[a-z0-9]+", entry.get("description", "").lower()))
        score = 3 * len(words & names) + len(words & description)
        if score:
            scored.append((score, entry))
    ranked = sorted(scored, key=lambda row: (-row[0], row[1]["name"]))
    return {"results": [entry for _, entry in ranked[:3]], "searched": len(catalog),
            "remaining": max(0, len(ranked) - 3)}


def tokenizer():
    try:
        import tiktoken
    except ImportError:
        sys.exit("tiktoken is required: python3 -m venv .scratch/venv && .scratch/venv/bin/pip install tiktoken")
    encoding = tiktoken.get_encoding("o200k_base")
    return (lambda text: len(encoding.encode(text))), {"name": "tiktoken o200k_base", "version": tiktoken.__version__,
                                                        "note": "OpenAI encoding used as a proxy, not Claude's tokenizer"}


def history_queries(cases, private):
    if private is None:
        return [(case["id"], case["excerpt"], case) for case in cases if "excerpt" in case], "public-excerpts"
    rows = json.loads(Path(private).read_text(encoding="utf-8"))
    fingerprints = {row["fingerprint"]: row["text"] for row in rows}
    queries = []
    for case in cases:
        text = fingerprints[case["fingerprint"]]
        assert hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode()).hexdigest() == case["fingerprint"]
        queries.append((case["id"], text[: catalog_search.SEARCH_QUERY_MAX], case))
    return queries, "private-full"


def measure(root, queries, count, ranker):
    catalog = json.loads(call(root, "hub_list_skills", {}))
    list_tokens = count(call(root, "hub_list_skills", {}))
    names = {entry["name"] for entry in catalog}
    totals = {"list_only": 0, "search_fallback_on_empty": 0, "search_oracle_fallback": 0}
    stats = {"present": 0, "strict_hit": 0, "silent_miss": 0, "empty_miss": 0, "absent": 0, "correct_empty": 0,
             "false_positive": 0, "package_loads_verified": 0, "package_tokens": 0}
    trace = []
    for case_id, query, case in queries:
        text = json.dumps(catalog_search.search_skills(catalog, query) if ranker == "candidate" else prototype(catalog, query))
        result = json.loads(text)
        found = [entry["name"] for entry in result["results"]]
        query_tokens = count(json.dumps({"query": query}))
        response_tokens = count(text)
        cost = query_tokens + response_tokens
        trace.append({"case_id": case_id, "query_tokens": query_tokens, "response_tokens": response_tokens,
                      "fallback_on_empty": not found, "cached_tokens": None, "model_output_tokens": None})
        totals["list_only"] += list_tokens
        totals["search_fallback_on_empty"] += cost + (list_tokens if not found else 0)
        if case["kind"] == "present":
            stats["present"] += 1
            hit = bool(set(found) & set(case["expected_any"]))
            stats["strict_hit"] += hit
            stats["silent_miss"] += bool(found) and not hit
            stats["empty_miss"] += not found
            totals["search_oracle_fallback"] += cost + (0 if hit else list_tokens)
            for name in sorted(set(case["expected_any"]) & names)[:1]:
                if name in names:
                    package = json.loads(call(root, "hub_get_item", {"kind": "skill", "name": name}))
                    assert package["files"] and not package["truncated"] and not package["omitted"]
                    stats["package_loads_verified"] += 1
                    stats["package_tokens"] += count(json.dumps(package))
        else:
            stats["absent"] += 1
            stats["correct_empty"] += not found
            stats["false_positive"] += bool(found)
            totals["search_oracle_fallback"] += cost + list_tokens
    return {**stats, "trace": trace, "tokens": totals, "tokens_with_one_expected_package": {key: value + stats["package_tokens"] for key, value in totals.items()}}


def run(root, count, private):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sets = {"dev": ([(f"d{i}", case["query"], case) for i, case in enumerate(fixture["dev"]["cases"])], "public")}
    sets["history"] = history_queries(fixture["history"]["cases"], private)
    report = {}
    for name, (queries, source) in sets.items():
        report[name] = {"source": source, "cases": len(queries)}
        for ranker in ("prototype", "candidate"):
            report[name][ranker] = measure(root, queries, count, ranker)
    return report


def main():
    parser = argparse.ArgumentParser(description="Compare list-only, prototype and experimental catalog search on frozen cases.")
    parser.add_argument("--history-private", help="private frozen history file; omit to use the public excerpts only")
    parser.add_argument("--out", help="write the JSON report here")
    args = parser.parse_args()
    count, tokenizer_info = tokenizer()
    reports = [run(ROOT, count, args.history_private) for _ in range(REPEATS)]
    assert all(report == reports[0] for report in reports), "search results changed between repeats"
    output = {"tokenizer": tokenizer_info, "repeats": REPEATS,
              "oracle_policy": "Fall back on present misses and all absent cases, including empty results, to confirm absence with the full list.",
              "repeat_note": "identical deterministic repeats measure stability, not independent samples",
              "catalog_sha256": hashlib.sha256(call(ROOT, "hub_list_skills", {}).encode()).hexdigest(),
              "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
              "catalog_skills": len(json.loads(call(ROOT, "hub_list_skills", {}))), **reports[0]}
    text = json.dumps(output, indent=1, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
