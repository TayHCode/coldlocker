# Client connections

Clients reach Coldlocker three ways: the local stdio catalog
(`mcp_endpoint.py`), the local gateway ([gateway.md](gateway.md)), or the remote
read-only catalog ([remote.md](remote.md)). Upstream credentials stay in the hub
Store; product and model logins stay separate.

## Connect detected agents

```sh
python3 hub.py connect            # print the exact native entries
python3 hub.py connect --write    # confirm interactively or pass --yes
```

New registrations use `coldlocker` and, when running, `coldlocker-gateway`.
Existing private registrations keep their aliases so permission and approval
rules continue to match, including policies stored outside the configuration
file. Reconnect updates those registrations without adding duplicates. Existing
duplicate or conflicting aliases require manual reconciliation.
To rename existing Claude and Codex registrations, use
`python3 hub.py connect --agent claude codex --write --migrate-names`.
The rename uses the same backups and preserves unrelated settings. It refuses
legacy names referenced by policy in the client config or home Claude settings.
Review project-specific tool permissions before renaming. Hooks are unchanged.
Other accounts, servers and settings stay. It starts no gateway or upstream
server, and no entry contains credentials. Executor header references are in
[executor-connector.md](executor-connector.md). Reload the client afterwards.

Once the gateway serves an imported server, a client that also keeps a direct
entry for it sees every tool twice. `python3 hub.py connect --write --retire-direct`
lists each user-level direct entry whose name matches a server the running
gateway lists tools for, and after confirmation removes them in the same
backed-up write. The printed `Undo:` command restores the previous file.
Project-level entries are untouched. A server whose name is another catalog
server's name plus a `-` suffix is never retired, because gateway tool names
can't tell the two apart. When the gateway requires a token,
`COLDLOCKER_GATEWAY_TOKEN` must hold that token. The Executor backend is not
supported.

## Imported MCP servers

On Linux and macOS, imported MCP servers reach each client that `connect` writes
through the gateway. While it is stopped, `connect` names the imported servers and
prints the command that installs, prepares and starts it; rerun `connect`
afterwards to add `coldlocker-gateway`. To list each tool once in the client you
imported from, run `connect --write --retire-direct --agent <that client>`: it
matches entries by name only, so limit it to that client and check the listed
entries before you confirm. Claude Desktop gets no gateway entry; add imported
servers there by hand.

Windows has no gateway. There `connect --write` adds each imported stdio server
to every client it writes under its catalog name as `python hub.py mcp-run <name>`;
Hermes and Windsurf, which it does not write, need them by hand. That
command resolves the server's `secret://` references from the private store at
launch, so no client config holds a credential. A client that already has an
entry with that name keeps its own, so the client you imported from does not
list the server twice. HTTP servers are listed as not written; add each in the
client by hand. A missing secret makes `mcp-run` exit with the
`secret://` name to add ([MCP auth](mcp-auth.md)).

The configured command is the `python3` on PATH, or the Python running connect
when that one is missing or too old, written as an absolute path so GUI clients
need no PATH. On Windows it is always the running
interpreter, since `python3` can be a Store stub.

User-level config paths, relative to the selected home:

| Agent | Linux | macOS | Windows |
| --- | --- | --- | --- |
| Claude Code | `.claude.json` | same | same |
| Codex | `.codex/config.toml` | same | same |
| Cursor | `.cursor/mcp.json` | same | same |
| OpenCode | `.config/opencode/opencode.json` | same | same |
| Claude Desktop | `.config/Claude/claude_desktop_config.json` | `Library/Application Support/Claude/claude_desktop_config.json` | `AppData/Roaming/Claude/claude_desktop_config.json` |
| Gemini CLI | `.gemini/settings.json` | same | same |
| Windsurf | `.codeium/windsurf/mcp_config.json` | same | same |
| VS Code | `.config/Code/User/mcp.json` | `Library/Application Support/Code/User/mcp.json` | `AppData/Roaming/Code/User/mcp.json` |
| Hermes Agent | `.hermes/config.yaml` | same | same |

Detection checks config files and directories and never executes clients.
`CODEX_HOME`, `CLAUDE_CONFIG_DIR` and `XDG_CONFIG_HOME` select custom locations.
When `codex` on PATH is the snap launcher and `snap/codex/current` exists, Codex
reads that snap revision's `config.toml`, so connect and scan use it instead of
`.codex`.
`--home DIR` isolates discovery from those overrides and from `APPDATA`;
`--platform linux|darwin|win32` selects the path table; `--agent claude codex ...`
filters. When `~/.cursor` or the Cursor app folder exists, `--write` creates
`~/.cursor/mcp.json`. OpenCode counts as detected only when `opencode.json` or
`opencode.jsonc` exists; otherwise connect prints the entry and the path to
create, and `--write` never creates it. No detected agents is a successful no-op.

