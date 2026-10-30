# Local setup wizard

Run `python3 hub.py serve` from the checkout and open the printed URL. The default is a free port; `--port 8765` picks one. Stop with Ctrl-C. The listener accepts only numeric IPv4 loopback; there is no remote host option. Assets use no frameworks, CDN or external requests. Python 3.10+ runs the wizard; Codex TOML import needs 3.11+ for `tomllib`.

The wizard has four physical screens and six navigation destinations: Welcome, Skills, Routines, MCP servers, Sign in and Connect. Skills, Routines and MCP servers focus groups in the same scan result without repeating imports. Mobile navigation opens from the current-step button. Buttons and summaries are at least 36px tall on desktop and 44px on mobile; checkbox labels provide the same target area. It follows the system color scheme and offers a persisted system/dark/light toggle. Shared tokens, routing and API helpers live in `ui/style.css`, `ui/app.js` and `ui/api.js`; each screen exports `init({api, state})` and `render({api, state})` from its own module. Back and Continue keep keyboard access, with native details, checkboxes, focus management and live status regions.

1. **Welcome.** Inventory rows for skills, routines, MCP servers, rules, and plugins and connections add up to the title: locker contents (imports plus the shipped examples, labelled as examples), scanner items on this computer that are new and ready to import, and servers needing a sign-in. The Health panel stays hidden until the first scan finishes, then reads "Everything checked out at <time>." or "Needs attention — run python3 hub.py check".
2. **Scan.** Results are expanded groups by type (Skills, Routines, MCP servers, Plugins, Connections and Rules); each row names its source agent and path. Groups can be collapsed, and item status stays visible on mobile. The title and sidebar count what was found on this computer, not what is in the locker.
   - **Import all** honors checkbox opt-outs, including in collapsed groups, and first confirms with per-type counts ("Import 2 skills, 1 routine and 1 MCP server?"); declining imports nothing. The button reads "Import 1 item" and, when nothing is ticked, a disabled "Nothing selected".
   - New and connect items start selected. Needs-review rows ([safety](safety.md)) show reasons, start unchecked, import only when ticked, and name any credentials ticking would move to the store; rows with an embedded secret cannot be imported.
   - Duplicate, already indexed, vendor/plugin-cache, conflict and skipped rows stay disabled with the concrete cause and one next step. Plugin-cache rows name their plugin; built-in vendor rows say "Built in to Codex".
   - **Scan again** refreshes. After import, rows say Imported, checkboxes clear, counts refresh, and the result message (success and rejection counts) scrolls into view and is announced politely.
   - A collapsed **Choose a folder or file instead** fallback offers manual previews (below).
3. **Sign in.** One table row per server, stacked on mobile, with a legend distinguishing verified, saved, needs sign-in, check failed and no credentials needed. The sidebar and Welcome count the same thing: servers still needing sign-in, plus stored servers whose copy has a credential in its args (shown as "Cannot be used" with the `python3 hub.py scan --import` fix and no Check again button).
   - A server whose OAuth discovery succeeds (or that already has an OAuth binding) offers "Sign in to <server id>" with token entry as secondary; any other HTTP server offers "Enter token". Server ids are shown as written.
   - Token entry is a form (Enter saves), visually masked, with autocomplete off and password-manager hints to ignore it (not guaranteed). Saving shows "Token saved on this computer", with no file path and no claim of provider verification.
   - OAuth uses a separate consent tab. On return, setup shows saved credentials or a sanitized denial, expiry or exchange failure with a timestamp and retry/token alternatives. Provider error descriptions are never echoed. These outcomes last for the setup session and preserve existing credentials. Credentials owned by live upstreams stay upstream.
