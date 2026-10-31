# agentlocker overview

agentlocker keeps your skills, rules, routines and MCP server definitions in one
local catalog that every agent can reach. For the commands, start with the
[README](../README.md).

The goal, from the [vision](../VISION.md): make it stupid simple to import all
skills, routines, MCP servers, connections and plugins, then have them reachable
from any agent.

- **One locker.** Everything lives in one repository-shaped folder with one index.
- **Agents first.** An agent finds everything from the repo root through
  [`AGENTS.md`](../AGENTS.md), `manifest.json` and plain `SKILL.md` packages.
- **Sign in once.** MCP authentication happens once in the locker. Secret values
  never enter Git.

## How it works

### The store and the manifest

The locker is the repository checkout.

| Path | Holds |
| --- | --- |
| `skills/<name>/` | Agent Skills packages; the whole directory is the unit. |
| `rules/<name>.md` | Shared policy fragments. |
| `mcp/servers/<name>.json` | Nonsecret MCP server definitions. |
| `routines/<id>/routine.json` | Routine definitions with an optional prompt file. |
| `plugins/<name>.json`, `connections/<name>.json` | Metadata records only. |
| `manifest.json` | Index of the shipped examples. |
| `.hub-store/catalog/` (gitignored) | Your imports in the same layout, with their own `manifest.json`. Readers merge it with the shipped index. |
| `.hub-store/` (gitignored) | Secret values, history and local state. |

Each manifest entry has a relative path, one `owner`, `provenance` and a `sha256`
hash. The exact contract is in [formats.md](formats.md).

### Secrets

Definitions hold references such as `secret://example-api-token`. Values live in
`.hub-store/` (directories 700, files 600). `store.py` has `set`, `has`, `list`,
`path` and `delete`; `set` reads stdin or a prompt, and no command prints a value.
Only the gateway adapter reads values, in process, to hand them to an upstream.

### Commands

`hub.py` uses only the Python standard library. See [cli.md](cli.md) and
[reference.md](reference.md).

| Command | Purpose |
| --- | --- |
| `scan` | Read-only inventory across agents; `--import` imports eligible items. |
| `serve` | Local setup wizard. |
| `connect` | Prints client entries; `--write` merges them after confirmation; `--remote` prints hosted-bot instructions. |
| `list`, `remove` | Inspect and remove imports. Re-importing a changed source updates it and keeps the old copy in `.hub-store/history/`. |
| `import-skill`, `import-rule`, `import-mcp`, `import-routine`, `import-plugin`, `import-connection` | Record content or metadata only. They never schedule, execute or contact anything. |
| `link --target <dir>` or `link --home` | Relative skill symlinks into `.claude/skills` and `.agents/skills`. Never replaces existing files. |
| `routine enable/disable/status` | Per-platform routine enablement; see [routines.md](routines.md). |
| `publish-remote` | Upload the catalog to the remote server; see [remote.md](remote.md). |
| `connect-composio` | `--catalog` exposes the remote locker to Composio; `--name`/`--url` register an upstream. |
| `import-executor`, `agents-import`, `agents-export`, `agents-sync` | Executor and amtiYo/agents connectors. |
| `check` | Verifies the repository (below). |

Companion tools: `gateway.py` (install, prepare, start, stop, status, sync,
refresh, url, register-executor), `store.py` (secrets) and `mcp_endpoint.py`
(the catalog endpoint).

### Scan, then import

`hub.py scan` reads each agent's locations (Claude Code, Codex, Cursor, OpenCode,
Gemini, Hermes, Grok Bot, T3, `.agents`) and system schedules, using one scanner
module per agent. It never writes. Findings are `new`, `connect`, `needs_review`,
`duplicate`, `already_in_locker`, `conflict`, `vendor` or `skipped`. See
[scan.md](scan.md).

Import first runs a local heuristic [safety scan](safety.md). Risky items become
`needs_review` and are imported only with `--approve-risky <id>` or the wizard's
per-row checkbox; items with an embedded secret are skipped. It is a triage aid,
not a guarantee.

### The catalog endpoint

