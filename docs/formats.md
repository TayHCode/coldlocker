# Coldlocker formats (v1)

Entries hold only repo-relative POSIX paths and opaque source ids, so the same manifest can later be served by a hosted endpoint.

## Layout

```
manifest.json               index of shipped examples
policy-pack.json            owner rules added by the optional policy pack
packs.json                  opt-in packs of shipped skills
.hub-store/catalog/         gitignored local layer for imports (same layout, own manifest.json)
skills/<name>/SKILL.md      Agent Skills package; the whole directory is the unit
rules/<name>.md             shared policy fragments
subagents/<name>.md         subagent definitions (one Markdown file each)
commands/<name>.md          slash commands and saved prompts (one Markdown file each)
mcp/servers/<name>.json     nonsecret MCP server definitions
routines/<id>/routine.json  routine definitions (+ optional prompt.md)
plugins/<name>.json         plugin metadata only
connections/<name>.json     live connection metadata only
hub.py                     import / link / check CLI (stdlib only)
store.py                   local secret store (values never in the repo)
mcp_endpoint.py            read-only stdio MCP endpoint listing the hub
gateway_render.py          renders the chosen gateway config from the manifest
checks/                    verification helpers used by `hub.py check`
examples/                  importable sample inputs
docs/, tests/
config.example.json         copy to gitignored config.json to override store_path
```

Entry names and routine ids match `^[a-z0-9][a-z0-9-]{0,62}$`.

## manifest.json

```json
{
  "schema_version": 1,
  "name": "coldlocker",
  "license": "MIT",
  "skills":      [ENTRY],
  "rules":       [ENTRY],
  "mcp_servers": [ENTRY],
  "routines":    [ENTRY],
  "plugins":     [ENTRY],
  "connections": [ENTRY],
  "subagents":   [ENTRY],
  "commands":    [ENTRY]
}
```

ENTRY (routines use `id` instead of `name`):

```json
{
  "name": "example",
  "path": "skills/example",
  "owner": "coldlocker",
  "provenance": {"source_id": "src-0123456789ab", "origin": "authored"},
  "sha256": "<64 hex>"
}
```

- `path` is repo-relative POSIX with no `..`, no leading `/` and no drive letter.
- `provenance.source_id` is `src-` plus the first 12 hex characters of the content sha256 at import time. It is opaque: never a filesystem path or hostname.
- `provenance.origin` is a short label (`authored`, `upstream`, `local-import`, ...) with no path separators.
- `owner` is one short label naming who maintains the item.
- A file's `sha256` is the hex digest of its bytes. A directory's `sha256` is the hex digest of the concatenation, in sorted relative-path order, of `"<relpath>\0<file sha256 hex>\n"` for each regular file. Symlinks and `.git` are skipped.
- Lists are sorted by name or id. The manifest is written as 2-space JSON with a trailing newline.

## packs.json

```json
{
  "schema_version": 1,
  "always": ["hub-discover"],
  "packs": {"pstack": ["evidence-before-done", "frontend-design", "..."]}
}
```

- Every shipped skill in `manifest.json` appears exactly once, in `always` or in one pack, and the skills in `policy-pack.json` join `pstack`; `hub.py check` and `doctor` fail otherwise.
- Listing, linking, the UI counts and remote publishing show `always`, the skills of packs that are on, and imports. `hub.py check` still hashes and scans every shipped skill.
- Which packs are on is kept in the gitignored `.hub-store/packs.json` as `{"version": 1, "enabled": [<sorted pack names>]}`, written by `hub.py pack add|remove`.
- Without that file, an install that already has `.hub-store/catalog/manifest.json`, `.hub-store/links.json` or `.hub-store/calls.jsonl` was set up before packs existed, so every pack is on. Otherwise none are. The first import, link or catalog call writes the file, so later state can't change the answer.
- A catalog root without `packs.json` lists every shipped skill, and `check` and `doctor` report `SKIP: skill packs`.