Entry shapes: Claude `mcpServers` (also valid in a project `.mcp.json`, but
writes use `.claude.json`); Codex `[mcp_servers.coldlocker]`; Cursor
`mcpServers`; OpenCode `mcp` with local command arrays and remote URLs; VS Code
`servers` with `type: "stdio"` or `"http"`; Gemini `command`/`args` for stdio and
`httpUrl` for Streamable HTTP.

Imported skills are readable from any connected agent through `hub_list_skills`
and `hub_get_item`. Connect creates no skill links; use `hub.py link --target
<project>` for a project or `hub.py link --home` for `~/.claude/skills` and
`~/.agents/skills` (see [cli.md](cli.md)).

### Write safety

- An existing file gets a timestamped `.bak` sibling and an atomic replace; a new
  file gets no backup. Connect prints `Backup: <path>` and an `Undo:` command.
  Backups may contain credentials; keep them private.
- Destinations are rechecked after preparing the replacement and after the
  backup; a change aborts the write. Symlinked destinations or parents and
  non-ordinary files are refused.
- Checkout paths containing `.scratch` or `.worktrees` produce a warning,
  because deleting the checkout breaks every written entry.
- An equal connection prints `already connected` and makes no backup.
- JSON edits are spliced in place, so untouched entries keep their layout. TOML
  keeps unrelated text, comments and CRLF endings and needs Python 3.11+; without
  it connect skips Codex, prints the block to add by hand and exits 1.
- A config that is invalid TOML/JSON (including duplicate keys) skips only that
  client: `<client>: <~/path> is not valid <TOML|JSON> (<message>); fix it and
  rerun`, exit 1. A lone `opencode.jsonc` that is plain JSON (OpenCode's own
  first-run file) is merged in place; one with comments or trailing commas is
  skipped with a manual-merge note. If both `opencode.json` and
  `opencode.jsonc` exist, both are refused.
- All selected changes are parsed before any write. If one write fails, connect
  prints `<client>: could not write <~/path> (<reason>)`, writes the rest and
  exits 1.

### Per-client notes