`python3 mcp_endpoint.py` speaks MCP over stdio. Tools: `hub_list_skills`,
`hub_list_rules`, `hub_list_mcp_servers`, `hub_list_routines`, `hub_list_plugins`,
`hub_list_connections` and `hub_get_item`, which returns a skill with all its
supporting files. Skills are also MCP prompts, and files are `agentlocker://`
resources. Listing never executes an upstream server or reads secret values.

The same catalog is served read-only with OAuth at `https://agentlocker.dev/mcp`
for hosted bots ([remote.md](remote.md)).

### The gateway

The optional gateway ([gateway.md](gateway.md)) gives agents one MCP address for
every server in the locker. It wraps the pinned npm package `@samanhappy/mcphub`
and serves streamable HTTP at `/mcp` on IPv4 loopback (default port 43871). It
is configured defensively: Host and Origin checks, opt-in bearer-token
authentication (`--require-token`) and the web dashboard off unless requested.
Set `gateway.backend` to `executor` to use a running Executor instead
([executor-connector.md](executor-connector.md)).

### The setup wizard

`python3 hub.py serve` opens a local page with Welcome, Scan, Sign in and Connect
screens ([ui.md](ui.md)). It binds to loopback, checks CSRF tokens, Host and
Origin, and only displays snippets; `hub.py connect --write` is what edits client
configs. Credential entry is covered in [mcp-auth.md](mcp-auth.md).

### Routines

A routine has one owner and one preferred runner and is imported disabled.
`hub.py routine enable` previews, and with `--write` plus confirmation installs a
local job or prints a platform handoff. See [routines.md](routines.md).

## What it connects to

Exact paths and merge rules are in [clients.md](clients.md).

| Client | How it connects |
| --- | --- |
| Claude Code, Codex, Cursor, OpenCode, Gemini, VS Code | `connect` prints entries; `--write` merges them with a backup. Codex TOML needs Python 3.11+. |
| Hermes, Windsurf | `connect` prints entries for manual merging; `--write` is unsupported and leaves their files unchanged. |
| T3 | Discovered by `scan` (databases opened read-only). |
| Grok Bot, Claude, ChatGPT, Codex CLI/app | Remote catalog via `connect --remote`; Grok Bot also has a [bootstrap](grok-bot-bootstrap.md). |
| Codex Cloud, Dots, hosted Muse, MausBot, T3 remote OAuth | Remote connection unconfirmed; `connect --remote` prints best-effort steps. |
| Any other MCP client | The gateway URL (`python3 gateway.py url`) or `mcp_endpoint.py` over stdio. |

Upstreams, used live rather than copied: [Executor](executor-connector.md),
[Composio](composio.md), [amtiYo/agents](agents-sync.md) and
[MCPHub](gateway.md). Related projects are in [related.md](related.md); the
boundary with Factory-style tooling is in [factory-overlap.md](factory-overlap.md).

## Checks

`python3 hub.py check` fails if any of these fail: manifest schema, MIT license,
content hashes, local state ignored by Git, the secret and private-path scan
(`checks/scan.py`), and commit emails against `config/commit-email-allowlist.txt`.
New commit text is checked with
`python3 -m checks.commit_text --base <trusted-base> --head HEAD`. The suite runs
with `python3 -m unittest discover -s tests -v`; client wiring is always tested
against temporary fixture homes. CI and acceptance details are in
[acceptance.md](acceptance.md).

## Design

- Python 3.10+ standard library only; Node is needed only for the optional gateway.
- Read-only by default; writes are explicit, backed up and tested on fixtures.
- References, not secret values.
- Live upstream over copy; one owner per item; provenance recorded on import.
- Private until a human approves release.

## Limits

- **Platforms.** The CLI, scan, import, connect and stdio endpoint run on Linux, macOS and Windows; the HTTP gateway runs on Linux and macOS only.
- Hosted-bot connections other than those verified from vendor docs are unconfirmed, and OAuth sign-in has not been exercised against every provider.
- The gateway is single-user, with no downstream auth beyond its optional bearer token.
- Scan misses T3 schedule changes that exist only in a write-ahead log, and never reads Windows Task Scheduler.
- The safety scan is heuristic.
- Imports do not serialize other writers or recover automatically after process termination ([cli.md](cli.md)).

Current verification status: [audit/current-disposition.md](../audit/current-disposition.md).