## policy-pack.json

```json
{"schema_version": 1, "rules": [ENTRY], "skills": [ENTRY]}
```

- Present only when the optional policy pack is installed. Its rule and skill entries join the shipped `rules` and `skills` everywhere `manifest.json` is read, and a name may not repeat a shipped one. Its skills are the pstack skills that need the pack's rules or `models.yaml`. They join the `pstack` pack, so `packs.json` names only the pstack skills in `manifest.json`.
- The pack's files are listed in `POLICY_FILES` in `checks/validators.py`. Without the pack, `check` and `doctor` report `SKIP: instruction consistency`, `update` skips the hook self-test, and doc links into pack files are not required to resolve. With `policy-pack.json` present, a missing pack file fails these checks and `update` rolls back. If `policy-pack.json` is missing while `~/.claude/settings.json` or the Codex `hooks.json` still runs this checkout's hooks, `check`, `doctor` and `link` fail, and `update` rolls back.

## MCP server definition

`mcp/servers/<name>.json`:

```json
{
  "name": "example-server",
  "description": "one line",
  "transport": "stdio",
  "command": "npx",
  "args": ["-y", "some-mcp-package"],
  "env": {"API_TOKEN": "secret://example-api-token", "LOG_LEVEL": "info"}
}
```

HTTP servers use `"transport": "http"`, `"url"` and optional `"headers"`.

- A stdio argument may begin with `${COLDLOCKER_ROOT}/`, for example `${COLDLOCKER_ROOT}/examples/example-server.py`. The gateway renderer expands the prefix against the selected manifest root and rejects traversal and symlinks. The catalog keeps the portable form; other clients must resolve the prefix before running it.
- On Windows a `python3` command renders as the running interpreter, so the bundled example server starts there too. See [gateway.md](gateway.md).
- Values in `env` and `headers` are either a `secret://<store-name>` reference or a short nonsecret literal.
- A key matching `token|secret|key|password|authorization|credential` (case-insensitive) must be a `secret://` reference. Import keeps an empty value as `""` and moves every non-empty value into the private store as a `secret://` reference.
- The definition never contains resolved values.

## Routine definition

`routines/<id>/routine.json`:

```json
{
  "id": "example-digest",
  "description": "one line",
  "owner": "coldlocker",
  "runner": "t3-scheduler",
  "schedule": {"type": "fixed_time", "time_of_day": "09:00", "weekdays": [1, 2, 3, 4, 5]},
  "timezone": "UTC",
  "prompt": "prompt.md",
  "overlap": "skip",
  "timeout_seconds": 600,
  "enabled": false
}
```

- Each routine has exactly one `owner` and one `runner`, its preferred platform: `t3-scheduler`, `systemd-timer`, `cron`, `launchd`, `n8n`, `hosted-bot`, `claude-code`, `codex`, `dots`, `muse`, `mausbot` or `schtasks`. A user can choose another runner when enabling.
- `schedule.type` is `interval` (`every_seconds`), `fixed_time` (`time_of_day`, optional `weekdays`), `cron` (`expression`) or `webhook`.
- `overlap` is `skip`, `queue` or `allow`.
- Portable definitions must keep `enabled: false`. Import and link never activate a routine. Explicit per-runner enablement is recorded in the private local store. See [routines.md](routines.md).

## Secrets

- A secret appears only as `secret://<name>`, where `<name>` matches `^[a-z0-9][a-z0-9._-]{0,62}$`.
- Values live in the local store, `.hub-store/` by default (directory mode 700, files mode 600). `store_path` in a gitignored `config.json` can move it. Both are gitignored.
- Only an in-process caller, the gateway adapter, may read a value. No CLI command or endpoint response prints one.
- OAuth access and refresh tokens are stored the same way, under names chosen by the importer. Provider and model account logins stay product-owned and out of scope.

## Subagents and commands

