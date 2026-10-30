# Composio

Composio connects agentlocker in two directions. Both keep Composio's toolkits and
connected accounts in Composio; this connector makes no provider API calls and
copies no toolkit implementation.

## Locker → Composio (recommended)

`connect-composio --catalog` exposes the published, read-only remote catalog at
the configured `public_url` as a Composio custom toolkit. Skills, rules and
routine definitions become tools that a configured Composio session can discover.
It adds the toolkit to your Composio project, not to every session or account.

Preview the exact request (no network, Store reads or writes):

```sh
python3 hub.py connect-composio --catalog
```

Set `public_url` in `.hub-store/remote.json` first, or pass
`--url https://catalog.example.org/mcp` with `--catalog`. Use your own deployed
URL, not the example. The preview prints the body for
[the upsert endpoint](https://docs.composio.dev/reference/api-reference/toolkits/postCustomToolkitsUpsert)
(`POST https://backend.composio.dev/api/v3.1/custom/toolkits/upsert`): slug
`AGENTLOCKER`, name `agentlocker`, `app_url` `https://catalog.example.org/mcp`, auth
mode `DCR_OAUTH`, discovery `https://catalog.example.org/.well-known/oauth-authorization-server`.
Those example URLs follow your chosen catalog origin; no maintainer URL is
substituted. Existing upstream registration still uses `--name` and `--url`.
The [remote Worker](remote.md) supports dynamic registration, PKCE and the
`catalog:read` scope. Live Composio OAuth interoperability is **UNCONFIRMED**.

Register the toolkit (an external write, confirmed explicitly):

```sh
python3 store.py set composio-api-key
python3 hub.py connect-composio --catalog --write --yes
```

- `store.py set` prompts invisibly. Never put the key in chat or argv.
  `--api-key-ref secret://<name>` selects another private Store entry.
- The project key authenticates registration with `x-api-key`. It is not an
  agentlocker OAuth token. No secrets are printed or written to config.
- Registration updates display metadata for a slug the project already owns.
  A changed URL or auth mode returns a conflict. The command never deletes,
  replaces, retries or follows redirects. After an uncertain response, check
  project state before retrying.

Registration alone grants no access. Use the same project's authenticated API client
for these owner steps, and keep returned links and credentials private:

1. [Create an auth config](https://docs.composio.dev/reference/api-reference/auth-configs/postAuthConfigs):
   `POST /api/v3.1/auth_configs` with `{"toolkit":{"slug":"CUSTOM_AGENTLOCKER"}}`.
   Keep `auth_config.id`.
2. [Create a connection link](https://docs.composio.dev/reference/api-reference/connected-accounts/postConnectedAccountsLink):
   `POST /api/v3.1/connected_accounts/link` with `auth_config_id` and your session
   `user_id`. Open `redirect_url` privately, complete the owner's Cloudflare Access
   login, and approve the catalog OAuth consent.
3. Tool sync starts when the account becomes active. If it fails or schemas change,
   run `POST /api/v3.1/custom/toolkits/sync` with `slug` and `connected_account_id`.
4. Create a session with `toolkits=["CUSTOM_AGENTLOCKER"]` and
   `connected_accounts={"CUSTOM_AGENTLOCKER":["<active account id>"]}`. See
   [sessions via MCP](https://docs.composio.dev/docs/sessions-via-mcp).
5. Discover with `hub_list_skills`, `hub_list_rules` and `hub_list_routines`, then
   read items with `hub_get_item`. Composio syncs tools only; it does not turn MCP
   prompts or resources into native skills, and it does not activate routines.

Only published content is reachable. Publishing is a separate flow in
[remote.md](remote.md). Routine definitions stay disabled.

Live registration, consent, sync and session execution are **NOT RUN** in this repository.

## Composio upstream → local gateway

Register an existing Composio hosted MCP endpoint so the [gateway](gateway.md)
can serve it. Existing Composio clients keep working.

1. Use a Composio project API key (Settings → Project Settings → API Keys). A scoped
   key needs Session management and Session tool execution. Organization and user
   keys use different headers and are unsupported. See
   [authentication](https://docs.composio.dev/reference/authenticating-to-composio).
2. Get a query-free, nonsecret Streamable HTTP MCP URL. Prefer `session.mcp.url`
   from a session created with the project key (`POST /api/v3.1/tool_router/session`,
   `instant: false` to avoid charged Instant Tools). Use the returned URL; never
   construct one. Dashboard URLs with query parameters are rejected.
3. Register the URL and store the key:

   ```sh
   python3 hub.py connect-composio --name research --url '<MCP_URL>'
   python3 store.py set composio-api-key
   python3 hub.py check
   ```

   The setup wizard (step 5, private token entry, header `x-api-key`, raw value with
   no bearer prefix) can do the same. All connector entries share the one
   `composio-api-key` reference.
4. Prepare and start the gateway, stopping it first if it runs:

   ```sh
   python3 gateway.py prepare
   python3 gateway.py start
   ```

The definition is `composio-<name>`, transport `http`, with
`headers: {"x-api-key": "secret://composio-api-key"}`. Rendered config keeps an
environment placeholder; only the private gateway adapter resolves the value.
Executor can use the same URL as an HTTP source. Choosing a backend is covered in
[executor-connector.md](executor-connector.md).

Limits:

- Remote endpoints must use HTTPS. HTTP is accepted only for loopback test fixtures.
- Credentials, queries, fragments, interpolation and unsafe URLs are refused.
- Registration does not test the network, account authorization or billing.
- Repeating an identical registration changes nothing. A changed URL or conflicting
  definition fails and preserves the existing one. Use a new name, or remove the
  owned entry with `python3 hub.py remove mcp composio-<name>` (confirm when prompted),
  then rerun `connect-composio --name <name> --url '<MCP_URL>'`. Do not hand-edit the
  catalog JSON: its recorded digest and provenance must stay consistent.
- Missing keys fail at gateway preparation or startup.
- Session expiry and recreation, extra headers, cross-project accounts, user keys and
  SDK-only custom tools are not managed here.
- Consumer Connect URLs use a separate flow and are outside this connector. The old
  `mcp.composio.dev` offering is deprecated.

Verification: unit tests use a loopback MCP server that requires `x-api-key`. The
pinned-runtime proof is
`python3 examples/test-servers/composio_proof.py --runtime '<INSTALLED_RUNTIME>' --evidence '<EVIDENCE_DIR>'`.
It runs the gateway transport and header path against a local fixture. It does not
exercise Composio's hosted service. Live proof against Composio is **NOT RUN**; it needs
a free-plan project key in Store entry `composio-api-key` and a query-free session URL.

## License and terms

The connector is MIT, uses the standard library and vendors no SDK. Composio is a
hosted service governed by its [terms](https://composio.dev/terms), your account
permissions and plan limits. Recheck the SDK license before installing or redistributing it.
