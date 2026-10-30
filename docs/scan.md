# Scan

Use the [copy-paste import prompt](IMPORT-PROMPT.md) for guided inventory, approval, import and client connection with any agent.

```sh
python3 hub.py scan
python3 hub.py scan --json
python3 hub.py scan --import
```

Scan is read-only. It writes no files or store state and never runs skills, upstream commands, plugins or routines. It never changes product config, logins, tokens, cron, systemd, LaunchAgents, T3 state, services or schedules.

## Output

The human view shows per-agent and per-type counts, with rows for `new`, `connect`, `conflict` and `skipped`. Each `skipped` row prints its cause and one `next:` step. Reasons name the finding category and line, never the value (`package too large`, `tailnet-ip at line 3`, `unsupported OnCalendar expression`, `service file missing`). `--json` carries `reason` and `next_step` on skipped and `needs-manual` items; the `needs-manual` flag remains for compatibility.

Vendor, plugin-cache, duplicate and already-in-locker rows collapse into one count line per agent. `--all` lists every row; `--json` always includes every item. On Windows, printed commands use the running interpreter instead of `python3`.

## Import

`--import` prints the grouped inventory, asks for confirmation, and imports every eligible item. Non-interactive imports need `--yes`. `--only sc-0123456789ab ...` selects IDs. After import the CLI prints `python3 hub.py connect` when that command is installed.

Every user item first gets a local [safety scan](safety.md). Flagged items show as `needs review` with the reason and are skipped unless you pass `--approve-risky ID` (repeatable, or several IDs after one flag). `--safety` prints the read-only report.

Apply re-scans and resolves IDs internally, refuses unindexed destination collisions, and leaves existing different content alone. Repeated imports leave indexed equal content unchanged. Imports use one `agentlocker` owner and a short `scan-<agent>` provenance.

## Options

- `--home DIR` selects a fixture or alternate home. Unless it is the real home, scan skips live reads (`crontab -l`, the local Executor session) and says so; `--live` includes them.
- `--project DIR` (repeatable) and `--projects-root DIR` (up to 1,000 immediate folders) include project settings. Project skills and instructions are read only for these folders.
- `--agent NAME` (repeatable) filters agents. An unknown name exits non-zero and lists valid agents. A valid agent with nothing found prints `<agent>: nothing found` and the paths it looked in.
- `--platform linux|darwin|win32` selects platform discovery independent of the host.
- `CODEX_HOME` selects the active Codex directory for the real-home scan. An explicit `--home` ignores the ambient `CODEX_HOME`.
- The Python API also accepts `env` and a read-only injectable `runner`, so fixture tests need no installed products.

## Sources

One scanner module per agent. Dot paths are relative to the selected home unless marked project-scoped.