`subagents/<name>.md` and `commands/<name>.md` are stored byte for byte like rules, after the same home-path rewrite and private-content validation. The file keeps the source client's frontmatter (for example Claude Code's `tools` and `model`); other clients may ignore fields they don't know. Both collections use the same ENTRY rules, are optional in older v1 manifests (treated as empty), and are served by `hub_list_subagents`, `hub_list_commands` and `hub_get_item` (kind `subagent` or `command`).

Hooks are never a catalog kind. Scan lists them as `skipped` because they are client-specific and not portable.

## Plugin and connection metadata

`plugins/<name>.json` has exactly `name`, `agent`, `source`, `version`, `enabled` (boolean) and `install_hint`. `source` is a nonsecret marketplace or package reference. Records never include plugin code, local install paths, credentials or executable setup instructions.

`connections/<name>.json` has exactly `name`, `provider`, `endpoint` and `auth_status` (`unknown`, `configured`, `needs-auth` or `connected`). `endpoint` is a nonsecret HTTP(S) URL with no credentials, query or fragment. No tokens, cookies or auth payloads are accepted. `auth_status` describes locally recorded metadata and does not prove live authorization. Import records a live upstream reference without activating, authenticating or reconfiguring any provider.

Both collections use the same ENTRY hash, owner and provenance rules and the read-only tools `hub_list_plugins` and `hub_list_connections`. Older v1 manifests may omit them; they are then treated as empty. Scan imports add the collections as needed. `importers.parse_metadata` validates the strict schemas and rejects unknown fields.

## Conventions

- Python 3.11+, standard library only, no network in tests. Run tests with `python3 -m unittest discover -s tests -v`.
- No code comments by default.
- Tracked files must pass the `hub.py check` scan: no tokens, private names, private hostnames, email addresses or absolute home paths. Reserved documentation values, fixture labels, common encoding and package labels, and the commit-email allowlist have explicit exceptions.
- Scanner patterns must not match the scanner's own source (for example `[/]home[/]`). Test fixtures that need a forbidden string build it at runtime.

## Delivered lessons

A lesson export contains a `## Lesson record` JSON fence with `schema_version: 1`
or `2`, `lesson`, `source_evidence`, `what_went_wrong`, `fix`, `rule`, `replay`,
and `gates`. Version 2 adds an admission projection and a reversed replay result,
with matching test digests. Either version may include validated `facts` excerpts
from the rule. Version 1 remains supported with its original fields.
The four required parts are source references plus failure summary, fix summary,
prevention rule, and replay proof. Replay binds a receipt and check digest to
different before/after commits and trees, FAIL/exit 1 before, PASS/exit 0 after,
and the expected failure identifier. Both privacy and release-check must be PASS.
The lesson source canonicalizes authored lesson text to LF before proof and export.

Delivery records completed ownership under `.coldlocker/rules.json`; each name
has `hashes`, `lesson`, and `pending` fields. Ownership fingerprints normalize
CRLF to LF, so Git text conversion does not invalidate a delivered lesson. Catalog
manifest hashes continue to cover original bytes. Pending transitions retain known
old/new hashes until instructions and files are complete. Modified, unregistered,
or incomplete lessons fail verification. Withdrawal archives owned snapshots and
removes active registration and managed links.

`hub.py sync-lessons` reads a lessons folder (`--lessons` or
`COLDLOCKER_LESSONS`). Each lesson listed in `briefing.md` whose front matter has
`status: active` or `enforced` and a `verification` starting with `proven` becomes
a local lesson rule named from a fixed prefix, its id and slug (at most 63 characters), with the lesson source as owner and origin.
Lesson rules this command mirrored for lessons that no longer qualify are removed
first. A missing `briefing.md`, or one that lists no lessons, fails without changing
the catalog. Lessons whose id is duplicated or differs from the file name, or whose
name would be invalid, are not served, and the command exits 1 after syncing the rest. Lesson delivery does not depend
on this mirror.
