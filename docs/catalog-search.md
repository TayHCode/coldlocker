# Catalog search

`hub_search_skills` returns the best three skill entries for a plain-language request, so an agent can load one skill without reading the full `hub_list_skills` output first. It is additive: `hub_list_skills`, `hub_get_item` and standing rule access are unchanged.

## Behavior

- Arguments: `{"query": "<1-500 characters>"}` and nothing else. Anything else is `-32602`.
- Result: `{"results": [...], "searched": N, "remaining": M}`. Entries use the `hub_list_skills` format (name, path, owner, provenance, sha256, description), so `hub_get_item` still verifies the indexed hash before returning files.
- An empty `results` list means no skill matched. It is not an error, and an agent should then call `hub_list_skills`.
- Ranking is lexical and local: lowercase `[a-z0-9]+` words, stopwords removed, light stemming, a small generic concept map (for example robot to agent, remember to lesson), score of 3 per name-word match plus 1 per description-word match, minimum score 2, top 3 sorted by score then name. No embeddings, vectors or services.
- `mcp_endpoint.py` and the Worker in `remote/src/mcp.ts` use the same ranker. `tests/remote_parity.py` fixtures keep them identical.

## Evaluation

Run `tools/catalog_search_eval.py` (needs `tiktoken`, used only by the eval). It compares the first prototype (exact word overlap), the shipped ranker, and the full list on identical cases.

- Dev set: the 8 requests from the Eve analysis plus one absent request. The shipped concept map was tuned on these only; the prototype's one miss ("stop saying finished until you actually tried it") is fixed by a dev-tuned `tri` concept.
- History set: 38 requests typed by the owner in earlier agent sessions, frozen before the ranker was run on them. 26 expect a skill in the catalog, 12 expect none. Labels come from one rater. The private file is not published; the fixture holds only a sha256 fingerprint per case and short excerpts for 6 generic cases. The eval checks fingerprints when given the private file.
- Tokenizer: tiktoken 0.14.0 `o200k_base`, an OpenAI encoding used as a proxy, not Claude's tokenizer. Five identical runs confirmed stability; they are not independent samples.
- Cost counts the query, the search response, and the full list when a fallback is needed. Policy A falls back only on an empty result. Policy B (oracle) also falls back whenever the expected skill is not in the top 3 and always for absent requests.
- Every expected skill was also loaded with `hub_get_item` to confirm a full, untruncated package.

| Set | Ranker | Strict hit | Silent miss | Tokens: list only | Policy A | Policy B |
|---|---|---|---|---|---|---|
| Dev (8 present, 1 absent) | prototype | 7/8 | 1 | 75,285 | 11,819 | 20,184 |
| Dev | shipped | 8/8 | 0 | 75,285 | 11,152 | 11,152 |
| History (26 present, 12 absent) | prototype | 8/26 | 18 | 317,870 | 20,127 | 271,077 |
| History | shipped | 16/26 | 10 | 317,870 | 41,142 | 200,077 |
| History public excerpts only (5 present, 1 absent) | shipped | 4/5 | 1 | 50,190 | 10,519 | 18,884 |

On the history set the shipped ranker also answers empty for 3 of 12 absent requests (9 false positives; the prototype had 12 of 12).

## Decision

Keep, as an optional tool.

- Discovery tokens fall under both fallback policies against the full list (policy A 87% lower, oracle policy B 37% lower on the history set), and the shipped ranker finds twice as many expected skills as the prototype on held-out requests (16/26 against 8/26).
- Required discoverability stays: the list tool is untouched and both tool descriptions tell agents to use it when no result fits.
- Risk, not fixed: 10 of 26 present requests got a confident wrong top 3 (silent miss), and long conversational or absent requests often return something. Under policy A those requests never reach the list. The cost of a wrong skill is not measured here. Recall is also not task completion.
- The "make the robot remember its mistakes" miss is fixed by general concepts, not by hardcoding that query.

Limits: one rater, weak labels for conversational prose, the `why` skill is about a third of the expected labels, and `how` and `why` are skill names so they are not stopwords.