- **Claude Desktop** takes `mcpServers` stdio entries only; no HTTP entry is
  written. macOS and Windows paths follow the official
  [local MCP quickstart](https://modelcontextprotocol.io/docs/develop/connect-local-servers);
  the Linux path is a convention. [Remote connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
  run from Anthropic's cloud and cannot reach a loopback gateway.
- **Gemini CLI**: [MCP configuration](https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md).
  Gemini disables every MCP server, user-level ones included, in a folder you
  have not trusted; `gemini mcp list` then shows `coldlocker ... - Disabled`.
  Trust the folder when Gemini asks, or add it to `~/.gemini/trustedFolders.json`.
- **VS Code** writes the default profile's `servers` format
  ([reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)).
  Copilot Global uses a separate format and location.
- **Hermes** supports stdio and HTTP ([docs](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/mcp.md)).
  Coldlocker prints the YAML only; write mode refuses it because a
  stdlib-only tool cannot safely preserve arbitrary YAML.
- **Windsurf**: the former [Cascade MCP page](https://docs.windsurf.com/windsurf/cascade/mcp)
  now redirects to a different application and the home path is unverified, so
  connect prints the snippet and never writes it.
- **Cursor** and **OpenCode** follow [Cursor MCP](https://cursor.com/docs/context/mcp)
  and [OpenCode MCP](https://opencode.ai/docs/mcp-servers/); Claude scopes are in
  the [Claude MCP guide](https://code.claude.com/docs/en/mcp#understanding-mcp-server-scopes).
  The Cursor CLI lists a new entry as `not loaded (needs approval)` until
  `cursor-agent mcp enable coldlocker` approves it.
- **Hosted Bot** gets only the [bootstrap instructions](hosted-bot-bootstrap.md) in
  local mode, never a hosted write.

### Fresh-VM acceptance

On 2026-11-09 a throwaway Coldbox VM (Ubuntu 26.04, Python 3.14) installed each
client into an empty HOME with no login, ran each client once, ran
`hub.py connect --write --yes`, and asked each client for its servers. Claude
Code and Codex are recorded in [release.md](release.md#recorded-local-client-acceptance).
Maintainers rerun it from the private checkout with
`vmrun --network -- env CONNECT_ACCEPTANCE_DISPOSABLE=1 bash tools/connect_acceptance.sh`.

| Client | Version | Written by connect | Client check | Result |
| --- | --- | --- | --- | --- |
| Gemini CLI | 0.67.0 | `~/.gemini/settings.json` | After trusting the folder, `gemini mcp list`: `coldlocker ... (stdio) - Connected` | PASS |
| OpenCode | 1.19.5 | its first-run `opencode.jsonc` | `opencode mcp list`: `coldlocker connected` | PASS |
| Cursor CLI | 2026.11.05-86047df | `~/.cursor/mcp.json` | `cursor-agent mcp list`: `needs approval`; after `mcp enable`, `mcp list-tools coldlocker` lists 7 tools | PASS |
| VS Code | 1.145.2 | `~/.config/Code/User/mcp.json` | Config read plus stdio handshake (7 tools); `code --add-mcp` edits the same file and keeps the entry. The VM has no display to watch VS Code start the server | INCOMPLETE |
| Claude Desktop | no Linux build | `~/.config/Claude/claude_desktop_config.json` | Config read plus stdio handshake (7 tools). There is no Linux app, so the script creates its config folder in place of a first launch; macOS and Windows are untested | INCOMPLETE |

Before this run, a fresh Cursor CLI install got no entry (connect required an
existing `mcp.json`), and OpenCode's own first-run `opencode.jsonc` got only a
manual-merge note.

## Hosted and remote connections

`python3 hub.py connect --remote` prints remote catalog setup without reading
client configuration or contacting any service; `--write` is rejected. Filter
with `--agent grok dots muse mausbot claude_ai claude_desktop chatgpt codex`
(`claude`, `muse_code` and `t3` also work). Per-platform support, sources and a
verification prompt are in [remote.md](remote.md#connect-a-client). OAuth paths
for Dots, hosted Muse, MausBot and T3 are unconfirmed.

## Composio-connected agents

`python3 hub.py connect-composio --catalog` prints the remote catalog's OAuth
Custom MCP registration and connection steps. Composio sessions configured with
`CUSTOM_COLDLOCKER` read skills, rules and routine definitions through the
read-only tools. Registering requires `--catalog --write --yes` and a private
Store project-key reference (`--api-key-ref secret://...`). OAuth consent, sync
and session configuration remain owner actions. See [composio.md](composio.md).

## Try without touching your config

These load the stdio catalog for one run and write nothing to the client's user
configuration. From the checkout:

```sh
HUB_DIR="$PWD"; TRY="$HUB_DIR/.scratch/try"; mkdir -p "$TRY"
printf '{"mcpServers":{"coldlocker":{"command":"python3","args":["%s/mcp_endpoint.py"]}}}' \
  "$HUB_DIR" > "$TRY/claude.json"
claude --mcp-config "$TRY/claude.json" --strict-mcp-config -p \
  'Call hub_list_skills and report the names.' < /dev/null
codex exec --ignore-user-config --ephemeral --skip-git-repo-check -s read-only \
  -c 'mcp_servers.coldlocker.command="python3"' \
  -c "mcp_servers.coldlocker.args=[\"$HUB_DIR/mcp_endpoint.py\"]" \
  'Call hub_list_skills and report the names.'
```

Model-driven runs need the client signed in. Without a login they stop before
any tool call (Codex 401, OpenCode free models 403 from headless runs, Claude
Code "Not logged in"); none of these is an Coldlocker fault. The login-free
check is the stdio handshake. Run it from the checkout and replace `<SKILL_NAME>`
with a name from `python3 hub.py list`:

```sh
HUB_DIR="$PWD"
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"hub_list_skills","arguments":{}}}' \
  '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"hub_get_item","arguments":{"kind":"skill","name":"<SKILL_NAME>"}}}' | python3 "$HUB_DIR/mcp_endpoint.py"
```

Each `tools/call` reply carries the JSON result as text in `result.content[0].text`;
use `hub_list_routines` and kind `"routine"` for routines.

The second reply lists the read-only `hub_list_*` and `hub_get_item` tools.
`claude mcp list` and `codex mcp
list` show the configured server only (Codex shows `Auth Unsupported`; Claude
needs user scope, and project `.mcp.json` servers show `Pending approval`).

## Through the gateway

Put the gateway URL from [gateway.md](gateway.md) where `<GATEWAY_URL>` appears.
Run experiments in an ignored scratch directory with a disposable HOME and XDG
variables (`env -i PATH=... HOME=... XDG_CONFIG_HOME=... XDG_DATA_HOME=...
XDG_STATE_HOME=... XDG_CACHE_HOME=...`) so no real login or config is touched.
Never copy auth files or put provider credentials in argv.

**OpenCode** `opencode.json` in the project:

```json
{
  "mcp": { "coldlocker": { "type": "remote", "url": "<GATEWAY_URL>" } },
  "permission": { "*": "deny", "coldlocker_*": "allow" }
}
```

Set `OPENCODE_CONFIG` to that file and `OPENCODE_DISABLE_AUTOUPDATE=true`, then
use `opencode mcp list` (connection status only; there is no tool-listing
command). A noninteractive `opencode run --pure` needs a usable provider login
made in the disposable environment.

**Cursor CLI**: use `cursor-agent`, not `agent` (which may be Grok's CLI; check
`agent --version`). Create `.cursor/mcp.json` at the project's own Git root
(run `git init` first; a nested folder without its own root did not load project
MCP configuration):

```json
{ "mcpServers": { "coldlocker": { "url": "<GATEWAY_URL>" } } }
```

Then `cursor-agent mcp enable coldlocker`, `cursor-agent mcp list-tools
coldlocker`, and `cursor-agent --trust -p --output-format json '<prompt>'`.
Enabling approves only this entry; re-enable after changing the URL. `status`
reports **Not logged in** until `cursor-agent login` runs in the disposable
environment or an authorized `CURSOR_API_KEY` is supplied privately.
