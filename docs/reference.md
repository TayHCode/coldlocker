# Import and catalog reference

Short reference for importing content and registering the catalog. Read the
[vision](../VISION.md) and the [format contract](formats.md) first. Full command
detail is in [cli.md](cli.md).

## Install

Python 3.10+ and Git are enough; there are no third-party Python dependencies.
Clone with `gh repo clone <OWNER>/agentlocker` or `git clone <HTTPS_URL> agentlocker`.
Without GitHub credentials, use the [bundle fallback](IMPORT-PROMPT.md#no-repo-access-on-this-machine).

## Import, list, remove, link

```sh
python3 hub.py import-skill <path> [--owner O] [--origin L] [--name N]
python3 hub.py import-rule <path.md> [--owner O] [--origin L] [--name N]
python3 hub.py deliver-rules --target <repo> --rule <name> [--dry-run]
python3 hub.py import-mcp <file.json>
python3 hub.py import-routine <file.json>
python3 hub.py list [--kind K] [--json]
python3 hub.py remove <kind> <name> [--owner O] --yes
python3 hub.py link --target <root> [--dry-run]
python3 hub.py link --home [--dry-run] [--yes]
python3 hub.py check
```

Replace placeholders and omit the brackets when running. Imports copy content into
the gitignored `.hub-store/catalog/` layer and record provenance in its own
`manifest.json`. Re-importing a changed source with the same owner and origin
updates it and keeps the previous copy in `.hub-store/history/`. Keep one
maintainer per item. See [Factory overlap](factory-overlap.md).

Input shapes: [examples/mcp-server.example.json](../examples/mcp-server.example.json),
[examples/routine.example.json](../examples/routine.example.json) and
[examples/routine-prompt.md](../examples/routine-prompt.md), which stays beside the
routine input. Importing an MCP definition does not install or start its package.

Definitions hold `secret://<name>` references, never values. Set up authentication
once through the private flow in [mcp-auth.md](mcp-auth.md). The store defaults to
`.hub-store/`; gitignored `config.json` can override `store_path`.

`link --target` and `link --home` create relative symlinks under `.claude/skills`
and `.agents/skills`. Preview with `--dry-run`; existing files are never replaced
and conflicts are reported.

## Register the catalog

Replace `<HUB_DIR>` with the checkout directory. These entries run
`python3 mcp_endpoint.py` over stdio and list hub content; they do not proxy
upstream tools. Merge only this entry into existing configuration, or let
`python3 hub.py connect` print it and `connect --write` merge it.

[Claude Code](https://code.claude.com/docs/en/mcp) (project `.mcp.json`) and
[Cursor](https://cursor.com/docs/mcp) (`.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "agentlocker": {
      "command": "python3",
      "args": ["<HUB_DIR>/mcp_endpoint.py"]
    }
  }
}
```

[Codex](https://developers.openai.com/codex/mcp/) `config.toml`:

```toml
[mcp_servers.agentlocker]
command = "python3"
args = ["<HUB_DIR>/mcp_endpoint.py"]
```

[OpenCode](https://opencode.ai/docs/mcp-servers/) `opencode.json`:

```json
{
  "mcp": {
    "agentlocker": {
      "type": "local",
      "command": ["python3", "<HUB_DIR>/mcp_endpoint.py"],
      "enabled": true
    }
  }
}
```

Reload the client, approve the connection and inspect the `hub_list_*` tools.
Hosted bots use the [remote catalog](remote.md); Grok Bot also has a
[bootstrap](grok-bot-bootstrap.md). Further client paths and snippets are in
[clients.md](clients.md).

## Scope

Imported routines stay `enabled=false`; `routine enable` is a separate explicit
step ([routines](routines.md)). The catalog does not proxy tools or implement
OAuth for local use; the [gateway](gateway.md) and [wizard](ui.md) supply
authenticated tool execution, and the [remote catalog](remote.md) supplies OAuth
for hosted bots. Multi-tenancy is not built ([hosted catalog](hosted-later.md)).

Verify with `python3 hub.py check` and `python3 -m unittest discover -s tests -v`.
A successful content check does not prove client discovery or auth reuse; verify
in each installed client.

MIT licensed; see [LICENSE](../LICENSE). Third-party projects are referenced, not
vendored; see [related projects](related.md).