| Agent | Skills and instructions | MCP, plugins, connections, schedules |
| --- | --- | --- |
| Claude | `.claude/skills`, `.claude/CLAUDE.md` (rule `claude`), project `CLAUDE.md` (`<project>-claude`) | `.mcp.json`, `.claude.json` (including per-project servers), `.claude/.mcp.json`; Claude Desktop `claude_desktop_config.json`; plugin records and enabled plugins in settings; connection records and `mcp-needs-auth-cache.json` status; `.claude/scheduled-tasks` (JSON/TOML routines; a `SKILL.md` task is `needs-manual`) |
| Codex | `.codex/skills`, `.codex/AGENTS.md` (rule `codex-agents`), `AGENTS.override.md`, project instruction files (`<project>-agents`); `.codex/rules/*.rules` are listed as skipped, never imported | `.codex/config.toml`, plugin records and cached metadata, extensions, `.codex/automations/*` JSON/TOML |
| agents | `.agents/skills` | `.agents/agents.json` flags managed content for `agents-import`, `agents-export` or `agents-sync`; nothing is duplicated automatically |
| Cursor | `.cursor/skills`, `.cursor/rules/*.md` and `*.mdc`; `.cursor/skills-cursor` (built-in, vendor, not imported) | home and project `.cursor/mcp.json`, `.cursor/extensions/extensions.json` |
| OpenCode | `skills`, `agents`, `commands` (singular folders too), `AGENTS.md` in config or project `.opencode` (rule `opencode-agents`) | `opencode.json`/`opencode.jsonc`: `mcp` local command arrays, environment, remote headers; `plugin` entries |
| Gemini | `~/.gemini/skills`, `GEMINI.md`, TOML commands, extension skills, context files (including the manifest's `contextFileName`) and commands | `settings.json` MCP servers (`httpUrl` normalizes to HTTP); extension manifests. No documented on-disk schedule store, so no routines |
| Hermes | `HERMES_HOME` (default `~/.hermes`) skills and `SOUL.md`; project `.hermes.md` and `HERMES.md` | `config.yaml` MCP servers; JSON cron jobs. Cron and interval jobs import disabled; one-shot jobs are skipped |
| T3 | readable settings | `.t3/settings.json`, userdata settings, schedule exports, userdata SQLite `scheduled_tasks` |
| Grok | `.grok/skills`, exported `skills/` or `workflows/` packages, or `skills.json` (`skills: [{name, files: {relative_path: text}}]`) | `.grok/exports` or `.config/grok/exports` (and `GROK_EXPORT_DIR`): `mcp.json`, `plugins.json`, `connections.json`, `routines.json`, `routines/`; `.grok/routines`; installed plugin metadata |
| System | none | read-only `crontab -l` (empty crontab is silent; a denied read, missing command or timeout is a skipped `<system:crontab>` row); `.config/systemd/user/*.timer` with its service; macOS LaunchAgents; injected Windows task XML (fixtures only) |

Project copies for Gemini and Hermes are read only for explicitly selected projects. Hermes YAML is read by a stdlib reader that accepts MCP block mappings, scalar lists and flow argument lists and ignores unrelated settings; anchors, tags and block scalars inside the MCP mapping are skipped.

| Platform | Application config | Native schedules |
| --- | --- | --- |
| Linux | `XDG_CONFIG_HOME` or `~/.config`; T3 also `~/.t3` | user cron, systemd timers, Hermes cron |
| macOS | `~/Library/Application Support` (includes Claude Desktop), OpenCode XDG config | cron, `~/Library/LaunchAgents/*.plist`, Hermes cron |
| Windows | `APPDATA` or `~/AppData/Roaming` (includes Claude Desktop); T3 also `LOCALAPPDATA` or `~/AppData/Local` | Hermes cron; Task Scheduler is not scanned |

Gemini and Hermes use `~/.gemini` and `~/.hermes` on every platform (`%USERPROFILE%` on Windows; Hermes honors `HERMES_HOME`). The `home` argument is authoritative for home dot directories; tests can pass a synthetic home and Windows directories through `env` on any OS.

Unavailable optional products are ignored. Malformed inputs are reported as skipped without aborting neighboring files. Environment-indirected stdio servers (`env_vars`, `${VAR}` in `env`) need manual migration. A server its agent has switched off is skipped as `disabled server`: `enabled = false` (Codex, Cursor, Claude, OpenCode), a name in a Claude project's `disabledMcpServers`, or a name in `disabledMcpjsonServers` of `.claude/settings.json` or `settings.local.json` beside a `.mcp.json`.

An HTTP server whose header comes from the environment (Codex `bearer_token_env_var` or `env_http_headers`, or a `${VAR}`, `$env{VAR}` or `{env:VAR}` header value) imports with that header as an unset `secret://scan-...` reference and a connection recorded as `needs-auth`. The scan never reads the variable; sign-in lists the server as needing auth and stores a typed token under the reference.

A server key is lowercased and non-alphanumeric runs become `-` (`node_repl` becomes `node-repl`). Keys that normalize to the same name go through the duplicate and conflict checks.

## Not scanned

Agent homes without an importer are listed by name and `~` path, never read. `scan` and `scan --json` (`not_scanned.agents`, each `{name, path}`, plus `not_scanned.next_step`) name each that exists under the selected home: `~/.grok` (only skills, routines, exports and plugins are read), `~/.antigravity`, `~/.opencodex`, `~/.pi`, `~/.copilot`, `~/.cagent`. The list is `NOT_SCANNED` in `checks/scanners/common.py`. Import their content by hand with `python3 hub.py import-skill <SKILL_DIR>` or `python3 hub.py import-rule <RULE_MD>`.

Bounded discovery skips `.git`, `.worktrees`, `.trash`, `node_modules` and symlinks. Each directory walk visits at most 5,000 entries; when the bound truncates discovery, `scan` and `scan --json` report an `incomplete_inventory` entry with its sanitized source and limit, and the human report says which folder to narrow. Importer limits apply: 1 MiB per file, at most 1,000 package files, no total-size cap (same as `import-skill`).

## Schedules

Routine imports always have one owner, one runner and `enabled=false`. Imported commands and prompts are inert text. The original scheduler is kept in the source label; runner labels are those allowed by [routines.md](routines.md). Complex schedules are skipped, not approximated.

- Cron: five-field expressions and hourly/daily/weekly/monthly/yearly aliases.
- Systemd: `OnCalendar` `daily` and `hourly` map to `fixed_time` and `interval`; `weekly`, `[Weekday[,..|..]] [*-*-*|*-*-D[,D]] H:M[:S] [Timezone]` and the `*`, `a,b,c`, `*/n`, `a/n` field forms map to a `cron` schedule carrying the timer's `timezone`. A timer without `OnCalendar` uses `OnUnitActiveSec` as an `interval`; `OnBootSec` is ignored. A weekday with a day list, a month or year, `*:*:*`, nonzero seconds (cron has minute resolution) or any other expression is skipped for manual conversion.
- Local time: timers without a zone, `daily`/`hourly`/`weekly` timers, crontab lines and LaunchAgent calendars record the host's IANA zone: `TZ` when it names one, else the `/etc/localtime` target under `zoneinfo/`, else `UTC`.
- LaunchAgents: intervals and simple hour-minute calendars.
- Codex: simple minute/hour intervals and daily/weekly RRULEs.
- Webhook schedules normalize to `{"type":"webhook"}`; delivery options and signature fields are discarded.

A routine id is the slugged title. A title over 63 characters is cut at the last hyphen that fits and gets `-` plus six hex characters of the full slug's hash, so ids stay whole-word, unique and stable. The full title (whitespace collapsed) becomes the `description`. If the title contains six or more consecutive digits or an account-number shape such as `202-555-0100`, the id is `routine-` plus a short hash and the description is `Imported disabled schedule`. The prompt is kept verbatim in `prompt.md`; an account-number shape in the title or prompt makes the item `needs_review` until you review it and pass `--approve-risky <id>`. A routine that fails validation is reported under its own name, not the file's.

A Grok Bot export keeps skills under `workflows/<name>/SKILL.md` and routines in `routines.json`: `{name, owner_bot, trigger: {type, ...}, prompt}`. `trigger` is an alias of `schedule`; `owner_bot` becomes the owner as a lowercase slug (`Agent Hub Ops` becomes `agent-hub-ops`; empty becomes `agentlocker`); Grok routines default to the `grok-bot` runner. The scanner does not unpack `skills.tgz`: extract it so `workflows/` sits beside `routines.json`, then set `GROK_EXPORT_DIR`. Until `skills/` or `workflows/` exists, scan reports one skipped `skills.tgz` row with that next step.

```sh
GROK_EXPORT_DIR=/path/to/extracted-export python3 hub.py scan --agent grok --all
```

T3 databases open with SQLite URI `mode=ro&immutable=1` and `query_only`. After schema discovery, a bounded query selects only task ID, title, prompt and schedule columns; webhook secrets, sessions, messages and credential tables are never selected. Immutable mode creates no WAL/SHM files, so only the checkpointed database is visible: items get `checkpointed-snapshot` when an uncheckpointed WAL exists, and recent WAL-only changes are not seen.

## Status and ownership

| Status | Meaning | Automatic import |
| --- | --- | --- |
| `new` | Eligible user content or metadata. An MCP server whose stored copy has a credential in its args is `new` with a reason and replaces that copy | Yes |
| `connect` | Existing live upstream or connection metadata | Yes |
| `needs_review` | Safety scan found a risk; see [safety](safety.md) | Only with per-item approval |
| `duplicate` | Same normalized content; `duplicate_of` names the first | No |
| `already_in_locker` | Matching content is already indexed | No |
| `conflict` | Same name, different content. `reason` names the other item or the locker copy; `next_step` gives `python3 hub.py import-rule <source> --name <new>` (or `import-skill`). If the locker copy came from the same agent's scan (`scan-<agent>` provenance) and the source changed since, `next_step` re-imports with the same `--name` and `--origin` to update it | No |
| `vendor` | System, vendor or plugin-cache content owned upstream | No |
| `skipped` | Malformed, unsafe, unsupported or agents-managed content | No |

Hashes normalize CRLF to LF. A skill hash covers the whole package, including supporting files and relative filenames. IDs are stable hashes of agent, kind, displayed source and content hash. Reports use `~` and project labels, never absolute home paths.

Before private-path validation, scan rewrites the selected home's absolute prefix to `~` in imported rule text, routine prompts (including systemd `ExecStart` lines) and MCP `command` and `args`; items whose only private finding was the home path import with `~/...`. Any other finding (tailnet or other IP, hostname, email, secret, another user's home path) still rejects the item. `import-rule` and the prompt beside `import-routine` run the same rewrite and validation.

