# Local gateway (MCPHub adapter)

`gateway.py` runs the Apache-2.0 npm package `@samanhappy/mcphub`, pinned to
**1.1.7**, so every client connects to one local endpoint that fronts all imported
MCP servers. It needs Python 3.10+ and Node. No Docker and no system service are
used. The installed package lives under ignored `.scratch/`.

```sh
python3 gateway.py install     # one-time, needs npm registry access
python3 gateway.py prepare     # render runtime settings from the manifest
python3 gateway.py start       # prints the endpoint and `servers: N/M connected`
python3 gateway.py status
python3 gateway.py url         # prints the endpoint while running
python3 gateway.py sync        # copy OAuth state into the Store
python3 gateway.py refresh     # refresh expiring UI credentials
python3 gateway.py stop
```

## Requirements

- Linux or macOS. `start` also needs `ss` (Linux) or `lsof` (macOS). On other
  systems, such as Windows, `install` and `start` stop with a pointer to the stdio
  route (`python3 hub.py connect` without the gateway).
- Node 20 or newer and npm; **Node 24 is recommended**. On the Node 20 line,
  Node 20.19 or newer is needed because several locked packages declare `^20.19.0`.
- On Node 20, `npm ci` may print `EBADENGINE` for `pgvector@0.3.0` (`>=22`). The
  warning is harmless: MCPHub lists `pgvector` but never imports it, and the gateway
  uses no database. An older Node 20.19.2 run with earlier pins passed. That result is historical
  and does not cover the current pins; the current pins are verified on Node 24.

## Install and upgrade

`install` creates private directories (umask 077), uses a local npm cache, stages
the tracked `gateway/package.json` and `gateway/package-lock.json` in a new ignored
`.scratch/mcphub-install-<opaque>/`, and runs `npm ci --ignore-scripts`. After the
versions below are checked, the staging directory becomes `.scratch/mcphub-runtime/`.
An installation that already matches is left alone.

Pinned versions (tracked overrides in `gateway/package.json`):

