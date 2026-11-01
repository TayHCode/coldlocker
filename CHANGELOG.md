# Changelog

## Unreleased

- Stop `scan` offering and `scan --import` importing the `agentlocker` and `agentlocker-gateway` entries that `connect` wrote, so the hub no longer lists and proxies itself.

- Stop the delivered `model-routing` rule linking to `../docs/...`, which does not exist in a target repo's `.coldlocker/rules/`.

- Rename the example routine to `example-routine` so the documented `import-routine examples/routine.example.json` no longer fails as a shipped-name collision.

- Add `--stat` to the export diff command for per-path added and removed line counts.

- Add `--show PATH` to the export diff command to show published versus checkout content.

- Add a read-only export diff command that reports what the next public publish would add, remove or change.

- Add a post-publish smoke command that checks a fresh Coldlocker clone with the export README's health, safety and MCP endpoint commands.

Self-hosted shared skills and routine instructions for local agents and hosted
bots. Claude Code, Codex CLI and Grok Bot have bounded owner-trial evidence for
catalog use and updates; routine execution through controlled local clients is
also recorded. Automatic scheduling remains optional and unverified. No release
tag exists yet. See [evidence and release requirements](docs/release.md).

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
