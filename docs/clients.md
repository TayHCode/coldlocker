# Client connections

Clients reach agentlocker three ways: the local stdio catalog
(`mcp_endpoint.py`), the local gateway ([gateway.md](gateway.md)), or the remote
read-only catalog ([remote.md](remote.md)). Upstream credentials stay in the hub
Store; product and model logins stay separate.

## Connect detected agents

```sh
python3 hub.py connect            # print the exact native entries
python3 hub.py connect --write    # confirm interactively or pass --yes
```

`--write` merges only `agentlocker` and, when running, `agentlocker-gateway`.
Other accounts, servers and settings stay. It starts no gateway or upstream
server, and no entry contains credentials. Executor header references are in
[executor-connector.md](executor-connector.md). Reload the client afterwards.

The configured command is a working `python3` (3.10+) on PATH, written as an
absolute path so GUI clients need no PATH. On Windows it is always the running
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
`--home DIR` isolates discovery from those overrides and from `APPDATA`;
`--platform linux|darwin|win32` selects the path table; `--agent claude codex ...`
filters. Cursor and OpenCode count as detected only when their config file
exists; otherwise connect prints the entry and the path to create, and `--write`
never creates it. No detected agents is a successful no-op.

Entry shapes: Claude `mcpServers` (also valid in a project `.mcp.json`, but
writes use `.claude.json`); Codex `[mcp_servers.agentlocker]`; Cursor
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
  rerun`, exit 1. JSONC-only OpenCode configs are skipped with a manual-merge
  note; if both `opencode.json` and `opencode.jsonc` exist, both are refused.
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
- **VS Code** writes the default profile's `servers` format
  ([reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)).
  Copilot Global uses a separate format and location.
- **Hermes** supports stdio and HTTP ([docs](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/mcp.md)).
  agentlocker prints the YAML only; write mode refuses it because a
  stdlib-only tool cannot safely preserve arbitrary YAML.
- **Windsurf**: the former [Cascade MCP page](https://docs.windsurf.com/windsurf/cascade/mcp)
  now redirects to a different application and the home path is unverified, so
  connect prints the snippet and never writes it.
- **Cursor** and **OpenCode** follow [Cursor MCP](https://cursor.com/docs/context/mcp)
  and [OpenCode MCP](https://opencode.ai/docs/mcp-servers/); Claude scopes are in
  the [Claude MCP guide](https://code.claude.com/docs/en/mcp#understanding-mcp-server-scopes).
- **Grok Bot** gets only the [bootstrap instructions](grok-bot-bootstrap.md) in
  local mode, never a hosted write.

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
`CUSTOM_AGENTLOCKER` read skills, rules and routine definitions through the
read-only tools. Registering requires `--catalog --write --yes` and a private
Store project-key reference (`--api-key-ref secret://...`). OAuth consent, sync
and session configuration remain owner actions. See [composio.md](composio.md).

## Try without touching your config

These load the stdio catalog for one run and write nothing to the client's user
configuration. From the checkout:

```sh
HUB_DIR="$PWD"; TRY="$HUB_DIR/.scratch/try"; mkdir -p "$TRY"
printf '{"mcpServers":{"agentlocker":{"command":"python3","args":["%s/mcp_endpoint.py"]}}}' \
  "$HUB_DIR" > "$TRY/claude.json"
claude --mcp-config "$TRY/claude.json" --strict-mcp-config -p \
  'Call hub_list_skills and report the names.' < /dev/null
codex exec --ignore-user-config --ephemeral --skip-git-repo-check -s read-only \
  -c 'mcp_servers.agentlocker.command="python3"' \
  -c "mcp_servers.agentlocker.args=[\"$HUB_DIR/mcp_endpoint.py\"]" \
  'Call hub_list_skills and report the names.'
```

Model-driven runs need the client signed in. Without a login they stop before
any tool call (Codex 401, OpenCode free models 403 from headless runs, Claude
Code "Not logged in"); none of these is an agentlocker fault. The login-free
check is the stdio handshake:

```sh
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | python3 "$HUB_DIR/mcp_endpoint.py"
```

The second reply lists the `hub_list_*` tools. `claude mcp list` and `codex mcp
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
  "mcp": { "agentlocker": { "type": "remote", "url": "<GATEWAY_URL>" } },
  "permission": { "*": "deny", "agentlocker_*": "allow" }
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
{ "mcpServers": { "agentlocker": { "url": "<GATEWAY_URL>" } } }
```

Then `cursor-agent mcp enable agentlocker`, `cursor-agent mcp list-tools
agentlocker`, and `cursor-agent --trust -p --output-format json '<prompt>'`.
Enabling approves only this entry; re-enable after changing the URL. `status`
reports **Not logged in** until `cursor-agent login` runs in the disposable
environment or an authorized `CURSOR_API_KEY` is supplied privately.
