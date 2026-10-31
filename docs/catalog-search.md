# Eve catalog search experiment

## Decision: do not adopt search-first discovery

The candidate fixes the original request, "make the robot remember its mistakes", and hits all eight development cases. On frozen history it retrieves an expected skill for only 16 of 26 requests. The other ten return plausible but wrong shortlists. Nine of twelve absent-capability requests also return matches.

That does not establish a reliable improvement over the full list. The production MCP endpoint and Worker keep their existing discovery behavior. The candidate remains an offline experiment in `tools/catalog_search.py`, not an advertised or optional production tool. This implements the request to keep search only if proven better.

Source: [Coldstack PR 57](https://github.com/TayHCode/coldstack/pull/57), report revision `0ab092ebe1e1b9d7d8fd75bbbe8893e8560de5a8`. The original untracked prototype is preserved separately.

## Method and results

The original eight requests plus one absent request are the development set. Before tuning, 38 owner-authored requests were frozen from earlier sessions, with source IDs and vault authorship checks retained privately. A single rater labeled 26 present and 12 absent capabilities. Labels are imperfect; about a third of present cases expect `why`. Short public excerpts are available for six cases. The remaining public fixture records fingerprints, not private transcripts. Requests longer than 500 characters are truncated after fingerprint verification; results apply to those prefixes.

The candidate removes stopwords, stems words, expands a small concept map, and returns the top three entries with score at least two. Name matches score three, description matches one. It uses existing catalog entries and unchanged hash-verified `hub_get_item`. The concept map was tuned only on development cases. Codex subsequently found unreachable concept keys; normalizing those keys fixed the bug, with a focused regression test. The history score below includes that correction. No further history tuning was performed.

Tokenizer: tiktoken 0.14.0, `o200k_base`. These are actual counts under that encoding, not Claude billing tokens. Five identical deterministic passes check stability, not statistical independence. Full JSON results, catalog and fixture fingerprints, and metadata-only per-request traces are in [the evidence file](evidence/eve-catalog-eval.json).

| Set | Ranker | Top-three hit | Present misses | Absent false positives |
| --- | --- | --- | --- | --- |
| Development | Original prototype | 7/8 | 1 | 0/1 |
| Development | Candidate | 8/8 | 0 | 0/1 |
| History | Original prototype | 8/26 | 18 | 12/12 |
| History | Candidate | 16/26 | 10 | 9/12 |

| History token accounting | Full list | Original prototype | Candidate |
| --- | ---: | ---: | ---: |
| Discovery, fallback only when empty | 317,870 | 20,127 | 41,234 |
| Discovery, oracle fallback on wrong results | 317,870 | 271,077 | 200,169 |
| Oracle discovery plus one expected package per present case | 498,500 | 451,707 | 380,799 |

Discovery includes serialized query arguments, response data and a full-list response when falling back. It excludes transport envelopes, system instructions, tool schemas and model reasoning. The oracle recognizes every wrong result from the labels; a real caller cannot do that automatically. Its apparent savings are conditional, not evidence of a working fallback policy. Empty-only fallback saves tokens by missing requested skills, so it cannot justify adoption.

Package counts add the first alphabetically sorted acceptable skill per present case to every strategy. These 26 loads verify full, untruncated packages and account for 180,630 tokens. They are labeled oracle loads, not successful model selections. Actual failed-attempt cost, latency, cache reuse, task completion and policy compliance remain unmeasured. Per-request traces contain only case IDs, token counts and fallback flags. Model output and cached tokens remain null because no model ran.

## Reproduce locally

```sh
python3 -m venv .scratch/venv
.scratch/venv/bin/python -m pip install tiktoken==0.14.0
.scratch/venv/bin/python tools/catalog_search_eval.py
python3 -B tools/verify_export.py
```

The default eval runs the development set and six public history excerpts, not the complete history set. An owner with the preserved frozen file can pass `--history-private FILE`; the script verifies each request fingerprint before running. No remote model or paid service is called.

## Small runtime proofs

`tools/runtime_proofs.py` and `tests/test_runtime_proofs.py` exercise these ideas using Python fixtures, not the Eve engine:

- Fresh processes resume after a recorded step and after an effect before its receipt. Stable operation keys yield one effect per step. Unkeyed replay deliberately duplicates the effect, demonstrating why it is unsafe.
- Checkpoint replacement is atomic for this single-process fixture. It does not prove concurrent writers, power-loss durability, authorization changes or version upgrades.
- The same output assertions reject a planted median bug with 2/3 passes and accept the fix with 3/3 passes. Calling the expected function alone cannot pass.
- Synthetic per-turn input, cached and output counters reconcile to 3,800, 2,300 and 440. These are fixture values, not measured model usage. The catalog eval separately records measured retrieval token counts.

Seven runtime tests pass, including fresh-process recovery at both boundaries. These proofs do not warrant adopting a new runtime or production checkpoint store.

## Verification scope

The public export intentionally omits private maintainer configuration. Its documented health, safety and MCP endpoint checks plus experiment tests run through `python3 -B tools/verify_export.py`, or `python -B tools/verify_export.py` on Windows. The repository-only `.fleet/verify.ps1` adapter invokes that same command and is excluded from source-only archives. The complete private maintainer suite is not claimed green. No services, credentials, runners or paid Vercel products are changed.
