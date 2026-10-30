# agentlocker formats (v1)

Entries hold only repo-relative POSIX paths and opaque source ids, so the same manifest can later be served by a hosted endpoint.

## Layout

```
manifest.json               index of shipped examples
.hub-store/catalog/         gitignored local layer for imports (same layout, own manifest.json)
skills/<name>/SKILL.md      Agent Skills package; the whole directory is the unit
rules/<name>.md             shared policy fragments
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
  "name": "agentlocker",
  "license": "MIT",
  "skills":      [ENTRY],
  "rules":       [ENTRY],
  "mcp_servers": [ENTRY],
  "routines":    [ENTRY],
  "plugins":     [ENTRY],
  "connections": [ENTRY]
}
```

ENTRY (routines use `id` instead of `name`):

```json
{
  "name": "example",
  "path": "skills/example",
  "owner": "agentlocker",
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

- A stdio argument may begin with `${AGENTLOCKER_ROOT}/`, for example `${AGENTLOCKER_ROOT}/examples/example-server.py`. The gateway renderer expands the prefix against the selected manifest root and rejects traversal and symlinks. The catalog keeps the portable form; other clients must resolve the prefix before running it.
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
  "owner": "agentlocker",
  "runner": "t3-scheduler",
  "schedule": {"type": "fixed_time", "time_of_day": "09:00", "weekdays": [1, 2, 3, 4, 5]},
  "timezone": "UTC",
  "prompt": "prompt.md",
  "overlap": "skip",
  "timeout_seconds": 600,
  "enabled": false
}
```

- Each routine has exactly one `owner` and one `runner`, its preferred platform: `t3-scheduler`, `systemd-timer`, `cron`, `launchd`, `n8n`, `grok-bot`, `claude-code`, `codex`, `dots`, `muse`, `mausbot` or `schtasks`. A user can choose another runner when enabling.
- `schedule.type` is `interval` (`every_seconds`), `fixed_time` (`time_of_day`, optional `weekdays`), `cron` (`expression`) or `webhook`.
- `overlap` is `skip`, `queue` or `allow`.
- Portable definitions must keep `enabled: false`. Import and link never activate a routine. Explicit per-runner enablement is recorded in the private local store. See [routines.md](routines.md).

## Secrets

- A secret appears only as `secret://<name>`, where `<name>` matches `^[a-z0-9][a-z0-9._-]{0,62}$`.
- Values live in the local store, `.hub-store/` by default (directory mode 700, files mode 600). `store_path` in a gitignored `config.json` can move it. Both are gitignored.
- Only an in-process caller, the gateway adapter, may read a value. No CLI command or endpoint response prints one.
- OAuth access and refresh tokens are stored the same way, under names chosen by the importer. Provider and model account logins stay product-owned and out of scope.

## Plugin and connection metadata

`plugins/<name>.json` has exactly `name`, `agent`, `source`, `version`, `enabled` (boolean) and `install_hint`. `source` is a nonsecret marketplace or package reference. Records never include plugin code, local install paths, credentials or executable setup instructions.

`connections/<name>.json` has exactly `name`, `provider`, `endpoint` and `auth_status` (`unknown`, `configured`, `needs-auth` or `connected`). `endpoint` is a nonsecret HTTP(S) URL with no credentials, query or fragment. No tokens, cookies or auth payloads are accepted. `auth_status` describes locally recorded metadata and does not prove live authorization. Import records a live upstream reference without activating, authenticating or reconfiguring any provider.

Both collections use the same ENTRY hash, owner and provenance rules and the read-only tools `hub_list_plugins` and `hub_list_connections`. Older v1 manifests may omit them; they are then treated as empty. Scan imports add the collections as needed. `importers.parse_metadata` validates the strict schemas and rejects unknown fields.

## Conventions

- Python 3.10+, standard library only, no network in tests. Run tests with `python3 -m unittest discover -s tests -v`.
- No code comments by default.
- Tracked files must pass the `hub.py check` scan: no tokens, private names, private hostnames, email addresses or absolute home paths. Reserved documentation values, fixture labels, common encoding and package labels, and the commit-email allowlist have explicit exceptions.
- Scanner patterns must not match the scanner's own source (for example `[/]home[/]`). Test fixtures that need a forbidden string build it at runtime.

## Delivered Coldfix lessons

A lesson export contains a `## Lesson record` JSON fence with `schema_version: 1`,
`lesson`, `source_evidence`, `what_went_wrong`, `fix`, `rule`, `replay`, and `gates`.
The four required parts are source references plus failure summary, fix summary,
prevention rule, and replay proof. Replay binds a receipt and check digest to
different before/after commits and trees, FAIL/exit 1 before, PASS/exit 0 after,
and the expected failure identifier. Both privacy and release-check must be PASS.
Coldfix canonicalizes authored lesson text to LF before proof and export.

Delivery records completed ownership under `.coldlocker/rules.json`; each name
has `hashes`, `lesson`, and `pending` fields. Ownership fingerprints normalize
CRLF to LF, so Git text conversion does not invalidate a delivered lesson. Catalog
manifest hashes continue to cover original bytes. Pending transitions retain known
old/new hashes until instructions and files are complete. Modified, unregistered,
or incomplete lessons fail verification. Withdrawal archives owned snapshots and
removes active registration and managed links.
