# Gateway choice and renderer

The default gateway backend is **samanhappy/mcphub** (Apache-2.0) in file-storage mode, wrapped by a thin adapter. Setup and runtime are in [gateway.md](gateway.md) and [ui.md](ui.md); Executor can be selected as an alternative backend ([executor-connector.md](executor-connector.md)).

## Why MCPHub

- A headless mode (`DISABLE_WEB=true`) keeps the backend, API and MCP endpoints without the dashboard.
- One aggregate MCP endpoint fronts every configured server.
- Plain JSON settings (`data/mcp_settings.json`, top-level `mcpServers`) fit the manifest fragments without a database or container catalog.
- `${VAR_NAME}` substitution in `env` and `headers`, `stdio` and `streamable-http` server types, and an OAuth client that persists credentials in its settings.

Docker MCP Gateway (MIT) is a credible alternative that works independently of Docker Desktop. Its README does not document a standalone `mcp_settings.json` input or generic `${VAR_NAME}` substitution, so none is assumed. MetaMCP adds application and database state. tbxark/mcp-proxy serves per-server routes, so one HTTP listener does not mean one aggregate tool catalog. Executor is a broader execution platform with its own credentials. These are scope decisions from reading upstream documentation, not runtime comparisons.

Upstream references: [MCPHub README](https://github.com/samanhappy/mcphub/blob/main/README.md), [MCPHub settings](https://docs.mcphub.app/configuration/mcp-settings.md), [MCPHub OAuth](https://docs.mcphub.app/features/oauth.md), [Docker MCP Gateway](https://github.com/docker/mcp-gateway/blob/main/README.md). Upstream pages can change.

## Renderer contract

`python3 gateway_render.py` reads only manifest-indexed `mcp/servers/<name>.json` files and writes MCPHub settings JSON to stdout. It does not discover unindexed fragments and never executes a server command.

- `transport: stdio` becomes `type: stdio`; `transport: http` becomes `type: streamable-http`.
- Commands, argument arrays, env and headers are kept, with secret references substituted. Descriptions and inventory metadata are not gateway fields.
- `secret://<name>` becomes `${AGENTLOCKER_SECRET_<HEX>}`, where HEX is the uppercase hexadecimal UTF-8 encoding of the exact store name. The mapping is reversible and avoids collisions between dots, underscores and hyphens, for both env and headers.
- Nonsecret literals are unchanged. Literal gateway interpolation is rejected so a fragment cannot read undeclared process environment variables, and sensitive env or header keys must use references even if the inline value looks harmless.
- URLs with embedded credentials, queries or fragments are rejected; put authentication in referenced headers. Errors never include input data.

`python3 gateway_render.py --out .scratch/mcp_settings.json` writes atomically when the parent directory exists. Output to a tracked file, a path overlapping one, a symlink or Git metadata is refused, except beneath `.scratch/` or `.hub-store/`; Git inspection fails closed. For deployment use the protected `.hub-store/` location. Rendering neither resolves credentials nor opens the store; see [mcp-auth.md](mcp-auth.md).
