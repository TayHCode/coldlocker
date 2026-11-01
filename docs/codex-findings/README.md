# Codex review findings as Coldfix lessons

Codex P1/P2 inline findings on merged TayHCode/coldlocker PRs that a later head fixed. `cases.json` lists each
finding; `tools/codex_findings.py` fetches the comment, replays a check against the reviewed head and the fixed
head, and prints Coldfix ingest events.

```
python3 tools/codex_findings.py > .scratch/events.jsonl
python3 coldfix.py --store <store> ingest --input .scratch/events.jsonl
```

Re-ingesting skips events with the same `source_ref`, so the run is repeatable.

| PR | Finding | Reviewed head | Fixed head | Replay |
|----|---------|---------------|------------|--------|
| 3 | P2 email scanner flags the apostrophe-quoted AGENTS.md reference in `tests/test_model_policy.py` | `b697063e` | `2a26412e` | `tests/replays/scan_file.py`: bad reports `email-address` at line 12, fixed is clean |
| 4 | P2 `test_manifest_items_and_supporting_files_are_included` needs Git metadata | `1eec204a` | `83fc9132` | the test itself in a tree without `.git`: bad errors on `git ls-files` (exit 128), fixed passes |
| 5 | P2 `tools/verify_export.py` runs the dogfood replay without Git metadata | `cf782bc6` | `e10b190e` | `tests/replays/verify_export_schedule.py`: bad schedules `test_e2e_dogfood.py`, fixed does not |

## Not ingested

- PR 4, P2 concept-map keys not stemmed in `mcp_endpoint.py` (`6b441b78`): the feature was removed in `035ab44a`
  and no test covers the finding, so there is no fixed head to replay.
- Merged PRs 1, 6, 7, 9, 10, 11 had no Codex P1/P2 findings. The coldlocker settle logs record only clean
  receipts and verify-loop failures that are baseline noise (no `config/`), not fix pairs.

`tests/test_codex_findings.py` covers extraction using the verbatim PR 3 finding.