- MCPHub **1.1.7**.
- `@modelcontextprotocol/client` **2.3.0**, which includes the fixes for
  [GHSA-6prh-2h8m-c8cw](https://github.com/advisories/GHSA-6prh-2h8m-c8cw)
  (cross-origin redirects resent custom headers and bodies, including OAuth token
  requests, in client 2.0.0 through 2.2.0) and GHSA-6qxp-vccf-f47h. The client now
  defaults to same-origin redirects; configure the final upstream URL if a server
  redirects elsewhere. No permissive override is enabled.
- **Hono 4.13.11**, which fixes
  [GHSA-5r4p-p66f-jhc7](https://github.com/advisories/GHSA-5r4p-p66f-jhc7)
  (double decoding that could bypass middleware on static-file paths).

To upgrade an installation with an older client (2.0.x to 2.2.x) or Hono 4.12.4
through 4.13.10, stop the gateway, then run `python3 gateway.py install`. The adapter
refuses to launch the vulnerable runtime and prints this command. The previous
directory, including unfamiliar files, is preserved as
`.scratch/mcphub-runtime.backup-<opaque>/` before the validated replacement is
switched in; Store, gateway settings and client configs are untouched. A failed
download or validation leaves the original in place, and a failed switch restores it.
Other MCPHub versions, unknown client or Hono versions, incomplete runtimes and
symlinked paths are refused for manual review. Nothing deletes preserved directories.

Install and launch share a lock under `.scratch/`. Install refuses while the gateway
runs and launch refuses during an install. A busy port is left untouched; retry with
`python3 gateway.py start --port NUMBER`. The setup UI's **Start gateway** always uses
the default port and, when busy, names it and prints that command.

For offline use, copy a complete trusted `.scratch/mcphub-runtime/` into the same
relative location. All three pinned versions, including nested copies inside MCPHub,
are checked before launch. Never copy Store or product credentials with it.

## Prepare and start

`prepare` renders the indexed manifest through `gateway_render.render` and never
executes server definitions; `start` does. Review definitions before starting.
`--source DIRECTORY` selects another manifest root for `prepare`. The tracked
`example-server` runs `examples/example-server.py` and exposes `add` with no
credentials, packages or network.

In stdio arguments, `${AGENTLOCKER_ROOT}/relative/path` expands to an absolute path
under the manifest root at render time (traversal and symlinks refused; only valid at
the start of an argument). Tracked definitions stay portable across clones.

When scan imports an absolute stdio command or argument under the home directory,
the catalog keeps the portable `~/...` spelling. The original path stays in the
private Store, indexed by `.hub-store/source-path-bindings.json`, and `start`
resolves it into the protected `.hub-store/gateway/mcp_settings.json` (mode 600).
Listing and ordinary rendering keep it opaque.

The default port is 43871; `--port` selects another unprivileged port. The aggregate
streamable HTTP endpoint is `/mcp` on IPv4 loopback. Verify with the bundled client:

```sh
LOOPBACK=$(python3 -c "print('.'.join(('127','0','0','1')))")
python3 examples/test-servers/http_client.py "http://$LOOPBACK:43871/mcp"
```

The client initializes a session, lists tools and calls `add` with 2 and 3,
expecting 5.

`start` launches a detached supervisor that pipes child output through
`Store.redact` (including individual lines of multiline values) and keeps only
redacted diagnostics. Startup requires a healthy endpoint and `ss`/`lsof`
confirmation that the listener belongs to its Node child and binds only to
loopback. A generated Node bootstrap forces TCP listeners to loopback; MCPHub has no
host setting in this version, so a version change requires reviewing the bootstrap.

## Defensive configuration

These settings reduce exposure of the local endpoint.

- **Host and Origin check.** The bootstrap checks every request on every route (MCP,
  SSE, REST, health, upgrades). `Host` must be `127.0.0.1:<port>`,
  `localhost:<port>` or `[::1]:<port>`, and a present `Origin` must be an `http://`
  origin with one of those authorities; anything else gets 403. This blocks
  DNS-rebinding and cross-site browser requests. Clients that send no `Origin` are
  unaffected.
- **Web UI disabled.** The MCPHub dashboard is off by default (`DISABLE_WEB=true`).
  `start --dashboard` serves it and the choice survives `refresh`. The REST API
  behind the admin sign-in stays available because setup uses it. Admin and JWT
  secrets are generated into Store and passed only in the child environment.
- **Bearer auth on `/mcp` (opt-in).** `python3 gateway.py start --require-token`
  requires a bearer token. `python3 gateway.py token --env-file <path>` writes it to
  a new mode-600 file (never overwrites). The token is generated into Store as
  `gateway-token`, installed as the enabled `agentlocker-gateway` key in the
  protected settings, and never printed. Load the env file into the environment the
  client starts from and restart the client. Generated client snippets and
  `hub.py connect` entries reference only the variable
  `AGENTLOCKER_GATEWAY_TOKEN` in each client's own syntax. Re-run `hub.py connect`
  after starting with `--require-token`; the wizard's checks send the header
  themselves. Starting again without the flag removes the key.
- **Limits.** A GUI client launched without the variable cannot connect while the
  token is required. Other processes under the same user can still read Store, so
  this defends against other users, other hosts and the browser, not the same
  account. MCPHub's OAuth authorization server stays off. Do not forward the port.

## Runtime layout and environment

`.hub-store/gateway/` and its `home/` are mode 700; files are mode 600:

- `mcp_settings.json`: sensitive runtime settings, atomically prepared.
- `bootstrap.mjs`: loopback bootstrap.
- `pid.json`: supervisor and Node PIDs, start identities, port, `dashboard` and
  `require_token` choices.
- `lifecycle.lock`, `supervisor.lock`: serialize lifecycle commands and prevent
  duplicate workers.
- `gateway.log`: redacted combined output.
- `home/`: disposable runtime home.

The child environment is `PORT`, `DISABLE_WEB`, `NODE_ENV=production`,
`MCPHUB_SETTING_PATH`, `AGENTLOCKER_BIND`, `AGENTLOCKER_ENTRY`, `HOME=<runtime>/home`,
`ADMIN_PASSWORD` and `JWT_SECRET` (from Store). It also keeps PATH, LANG, LC_ALL,
LC_CTYPE, TMPDIR, SSL_CERT_FILE, SSL_CERT_DIR, NODE_EXTRA_CA_CERTS and the proxy
variables when present, plus declared `AGENTLOCKER_SECRET_<HEX>` variables (mapping in
[MCP auth](mcp-auth.md)). Other vendor credentials, Node options and database
settings are not inherited, and secret values never appear in arguments.

A server whose `env`, `headers` or `args` reference a `secret://` name missing from
Store is left out on prepare, start and refresh; the rest still start. It shows as
"Needs sign-in" in the wizard. `start` and `status` print `servers: N/M connected`
(a server counts as connected when it lists at least one tool; `servers: unknown`
if the gateway lists none), name failures, and print one line per skipped server such
as `skipped: <server> needs secret://<name>; set it on the Sign in screen`. Skipped
servers are not counted in `N/M`. The wizard lists them under `skipped` with `name`,
`missing` and `next_step`. A slow upstream may still be connecting when `start`
returns; run `status` again.

`stop` verifies the recorded process start identity and supervisor command line
(any `python*` interpreter path matches), terminates and reaps Node (killing it after
five seconds if sessions linger), and exits non-zero naming the port if it is still
listening. `prepare` refuses while running. No boot registration or system service is
installed.

## OAuth persistence

`sync` reads each upstream server's `oauth` object and stores string leaves under
opaque hashes of server identity and field path, plus a snapshot of references and
non-string metadata. This covers `clientId`, `clientSecret`, `accessToken`,
`refreshToken`, scopes, expiry and pending PKCE state, and lets redaction catch bare
token values. Old Store entries are not garbage collected.

`prepare` first syncs the existing runtime settings, then renders and re-seeds OAuth
from Store, so deleting the settings file is recoverable. OAuth attaches only to
currently rendered server names; treat a server name as an account boundary and clear
its Store records before reusing it for another account. Restored OAuth values live in
the private runtime settings (see [mcp-auth.md](mcp-auth.md)); do not treat them as a
nonsecret export. The supervisor syncs before persisting each diagnostic line and on
shutdown; run `sync` explicitly after authorization and before backing up Store.
Concurrent upstream writes can make a sync fail safely; retry.

## UI credentials and refresh

`prepare` consumes the wizard's `mcp-<hash>-binding` records from Store, including
headerless HTTP definitions. A binding's resource URL must match both the manifest
definition and the runtime server URL; a mismatch fails closed and needs
reconnection in setup. Bearer and API-key bindings supply the complete stored header;
OAuth bindings supply the Authorization header written by `ui_auth.Authentication`,
so no second MCPHub consent is needed. Bound servers drop any legacy `gw-*` OAuth
seed. Values reach MCPHub only through environment placeholders, never output,
argv, manifests or client snippets. `prepare` saves the source root in mode-600
`source.json` so `start` and `refresh` validate against the same manifest.

`prepare` and `start` refresh OAuth credentials expiring within 60 seconds using
the stored refresh token and client credentials, writing rotated tokens and the
complete header back to Store. Failed exchanges keep existing credentials and fail the
operation; an omitted refresh token keeps the prior one; with no refresh token an
expiring connection fails closed and needs reconnection in setup.

`python3 gateway.py refresh` checks expiry and updates headers. When running it
stops the gateway before checking credentials, then restarts Node on the same port
so the child inherits current Store values, which
means active MCP sessions must reconnect (MCPHub resolves header placeholders at
transport creation from the child's environment, and the parent cannot change it).
When stopped it only updates settings. A running gateway restarts even when headers
are unchanged. A failed refresh leaves the gateway stopped with settings preserved.

Unattended refresh is opt-in: `python3 gateway.py start --auto-refresh`. The
supervisor checks every 30 seconds (`REFRESH_INTERVAL`) and refreshes bindings
expiring within 60 seconds (`REFRESH_LEAD`). Tokens with no expiry, `bearer`
bindings and unbound MCPHub-managed OAuth are untouched. A failed exchange keeps the
prior credentials, leaves Node running and logs a fixed redacted line. Per-binding
retry delays start at the interval, double on failure and cap at the larger of 300
seconds and the interval; success resets them. Revoked refresh tokens still need
reconnecting in setup. A replacement Node startup failure ends the supervisor; use
`status` and the protected log. There is no zero-downtime guarantee. New imports
require stop, prepare, start.

## Fixture proofs

Three scripts under `examples/test-servers/` exercise the pinned runtime against loopback fake providers in disposable `.scratch/` directories (no real account): `oauth_proof.py --clients` (discovery, registration, PKCE, `sync`, re-seeding, refresh rotation, and `add` called from the Claude Code and Codex CLIs through one endpoint), `ui_gateway_proof.py --clients` (wizard API, expiry refresh, restart, `refresh`, bearer switch) and `unattended_refresh_proof.py` (`--auto-refresh` after real expiry). Product credentials are read in process and never copied. They do not establish compatibility with real providers, revoked-token handling, multi-user access control or crash-atomic persistence across several Store files. Unit tests using `tests/fake_oauth.py` cover expiry, failed grants, backoff and secret-free logs.