4. **Connect.** Uses `connect.py` to detect Claude, Codex, Cursor, OpenCode, Claude Desktop, Gemini CLI, Windsurf, VS Code and Hermes, showing each exact destination and the locker entries to merge, with **Copy**. The UI never writes client settings: copy the snippets, or run `python3 hub.py connect --write --yes` in a terminal ([cli.md](cli.md#connect-clients)). Hosted bots use `connect --remote` ([remote.md](remote.md)). Displayed snippets do not prove a client loaded them; reload clients after configuring them.
   - With the gateway running, the panel says how many catalog servers it serves ("serves 1 of 9 servers", or none yet). Servers skipped for an unset secret are listed separately, not counted. Every other catalog server is shown as serving or down with the reason; a server that lists no tools gets a one-line next step.
   - With the gateway stopped and installed, **Start gateway** runs `python3 gateway.py prepare && python3 gateway.py start` on port 43871 and never picks another port, since configured agents would break. If the port is busy the screen names it and shows `python3 gateway.py start --port <port>`; a gateway running elsewhere shows that port. If it is not installed, the screen shows `python3 gateway.py install`. A separate Stop action is available only for this locker’s managed gateway; Executor remains user-managed. The read-only catalog entry stays available in both states. See [gateway.md](gateway.md).
   - Skill linking ([cli.md](cli.md#link-skills)) and Grok's hosted bootstrap stay separate, visible instructions.

The read-only `mcp_endpoint.py` exposes inventory; the gateway exposes authenticated upstream tools.

## Fixture testing

`python3 hub.py serve --scan-home <FIXTURE_HOME> --scan-platform linux --scan-project <PROJECT>` (repeat `--scan-project`) scans a fixture. Explicit fixture homes ignore host environment overrides and system subprocess discovery; the default scans the real home read-only. Import writes only the gitignored `.hub-store/catalog/` layer and the private store. Client configuration writes exist only in the CLI; tests must target fixture homes.

## API

All POSTs need the CSRF token and accept JSON up to 1 MB.

- `GET /api/status` is the one source for locker counts: `counts` per kind (imports plus examples) and `inventory` per kind with imported names and the number of shipped examples.
- `POST /api/scan` returns the scanner report plus an expiring opaque ticket.
- `POST /api/scan/import` accepts only `{ticket, ids}`. Paths come from server configuration. It rejects disabled or unknown IDs, calls `scanner.apply` (which re-scans) and consumes the ticket. `scanner.scan` takes an optional `root` to classify already-indexed content against the served locker.
- `POST /api/connect` returns nonsecret snippets and gateway connection info; it stages no merge plan or ticket. `POST /api/connect/write` is removed (404 with a valid CSRF token). Existing config contents never reach the browser.
- `POST /api/auth/bearer`, `/api/auth/secret`, `/api/auth/secret/remove`, `/api/auth/discover`, `/api/auth/oauth`, `/api/auth/refresh`, `/api/auth/check` and `GET /api/calls` are described below.

## Manual fallback inputs

Paths refer to the computer running the wizard. Supply a containing root and a relative filename; the server resolves and confines paths to that root, refusing symlink components and traversal. It reads only `.mcp.json`, `.claude.json`, `mcp.json` or `config.toml`.

- Claude global `mcpServers` and project entries are combined; duplicate names need manual reconciliation. Codex `mcp_servers` supports command/args/env and URL/http_headers; environment-variable indirection needs manual migration. Cursor uses `mcpServers`. Stdio and HTTP (including streamable-http/SSE labels) become the stdio and http definition formats. Imports contact no server; only the explicit OAuth action contacts the selected provider.
- Skill discovery checks the selected directory, its direct children, and direct children of `skills`, `.agents/skills`, `.claude/skills`, `.codex/skills` and `.cursor/skills` beneath it, at most 1,000 children per folder. Whole packages are validated before copying: private-content scan, 1 MiB per-file limit, 1,000-file limit, no aggregate size cap, no symlinks.
- Routines: paste or upload a JSON definition, or select a `.json` file under the root; a file import also reads the named sibling prompt. Paste and upload use a marked review placeholder for the prompt; supply the original before use. Routines are forced to `enabled=false`, validated with the CLI validators and recorded with one owner and runner. The wizard has no scheduler; see [routines.md](routines.md).
- All env and header values in MCP inputs become opaque `secret://` references, including under neutral keys. Values stay in memory during preview and are written only to the default `.hub-store/` on import (directories 700, files 600). Previews show masked status, never values. Commands and args must already be nonsecret and scanner-safe; URL authentication, queries and fragments are rejected. Existing items and destination files are refused, not replaced. A batch can partially import if a filesystem operation fails; inspect status before retrying. Source files are read-only.

## Security boundaries

The server enforces the exact `127.0.0.1:<port>` Host and same-origin Origin, rejects cross-site Fetch Metadata, and requires a random process-scoped CSRF token on every POST. Only `/api` routes, fixed static assets and the SVG/ICO favicon are served. Responses disable caching, MIME sniffing, framing and external script connections. Request bodies, input exceptions and access logs are never printed. Preview tickets expire after 15 minutes; at most 32 previews are kept, and a restart discards them.

The UI requires the default `.hub-store` location and refuses a custom store override. It writes no configuration in native agent directories. The local session trusts the OS user: another process with that user's privileges can read their files or control the wizard. Imports do not execute packages, and displayed credential presence does not verify provider acceptance.

## Authenticate HTTP MCP servers

The sign-in screen offers masked bearer-token or API-key entry and OAuth for HTTP servers needing attention. HTTP servers without declared credentials start as `needs-auth`; the wizard does not probe servers to display status, so public servers may simply be skipped. Stdio entries show credential presence, name each secret variable (never its value), and offer a masked **Set** or **Replace** field, plus **Remove** (after confirmation) next to Replace. A value moved from a client config reads `Copied from <client> config` until replaced.

`connected` for manual or imported credentials means stored, not provider-tested. OAuth `connected` means a token exchange succeeded and all declared header credentials are present. Expired access tokens show `expired`; use Refresh connection or Re-connect with OAuth. Status survives restarts. Refresh is explicit in the UI; gateway prepare/start refresh tokens within 60 seconds of expiry, and `python3 gateway.py refresh` covers longer sessions.

- `/api/auth/bearer` takes a selected declared secret header, or Authorization when none is declared. Bearer mode stores the complete scheme-prefixed header; API-key mode stores the exact value. Input clears on submission and no response echoes it. Existing referenced credentials change only through the explicit Save credential action.
- `/api/auth/secret` takes `{name, key, value}`. `key` must be a declared `env` or `headers` variable holding a `secret://` reference; the value is written to that reference in the private store and never returned.
- `/api/auth/secret/remove` takes `{name, key}` and deletes only that reference's value, returning the row to `Needs sign-in`. `python3 store.py delete <name>` does the same from a shell.
- `/api/auth/discover` with `{"name": "example-server"}` returns `{"oauth": true}` only if the discovery below succeeds, else `{"oauth": false}`, without registering a client or opening consent. The sign-in screen asks it for every HTTP server needing sign-in.

### OAuth

`/api/auth/oauth` does [RFC 9728](https://www.rfc-editor.org/rfc/rfc9728.html) resource discovery from the unauthenticated resource's `WWW-Authenticate` resource_metadata URL or path-specific well-known metadata, with a root well-known fallback on 404. It validates the resource identifier, fetches [RFC 8414](https://www.rfc-editor.org/rfc/rfc8414.html) authorization server metadata, checks the exact issuer and S256 support, and uses the first advertised authorization server. On HTTP 404 for authorization metadata it tries OpenID configuration with path insertion, then appending, with the same issuer and S256 checks; other errors fail closed. Signed metadata and non-S256 providers are unsupported.

- Endpoints must be HTTPS, except numeric IPv4 loopback HTTP for local providers. Requests time out at ten seconds; JSON responses are limited to 1 MB; redirects on discovery, registration and token requests are refused. Requests identify as `agentlocker/3`, since some providers reject the default Python user agent.
- A supplied pre-registered public client ID beats dynamic registration. Otherwise, when offered, dynamic registration requests an authorization-code/refresh client with the exact UI loopback callback. Token endpoint client auth may be `none`, `client_secret_basic` or `client_secret_post`. Confidential preconfigured clients are unsupported.
- OAuth opens a new tab with no opener and sends S256 PKCE, state, scope and resource. Scopes come from the resource challenge, else protected-resource metadata; absent or empty scopes omit the parameter.
- `/oauth/callback` accepts cross-site top-level navigation with the exact loopback Host. State is mandatory, random, single-use, process-local and expires after ten minutes; duplicate parameters, denial, expired state and code replay fail. Tokens exchange server-side and never reach the browser. The authorization URL includes the public client ID, state and PKCE challenge as request parameters; it contains no access or refresh tokens or client secrets. Callback pages are static and request logging is off. A restart invalidates pending consent; the consent tab may be closed once done.
- Each server has one current consent attempt. Starting a new consent successfully or saving a manual HTTP credential supersedes that server's earlier attempts, including exchanges already in flight. Superseded callbacks fail without changing stored credentials or the current setup outcome. Other servers' attempts remain independent. A current denial, expiry or failed exchange preserves existing credentials.

## Store binding contract for the gateway adapter

`ui_auth.Authentication` and `gateway.py` share `FileStore`. Let P be `mcp-` plus the first 24 lowercase hex characters of the SHA-256 of the UTF-8 manifest server name. These live in `.hub-store/secrets/` through `Store.put` (directories 700, atomic files 600):

| Store name | Value |
| --- | --- |
| `P-binding` | JSON: method (`bearer` or `oauth`), exact resource URL, headers mapping header names to store names; OAuth adds token_endpoint, auth_method, expires_at (Unix seconds) |
| `P-typed` | JSON list of store names of a stdio server's secrets set or replaced in the wizard, so `Copied from` stops showing |
| `P-header` | Complete Authorization header when the manifest has no matching secret reference |
| `P-access` | Raw OAuth access token |
| `P-refresh` | Raw refresh token, when issued |
| `P-client-id` | OAuth client identifier |
| `P-client-secret` | OAuth client secret, when issued |

For an existing Authorization reference, the complete OAuth header is written to that referenced store name and the binding points to it. Other declared secret header references stay in the binding and must also be present for connected status. Manual entry likewise writes the selected declared reference, or P-header. Definitions and manifest hashes stay unchanged.

Gateway prepare reads P-binding for indexed HTTP servers, checks the exact resource URL, adds store-backed header placeholders to private runtime settings, refreshes expiring OAuth with the same UI logic, and removes legacy MCPHub OAuth state for bound servers. See [gateway binding consumption](gateway.md#ui-credentials).

`/api/auth/refresh` reads P-refresh and the stored client credentials in process, sends the original resource, and replaces tokens through `Store.put`. A successful refresh without a refresh_token keeps the old one; rotation replaces it; a new consent without one removes the old. Failed exchanges keep existing credentials. A binding for a changed resource URL shows needs-auth and cannot refresh against the new resource. Writes are individually atomic, not transactional across files; retry authentication after a filesystem interruption. Only the gateway adapter should read these values; never return them through inventory or client APIs. Product account sessions stay outside this flow.

## Live checks

`POST /api/auth/check` with `{"server": "example-server"}` initializes an MCP session through the selected endpoint, sends `notifications/initialized` and lists every tools page. Stdio upstreams are reached through the gateway; the wizard never launches them. Displaying status runs no check. Provider errors use fixed messages, never provider text.

- `state: verified` needs a live list with at least one attributed tool.
- Zero matching tools reports `Checked, listed 0 tools` (`no_tools`): a running gateway whose server lists none, which a dead or still-starting upstream also produces.
- A stopped gateway reports `unreachable`; Sign in then offers **Start gateway and check** if installed, else the install command.
- MCPHub attributes tools by the longest manifest server name followed by its `nameSeparator` (default `-`); a real MCPHub 1.1.7 run lists `example-server-add` for the bundled `add` tool. The supervisor snapshots definition and binding fingerprints at launch, so changed configuration needs prepare and restart before checking. A stopped MCPHub cannot keep a Verified state.
- Executor uses `executor_connector.Session`; only explicitly prefixed server tools can be attributed. An Executor exposing only aggregate `execute`/`skills` tools reports zero attributed tools and cannot verify one server.
- Verification records in `.hub-store/verify.json` (mode 0600) invalidate when definitions, bindings, credential values or backend configuration change.

Each auth entry keeps its earlier fields and adds `state` (`needs_signin`, `saved`, `verified`, `no_credentials`, `check_failed`), `saved_at`, `checked_at`, `verified_at`, `tools`, `label`, `detail` and `error`. Setup-session OAuth results appear separately as `oauth_result` (`status`, `at`). A credential file's mtime supplies `saved_at`; stored credentials alone show `Saved` and `Not checked yet`. Timestamps are Unix seconds; the UI shows local times (`Saved 5:02 PM`).

## Design assets and safe actions

The local IBM Plex Sans 400/500/600 and Plex Mono 400 files match the supplied v4 design. Mono is reserved for paths and code; license and provenance are in `ui/fonts/LICENSES.md`. Static routes explicitly whitelist each font with `font/woff2`; arbitrary font paths and symlinked assets are refused. The supplied SVG favicon also has a raster ICO fallback.

Scan progress is indeterminate: the synchronous read-only scanner does not stream partial results or support cancellation. Unreadable files show sanitized reasons, rescan and manual file selection; setup never launches an editor or exposes configuration excerpts. Routine source enablement is display metadata; imports remain paused. Changed-source conflicts keep their existing reviewed update instructions and disabled checkbox.

Connect always includes the selected client’s read-only catalog entry, even while the gateway runs. `POST /api/gateway/stop` requires the setup session’s CSRF token, the same locker root as the gateway runtime, and a locker-managed backend. It uses the existing lifecycle lock, ordinary-path checks and supervisor identity guard; Executor must be stopped separately. Start and Stop require user actions. Setup never writes client configurations.

## Connect call log

`GET /api/calls?since=<unix-seconds>` returns:

- `calls`: the latest 20 from real agents;
- `older`: earlier real calls omitted ("N earlier calls not shown");
- `setup_checks`: how many of the wizard's own `setup check` requests matched, shown as one summary line so probes never push agent calls out of view;
- `sources`: always `stdio`, plus `gateway` after its first observed request (needs the updated supervisor);
- `first_call`: client name to earliest record, surviving polling and rotation. It marks the first request, including initialize, not necessarily the first tool call;
- `waiting`: no request observed since logging began. It is not proof an agent is disconnected.

Poll with the largest returned timestamp. Client names come from `initialize.clientInfo.name`, falling back to `unknown`. The wizard's probes tag requests `_meta.agentlocker = "setup check"`; the supervisor logs them as client `setup check`, which never becomes a `first_call`, so only a real agent clears `waiting`.

The catalog stdio endpoint writes one metadata-only JSON line per request: `ts`, `client`, `method`, optional `tool`, `duration_ms`, `source`. Arguments and responses are never recorded. The gateway supervisor extracts the same fields from MCPHub's request diagnostics; a request's client is bound through the session id announced after `initialize`, and requests with no bound session stay `unknown`. Gateway durations are `null`. A log proves receipt, not success. The log rotates at 1 MiB into one previous file, keeps its first-call index, is mode 0600 in the private store, redacts known store values and is never filled with synthetic calls. Raw MCPHub diagnostics stay in private gateway logs and are never returned by `/api/calls`.

## Verification

Run `python3 -m unittest discover -s tests -v` and `python3 hub.py check`. Synthetic inputs live in `tests/fixtures/ui/`; API tests use an ephemeral loopback server, and `tests/fake_oauth.py` is a stdlib OAuth provider, so no real account is needed. `tests/ui_scan_smoke.cjs` needs Node and skips without it.
