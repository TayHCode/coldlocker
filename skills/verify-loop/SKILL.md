---
name: verify-loop
description: Run the repo's tests locally on the committed head merged with its base branch before any merge, fix failures and rerun until pass, then hand off to review and merge. Use before merging a pull request, when asked to verify, or when no hosted CI is available.
---

# Verify loop

Read [rules/verify-before-merge.md](../../rules/verify-before-merge.md) first. This skill is the procedure; the rule is the policy.

## Run

From the pull request worktree, with everything committed:

```sh
python3 -B <catalog>/skills/verify-loop/verify_loop.py --post --pr N
```

Replace `N` with the PR number. Keep both `--post` and `--pr N` for PR
verification so a passing local run leaves a check on the exact PR head.

`<catalog>` is the Coldlocker checkout that holds this skill. On Windows use `py -3 -B`. Drop `--post` for quick local iteration; it then also tests uncommitted and untracked files and posts nothing. `--pr N` (with or without `--post`) must run from the pull request's worktree: it fails unless `HEAD` equals the pull request head and the worktree is clean, and never checks anything out. Pass `--repo <worktree>` to point at it.

The runner:

1. Clones the committed `HEAD` into a temporary directory and merges the current `origin/<base>`. The base is the pull request's, else the remote default. A conflict fails with `rebase on <base>`.
2. Picks the entrypoint, in order: `.fleet/verify.sh` (`.fleet/verify.ps1` on Windows), `package.json` `scripts.test`, `go.mod`, `Cargo.toml`, then Python `test_*.py` anywhere in the repo (pytest when configured or when the tests use pytest-style functions or classes, else unittest). Override with `--cmd "<command>"`. No repo needs changing.
3. Runs it with token, secret, password, credential and `GH_`/`GITHUB_` variables removed. `FLEET_VERIFY_BASE` holds the merged base SHA.
4. Runs the shell checks of active/enforced proven Coldfix exports in `.coldlocker/rules/` before project tests run. A failed check or malformed export fails verification. Delivery explicitly binds repository-specific exports to this repo. Counts tests. A recipe may end with `FLEET_TESTS=<n>`, or `FLEET_CHECKS=<n>` for build-only repos. Zero tests is a failure even on exit 0.
5. Writes the log to `<git-common-dir>/verify-loop/<head>-<base>-<id>.log` and prints its path. On failure it prints the last lines.
6. With `--post`, refuses a dirty worktree, creates one `in_progress` check run on the head SHA, then completes that same run as `success`, `failure` or, when a newer run of the same name exists, `neutral` when the newer run belongs to the same App (the runner then exits nonzero and prints SUPERSEDED, so the newer result must be assessed). The check name is the base branch's required context from the App (rulesets or classic branch protection), else `verify`. The token comes from `cold gh-token` (`--token-cmd` overrides it) and is never passed to the verified code or printed.

Exit 0 is PASS, 1 is FAIL.

## Coverage

```sh
python3 -B <catalog>/skills/verify-loop/verify_loop.py --coverage N
```

Read-only. Uses `gh` (no App token) to list the last `N` merged pull requests (chosen by merge time from the 100 most recently updated) with their full head SHA and `posted <conclusion>` or `missing` for the check name (`--check-name`, default `verify`). Ends with `posted X of N`. Exit 0 when all are posted, 1 when any is missing, 2 on error. Pass `--slug owner/repo` when origin is not a GitHub URL. It never posts, edits or re-runs anything.

## Loop

1. Run the command. On FAIL, read the log and fix the cause in the worktree. Commit, then run it again. No retry or time limit.
2. If a fix is handed to another agent, resolve its role through [model routing](../../rules/model-routing.md). Never name a model here.
3. When the same failure repeats with no new hypothesis, apply `principle-attack-the-premise` once, then report BLOCKED with the log path.
4. On PASS, merge through `cold review owner/repo PR --builder-model <actual-builder-model> --merge`, or the project's own merge policy. If the base moved since the check, rerun first.
5. If the output says `verification changed`, the diff touched tests, `.fleet/` or workflows. The reviewer must read that diff before merging.

Never post the check by hand and never weaken a test to make it pass. Changing rulesets or required checks needs the owner's approval; list the change instead.