Bundled skills, `.codex/skills/.system`, the Claude synced hosted library (`.claude/skills/synced/**`), Claude plugins and Codex plugin caches are `vendor`; their code is never copied.

Plugins are one record per `name@marketplace`: the agent's config (Codex `config.toml`, Claude `settings.json`), installed-plugin registry and plugin cache are merged, and `source` keeps the marketplace. `enabled` is true only when the agent's config enables it. Where sources disagree on `source`, `version` or `enabled` (a registry version outranks cached versions), each row is a `conflict` whose reason names the field and values. A plugin-cache item carries a `plugin` field with the owning plugin's name when readable. Connection items hold only provider, name, nonsecret endpoint and auth status. Both are indexed with hashes and provenance and listed by `hub_list_plugins` and `hub_list_connections`. This records inventory; it installs no plugins and starts no OAuth sessions.

## Upstream connections and secrets

An existing loopback Executor (port 4788), MCPHub (port 43871 or named) or Composio hosted endpoint gets `connect` and an `upstream` reference. Import registers an HTTP definition pointing at it, reusing Executor URL and Composio definition validation, the MCP importer and the renderer. Credentials stay with their connector. The scanner may read a discovered numeric loopback Executor's integration list; it never approves interactions or invokes tools. Fixtures with injected runners disable live Executor discovery. `connect` means "reuse the configured upstream", not that the endpoint is available or authenticated. Scan and import never change `gateway.backend`, start a server, sync agents or contact Composio or MCPHub. Remote MCP URLs with queries, fragments or userinfo are skipped.

`importers.parse_mcp` handles credentials per server. Env and header values become opaque `secret://` references; values are written to the private store only on explicit import. Existing references stay references and values are never resolved during scanning. Reports show only secret counts; no literal value appears in report JSON, CLI output, manifest, definitions or store name listings.

Only values under secret-looking keys (token, secret, key, password, authorization, credential) or shaped like known tokens count as credentials; ordinary values such as `1` or `prod` do not. A credential is matched as a whole word, never inside a longer word. An item that repeats one is skipped as `needs-manual` with a finding naming the server and key (never the value); an item whose public fields repeat it also drops its upstream. Only a name or source that itself repeats the value shows as `redacted-item` or `<system:redacted>`; `scan --safety` lists both. Generic detection cannot find arbitrary secrets in prose, so review user-authored content before confirming import.
