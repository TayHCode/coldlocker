# Changelog

## Unreleased

- The publication guard denies `git <word>` and `gh <word>` when the word is not a built-in subcommand, so an alias or extension from git config, gh config, `-c` or `GIT_CONFIG_*` can no longer hide a push or release (#234, #239). The sets come from `tests/builtin_probe.py` and leave out names an alias can shadow, and `git send-pack` and `git http-push`, which push without the word `push` (#243). A glob, brace, variable or command substitution that can stand for git or gh inside another command is denied when what follows looks like a git or gh call. `$'...'` quoting is decoded before words are read, and a bash extended glob is denied.

- The fleet policy layer is now an optional pack. The five owner rules moved from `manifest.json` to `policy-pack.json`, so an install without the pack lists no owner rules. Without the pack, `check`, `doctor`, `link` and `update` skip the instruction consistency check, hook lessons and the hook self-test instead of failing. `checks/validators.py` lists the pack's files in `POLICY_FILES`.

- The `hub_list_rules` tool description and the `connect --remote` verify prompt no longer name a specific owner rule; agents read whichever listed rules apply. `AGENTS.md` sends agents to the routing rule only when the optional policy pack is installed.

- The publication guard now refuses `gh pr create` to a repository where the signed-in `gh` account already has an open pull request, so an account keeps at most one open PR per upstream repo across every agent. Repositories the account owns are exempt, and a failed lookup denies the call.

- The owner guard now checks only the write targets it can parse (redirects, operands of writing programs and unknown programs, and the scripts that shells, `ssh`, `su`, launchers and `$(...)` run) instead of resolving every word as a path. Of the 1,254 recorded false owner denials from 2026-11-03 to 11-09, 60 are still denied (main: 973), and the guard replay baseline drops from 11 to 6 (the 6 left are publication-guard denials). `gh repo rename` and `gh repo create --source/--remote` are now denied as origin changes, as are `ln` sources in `~/.coldlocker`, `git clean` paths, `-C`/`--work-tree` folders (or else the working folder) of git commands that write the worktree, `find -delete` in a folder holding owner state, and unknown programs' plain operands into `.git` (`tar -C .git`). Commands it cannot read fall back to the old word-by-word check (#122).

- Replay a scrubbed corpus of 68 guard probes in maintainer CI. Missed required denials and increases above the committed false-denial baseline fail verification.

- `hooks/install.py --write` now takes an OS file lock (`.coldlocker-hook.lock`) instead of lock directories, so a killed installer no longer blocks every later install. Leftover `.coldlocker-hook-lock` and `.agentlocker-hook-lock` directories from older installers are ignored and can be deleted.

- Local routines record a slot only when the agent starts (`started`) or `skip` drops it (`skipped`). A tick killed before then, for example while queued, no longer consumes the occurrence. A queued tick older than the last recorded slot no longer runs or rewinds the receipt.

- Add `hub.py model get` and `hub.py model target`, which print a role's route and dispatch target from Coldlocker's `models.yaml` without `cold`. The dispatch guard uses them when its hook root is the Coldlocker checkout, so `hooks/install.py --hook-root <checkout>` no longer requires `cold`; any other hook root still does.

- `connect --write` now creates `~/.cursor/mcp.json` for a detected Cursor and merges OpenCode's plain-JSON first-run `opencode.jsonc` in place. A fresh-VM acceptance run for Gemini CLI, OpenCode, Cursor CLI, VS Code and Claude Desktop is recorded in docs/clients.md.

- Shorten the README to a quickstart a newcomer can run as written: the literal public clone first, then doctor, scan, import, connect and `claude mcp list`. Hook documentation moves to `docs/hooks.md`, and every doc states one Python floor, 3.11+ (Windows 3.12.4+).

- Make shipped skills opt-in packs. A new install lists only `hub-discover` and your imports, so `hub_list_skills` drops from about 27 KB to under 4 KB. `python3 hub.py pack add pstack` lists the other 57 again. Installs that already had imports, links or catalog calls keep every pack on.

## 0.1.0 - 2026-11-09

- Add `hub.py --version`. The private repository tags this version `v0.1.0`.

- Add `hub.py update`: fast-forward `main` from origin, run `doctor` and a hook self-test, print the rollback SHA, and roll back when a check fails.

- Stop `scan` offering and `scan --import` importing the `coldlocker` and `coldlocker-gateway` entries that `connect` wrote, so the hub no longer lists and proxies itself.

- Stop the delivered `model-routing` rule linking to `../docs/...`, which does not exist in a target repo's `.coldlocker/rules/`.

- Rename the example routine to `example-routine` so the documented `import-routine examples/routine.example.json` no longer fails as a shipped-name collision.

- Add `--stat` to the export diff command for per-path added and removed line counts.

- Add `--show PATH` to the export diff command to show published versus checkout content.

- Add a read-only export diff command that reports what the next public publish would add, remove or change.

- Add a post-publish smoke command that checks a fresh Coldlocker clone with the export README's health, safety and MCP endpoint commands.

Self-hosted shared skills and routine instructions for local agents and hosted
bots. Claude Code, Codex CLI and Hosted Bot have bounded owner-trial evidence for
catalog use and updates; routine execution through controlled local clients is
also recorded. Automatic scheduling remains optional and unverified. Version
0.1.0 marks this source; it is not a supported release. See [evidence and release requirements](docs/release.md).

- Rebind the Coldlocker source-review approvals to release-check's umask-independent git-style fingerprints, so a fresh clone of the published export passes under any umask.
- Make the upgrade guide's imported-item check need only Python and Git, so it works on Windows, macOS and Linux.
- Add the `verify-loop` skill, rule and stdlib runner: test the committed head
  merged with its base branch, post one check run to the head SHA, and fix and
  rerun until it passes. Models and effort come only from model routing.
- Add an opt-in Claude Code PreToolUse hook that auto-picks the recommended
  option instead of asking, except for money, publishing, deleting or messaging.
- Add an opt-in drift-check Stop hook: a cheap reviewer model checks the
  change against the goal and sends back cuts, for at most two rounds.
- Add an opt-in keep-going Stop hook and installer for Claude Code and Codex.
- Add a repeatable offline proof covering import with supporting files, catalog
  reads, client configuration preservation, update history and removal.
- Record successful model-driven Claude Code and Codex catalog acceptance on
  synthetic imported content using existing product logins.
- Warn when client connection entries point at a linked Git review worktree.
- Document runtime requirements, private-state backup and isolated restoration,
  stable installation, release gates and rollback limits.
- Correct the current audit disposition while retaining the historical audit.
- Configure one self-hosted catalog URL for hosted client instructions, routine
  prompts and optional Composio registration, with validated per-command overrides.
- Require explicit local-content selection before publishing and support an
  installed Wrangler alongside `cf` and the Cloudflare API transport.
- Add `doctor` for installed catalogs without imposing maintainer commit
  identities, and document the first-user self-host setup.

Executor, MCPHub, Composio, agents-sync, the setup wizard and the remote Worker
remain included. Live upstream providers, gateway operation, automatic
schedules, other operating systems and independent-user installation remain
unverified. Hosted catalog evidence is limited to the recorded owner staging
trial. See the [release guide](docs/release.md).
