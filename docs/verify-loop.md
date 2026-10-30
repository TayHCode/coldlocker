# Verify loop

`skills/verify-loop/verify_loop.py` is a stdlib-only runner for Python 3.9+ on Linux, macOS and Windows. It tests what would be merged, so a green branch cannot hide a red base.

```sh
python3 -B skills/verify-loop/verify_loop.py            # local, includes uncommitted files, posts nothing
python3 -B skills/verify-loop/verify_loop.py --post     # committed HEAD only, posts a check run
```

Run it from the pull request worktree; `--pr` (with or without `--post`) fails loudly unless the `--repo` `HEAD` equals the pull request's head and the worktree is clean, and does not check anything out. Options: `--repo`, `--pr`, `--base`, `--cmd`, `--slug owner/repo`, `--check-name`, `--token-cmd`, `--api-url`, `--app-id`. `VERIFY_LOOP_TOKEN_CMD`, `VERIFY_LOOP_API_URL` and `VERIFY_LOOP_APP_ID` set the same defaults.

## Entrypoint

First match wins: `.fleet/verify.sh`, `.fleet/verify.ps1` on Windows, `package.json` `scripts.test` (`npm ci` or `npm install`, then `npm test`), `go test -v ./...`, `cargo test`, Python `test_*.py` in every directory except hidden ones, `node_modules`, `venv`, `env`, `site-packages`, `build` and `dist`, including directories without `__init__.py` (counts are added; pytest when `pytest.ini`, `conftest.py`, a pytest section in `pyproject.toml`, `setup.cfg` or `tox.ini`, or top-level `def test_` functions, `class Test` classes or `import pytest` in the tests select it, else unittest; a configured pytest that is not installed fails). Repos can keep a `.fleet/verify.sh` of their own; none is required. Print `FLEET_TESTS=<n>` or `FLEET_CHECKS=<n>` as the last line to give an exact count. Without a marker the runner reads `Ran N tests`, pytest, `node --test`, jest, cargo and `go test -v` summaries. Skipped tests are not counted (unittest, node --test). Zero or unknown fails.

## Posting

`--post` needs a clean worktree and a GitHub `origin`, or `--slug`. It runs `cold gh-token`, creates one `in_progress` check run named after the base branch's required status check from the App (`--app-id`), else `verify`, and completes that same run. `external_id` is the merged base SHA. A superseded run (a newer run of the same name from the same App) completes as `neutral` and exits nonzero. The test count comes from the last command only, so install output cannot supply it. Local runs refuse a submodule with uncommitted changes. A merge step can compare it with the live base tip and ask for a rerun when they differ. The summary records host, command, base, duration, test count and the log's sha256. `--post` and `--pr` fail closed unless `gh` can read the pull request and HEAD equals the pull request head; the PASS line then shows `head=` and `pr_head=`. `--post` also requires any `--base` must equal its base. An App context is used only when the base's required check belongs to `--app-id`. A failed `git fetch` of the base fails the run instead of using a stale ref. With `--pr` or `--post` the base is fetched from the local remote that points at the pull request's base repository (fork pull requests included); no matching remote fails the run. Submodules are initialized in the snapshot.

## Install

```sh
python3 hub.py link --home --dry-run
python3 hub.py link --home --yes
```

This links `skills/verify-loop` into `~/.claude/skills` and `~/.agents/skills`; the runner is read from the checkout the link points at. Do the same in each host's checkout after pulling.

## Limits

- The verified code runs as the invoking user in a temporary clone with secret-like environment variables removed. It is not sandboxed; run untrusted repos in a throwaway account or container.
- The check is self-attested by whoever holds the token, like any local verification. A pull request that weakens its own tests is caught by review, which the `verification changed` note flags.
- Enabling GitHub Actions, changing rulesets or editing required checks is outside this tool.

## Exported lesson checks

Before project tests run, the runner executes the shell check in every
active or enforced, proven Coldfix lesson marked by `deliver-rules` in
`.coldlocker/rules/`. Only snapshots bearing the delivery marker and matching their completed ownership
registration are executable;
copying an export by hand does not enable its check.
It runs in the same temporary merged checkout with the same scrubbed environment.
A check must exit 0 and emit exactly the JSON object `{"status":"PASS"}` on
stdout, matching Coldfix replay. Empty, malformed, duplicate-field or INCOMPLETE
results fail the gate, as do nonzero exits and malformed lessons. Retired lessons are
skipped. Ordinary rule files have no executable check. Lesson checks do not
replace project tests or contribute to their test count.

Use `hub.py deliver-rules` with the event handler's configured target. Delivery
requires the four-part proof record and passing privacy and release-check gates;
no human review flag is required.
The shell checks require `sh` on the verification host. Checks are trusted code
with the same access as project tests; environment scrubbing is not isolation.
Only explicitly delivered files are run, never arbitrary catalog imports.
