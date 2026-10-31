---
name: verify-coldlocker
description: Verify the public Coldlocker export through its CLI and MCP endpoint, plus the offline Eve experiments.
---

# Verify Coldlocker

## Launch

Use Python 3.11 or later from the repository root. The CLI and stdio MCP server run per invocation; no service, credential or deployment is needed.

## Doctor

Run `python3 -B hub.py doctor`. Require all checks to pass before continuing.

## Drive

Run `python3 -B tools/verify_export.py`. This executes the README's exported-catalog checks plus both experiment test files and the two public export-manifest checks. Endpoint tests drive JSON-RPC discovery and package retrieval, including content hash rejection. Runtime proofs restart a fresh subprocess across both interruption boundaries. On Windows use `python` in place of `python3`; `.fleet/verify.ps1` is the ordinary native verification recipe.

The private maintainer suite needs omitted `config/` files. This recipe covers the public export only, and does not claim a full maintainer-suite pass. Worker code is unchanged by this experiment.

## Evidence

Keep stdout, stderr, exit status and `git rev-parse HEAD` in `.scratch/`. For token measurements, install `tiktoken==0.14.0` in a scratch virtual environment and run `tools/catalog_search_eval.py`. The default uses six public excerpts. The complete frozen history stays private; `--history-private FILE` verifies its fingerprints. Never commit raw transcripts.

## Cleanup

Tests remove their own temporary directories and end their subprocesses. Preserve `.scratch/` evidence. No shared processes, stores or user settings need cleanup.

## Helpers

`tools/verify_export.py` runs the export checks. `tools/catalog_search_eval.py` writes measured retrieval token counts, not model billing. See [features](features/README.md).
