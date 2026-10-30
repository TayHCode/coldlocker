# Remote catalog

The remote catalog serves published skills, rules, routines and MCP server
definitions to hosted bots over HTTPS at `https://<your-domain>/mcp`. It is
read-only, requires OAuth on every request, and runs as a Cloudflare Worker in
[`remote/`](../remote). Configure your own endpoint with `public_url`; the
maintainer's documented deployment is not a shared service for other owners.
Start with the [self-host quickstart](self-host.md). The local stdio endpoint
(`mcp_endpoint.py`) is unchanged.

For local build/test commands and a comparison with machine hosting through
Tailscale Funnel, see [hosting options](hosting-options.md).

## What is served

`python3 hub.py publish-remote` builds an artifact from the items in the shipped
`manifest.json`, verifies each item's digest, then refuses:

- symlinks, special files and symlinked catalog directories;
- secret values, tokens, email addresses, private hostnames and home paths in
  any file (findings name a category and line, never the value);
- absolute or machine paths in MCP server `command`, `args`, `env` and
  `headers`; only `secret://<name>` references are published;
- enabled routines.

Files over 1 MiB or not valid UTF-8 are listed as omitted.

Items imported into `.hub-store/catalog` stay private by default. To publish
them:

```sh
python3 hub.py publish-remote --include-local --dry-run   # lists local items; uploads nothing
python3 hub.py publish-remote --include-local --yes       # confirms and uploads
```

Local items pass the same screening and keep their repository-relative paths.
Without a terminal the command stops unless `--yes` is given. Anything a
connected client can read is readable in full, so review the list. Publishing
with local imports requires an explicit `--include-local` or `--shipped-only`
selection. This also applies to dry runs. `--shipped-only` deliberately replaces
the served catalog with shipped content only and reports the omitted count.
The selection flags are mutually exclusive; malformed local manifests are
refused rather than silently ignored.

## Tools, prompts and resources

The Worker answers the same MCP methods as `mcp_endpoint.py` with the same
validation, result shapes and error codes: `initialize` (2025-06-18, 2025-03-26
or 2024-11-05), `ping`, `tools/list`, `tools/call`, `prompts/list`,
`prompts/get`, `resources/list`, `resources/read` and `resources/templates/list`.

- Tools: `hub_list_skills`, `hub_list_rules`, `hub_list_routines`,
  `hub_list_mcp_servers`, `hub_list_plugins`, `hub_list_connections` and
  `hub_get_item`. `hub_get_item` returns `files` keyed by repository path (for
  example `skills/<name>/SKILL.md`) plus `omitted`, `truncated` and `max_bytes`,
  so skills arrive with their supporting files.
- Each skill is an MCP prompt carrying `SKILL.md` and listing its supporting file
  URIs.
- Each file is a resource at `agentlocker://skills/<name>/<path>`,
  `agentlocker://rules/<name>` or `agentlocker://routines/<id>/<file>`. Every
  file's SHA-256 is checked before it is returned.

`remote/test/golden/parity.json` records `mcp_endpoint.dispatch` output for a
fixture hub; `python3 tests/remote_parity.py` regenerates it, and tests fail on
drift.

Differences from the local endpoint:

- Non-text files and files over 1 MiB appear in `omitted` and are not resources.
- MIME types come from a built-in table (`md`, `json`, `txt`, `py`, `sh`, `js`,
  `css`, `html`, `csv`); other extensions read as `text/plain`.
- `search` and `fetch` are not served.
- `/mcp` accepts `POST` only (405 otherwise), returns `202` for notifications,
  `400` for malformed JSON, `413` above 1 MiB, and `403` for a browser `Origin`
  other than the public origin. Replies are single JSON documents, not streams.

## Architecture

| Piece | Purpose |
| --- | --- |
| Worker (`remote/`) | Stateless JSON-RPC at `/mcp`, and OAuth 2.1 via `@cloudflare/workers-oauth-provider`: authorization code with PKCE S256, dynamic client registration, refresh tokens, audience-bound tokens, protected-resource and authorization-server metadata |
| Cloudflare Access | Identifies the owner on `/authorize` only; the Worker verifies `Cf-Access-Jwt-Assertion` (signature, issuer, audience, email) before showing consent |
| KV `OAUTH_KV` | OAuth grants, clients and tokens |
| KV `CATALOG_KV` | `blob:<sha256>`, immutable `rev:<revision>`, and a `current` pointer written last |

Clients register themselves, send the owner through Access sign-in (one-time PIN
by email) and receive a `catalog:read` token that lasts one hour and refreshes.
Revoke access by deleting the grant from `OAUTH_KV` or rotating the Access
policy. Approve only clients you started.

## Deploy on your own domain

Prerequisites: a Cloudflare account that owns the domain, Node 22+ and npm.
The locked remote dependencies include Wrangler for deployment and publication.
The `cf` CLI and stored API-token publication paths are alternatives.

1. `cd remote && npm ci`
2. Create two KV namespaces (for example `agentlocker-oauth` and
   `agentlocker-catalog`) and note their IDs.
3. Enable Zero Trust (the free plan suffices) to get an Access team domain
   (`<team>.cloudflareaccess.com`).
4. Create a self-hosted Access application for `<your-domain>/authorize` with one
   allow policy for your email and the one-time PIN method. Note its AUD tag.
5. Copy `remote/wrangler.example.jsonc` to `remote/wrangler.local.jsonc`
   (gitignored). Fill in the domain, both namespace IDs, `ACCESS_TEAM_DOMAIN`,
   `ACCESS_AUD` and `OWNER_EMAIL`.
6. `npx wrangler deploy --config wrangler.local.jsonc`, with
   `CLOUDFLARE_API_TOKEN` (Workers Scripts edit, Workers KV edit, Zone/DNS edit)
   and `CLOUDFLARE_ACCOUNT_ID` in the environment.
7. Let non-browser clients past Browser Integrity Check, which rejects some MCP
   User-Agents with `403` / `error code: 1010`. Add a zone custom rule (action
   Skip, product Browser Integrity Check) for:

   ```
   (http.request.uri.path eq "/mcp") or (starts_with(http.request.uri.path, "/.well-known/")) or (starts_with(http.request.uri.path, "/oauth/"))
   ```

8. Verify: `curl -A Python-urllib/3.12 -i https://<your-domain>/mcp -X POST`
   returns `401` with a `WWW-Authenticate` header whose `resource_metadata`
   points at `/.well-known/oauth-protected-resource/mcp`.

Run `npm run typecheck && npm test` in `remote/` before deploying.

## Publish

Create `.hub-store/remote.json` (gitignored, mode 600):

```json
{"public_url": "https://catalog.example.org/mcp", "catalog_namespace_id": "REPLACE_WITH_CATALOG_NAMESPACE_ID"}
```

```sh
python3 hub.py publish-remote --include-local --dry-run
python3 hub.py publish-remote --include-local --via wrangler --yes
```

If the secret `cloudflare-api-token` exists in the local store
(`python3 store.py set cloudflare-api-token`), the Cloudflare API is used with
an `account_id` added to the JSON config. Otherwise an available `cf` CLI is
selected, then an installed Wrangler. Without any available transport, the
command reports installation/token options. Override with `--via cf|wrangler|api`.
Wrangler runs from `remote/` with explicit `wrangler.local.jsonc`; it uses an
installed executable or `remote/node_modules/.bin/wrangler` and never runs a
package install. Optional `cf_profile` selects the CLI authentication profile.
Wrangler's batch files are private and removed after success or failure; its
raw output is suppressed. See [Cloudflare's KV command reference](https://developers.cloudflare.com/workers/wrangler/commands/kv/).
Blobs upload first, then the revision index, then the
`current` pointer. Failure before the pointer write does not advance it; a
failure or timeout during that final write can leave the outcome uncertain.
Inspect remote state before retrying an uncertain publication.
Unchanged content produces the same revision.

## Connect a client

Each client needs its own OAuth grant: sign in through the Access PIN flow and
review the `catalog:read` consent. Never paste PINs or tokens into chat. The
catalog executes no upstream MCP servers and installs no hosted skills.

```sh
python3 hub.py connect --remote
python3 hub.py connect --remote --agent grok dots muse mausbot
python3 hub.py connect --remote --agent claude_ai claude_desktop chatgpt codex
```

Remote mode prints JSON with the scope, per-client steps, source URLs, support
status and a verification prompt. It reads no client settings, contacts no
server and writes nothing; `--write` is refused. `claude`, `muse_code` and `t3`
are also accepted.

`connect --remote` reads `public_url` from `.hub-store/remote.json`. Override it
with `--url https://catalog.example.org/mcp` when printing a specific deployment's
entries. The URL must be HTTPS with path `/mcp` and no credentials, query or
fragment. Missing configuration reports setup instructions instead of emitting
the maintainer's endpoint. The same setting and override apply to
`routine enable --remote` and `connect-composio --catalog`; Composio OAuth
discovery follows the selected origin. Complete OAuth against your deployment.

**verified-from-docs** means the vendor documents the capability and setup path,
not that a connection or unattended run was tested. **unconfirmed** means the
sources do not establish custom remote MCP OAuth for that surface. Sources were
reviewed 2026-10-20.

The manual examples below use `https://catalog.example.org/mcp`; replace it
with your own deployment. Generated instructions already use `public_url` or
the supplied `--url` and need no manual URL replacement.

- **Grok Bot (Cursor) — verified-from-docs.** Ask the bot to add
  `https://catalog.example.org/mcp` as an MCP server named agentlocker, finish
  browser sign-in from the card, and send a new message. Team policies may block
  the domain. See the [bootstrap](grok-bot-bootstrap.md).
- **Dots — unconfirmed.** OpenAI's [Dot guide](https://help.openai.com/en/articles/20001530-getting-started-with-your-dot)
  says plugins share ChatGPT settings but availability can differ. Try the
  ChatGPT path and check whether the Dot sees the tools.
- **Hosted Muse — unconfirmed.** Meta's [connector help](https://www.meta.com/help/artificial-intelligence/1687253048996149/)
  covers supported apps, not custom MCP. Probe by asking: `Create a Custom
  Connector named agentlocker for https://catalog.example.org/mcp using remote MCP
  over Streamable HTTP and OAuth browser sign-in.` Report if unavailable; do not
  request API keys.
- **MausBot — unconfirmed.** Tool access depends on the engine
  ([project](https://github.com/dr-biko/MausBot)). A fallback is the documented
  Codex or Claude Code setup on the bot's computer; that is not a hosted
  connector.
- **OpenMausBot — verified-from-docs, separate project.** Per its
  [custom MCP guide](https://github.com/milind-soni/OpenMausBot/blob/main/docs/custom-mcp-servers.md):
  Plugins > MCP servers > Add server > Connect to a URL, enter the URL, Test,
  Sign in, Test for tools, enable it for the bot, start a new task.
- **Claude.ai and Claude Desktop — verified-from-docs.** On Pro or Max:
  Customize > Connectors > + Add > Add custom connector, enter the name and URL,
  choose Sign in now and Register automatically. Team and Enterprise owners first
  add a Custom > Web connector under Organization settings > Connectors. Desktop
  uses this account connector, not an HTTP entry in `claude_desktop_config.json`.
  See the [connector guide](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp).
- **Claude Code — verified-from-docs.**
  `claude mcp add --transport http agentlocker https://catalog.example.org/mcp`,
  then `/mcp` to sign in ([docs](https://code.claude.com/docs/en/mcp)).
- **ChatGPT — verified-from-docs.** Plugins > plus > Add custom MCP server, name
  it `agentlocker`, enter the URL including `/mcp`, choose OAuth. Workspace policy
  controls availability ([connect](https://developers.openai.com/plugins/deploy/connect-chatgpt),
  [OAuth](https://developers.openai.com/plugins/build/auth)). ChatGPT support does
  not prove Dot support.
- **Codex — verified-from-docs for CLI and app.**
  `codex mcp add agentlocker --url https://catalog.example.org/mcp`, then
  `codex mcp login agentlocker`, or set `url` under `[mcp_servers.agentlocker]`
  ([docs](https://developers.openai.com/codex/mcp)). Codex Cloud propagation is
  unconfirmed; check it in the real execution environment.
- **Muse Code — verified-from-docs, separate from hosted Muse.** Add
  `{"mcp_servers":{"agentlocker":{"transport":"streamable_http","url":"https://catalog.example.org/mcp","enabled":true,"mode":"optional"}}}`
  to its settings, run `muse mcp login agentlocker`, then `/mcp`
  ([docs](https://dev.meta.ai/docs/muse-code/extending)).
- **T3 — unconfirmed.** The [public project](https://github.com/pingdotgg/t3code)
  documents no remote OAuth path. Verify tool visibility in the target thread and
  scheduler; do not assume inheritance.
- **Not researched:** Cursor (`{"mcpServers":{"agentlocker":{"url":"..."}}}`, then
  sign in from MCP settings; [docs](https://cursor.com/docs/context/mcp)), Hermes
  (`hermes mcp add --url <url> --auth oauth agentlocker`, then `hermes mcp login
  agentlocker`) and Gemini CLI (`gemini mcp add agentlocker <url> --transport
  http`, then `/mcp auth agentlocker`).

Local client paths are in [clients.md](clients.md).

### Verify catalog access in each bot

Paste the `verify_prompt` printed by `connect --remote`. The bot lists tools,
skills, rules and routines, reads `shared-policy`, loads one harmless skill and
one routine with `hub_get_item`, and reports `files`, `omitted` and `truncated`
and any missing tools. Connecting does not apply rules to conversations; rule
fragments must be read explicitly. Test each bot separately, including its
unattended context, and create no schedule while testing. OAuth success alone
does not prove catalog availability. For hosted routine enablement see the
[remote routine handoff](routines.md#remote-catalog-handoff).

## Operating notes

- Nothing in `remote/` imports the Python code; the shared contract is the
  artifact format in `remote/src/catalog.ts`, produced by `remote_publisher.py`.
- CI runs `npm ci`, `npm run typecheck` and `npm test` for `remote/` on a
  self-hosted Linux runner (Node 24). It does not deploy the Worker.

## Repeat a content-update check

`tools/check_catalog_update.py` compares all text files in one skill, including
supporting files, through a fresh local stdio process and the remote catalog.
It does not publish content or alter client configuration. Imported local paths
are normalized to the published `skills/<name>/...` paths before comparison.

1. Choose the local catalog root and a skill with a supporting file. Obtain an
   authenticated remote read using a private token file, or export the complete
   `hub_get_item` result from the connected hosted client as JSON. Never paste
   authentication material into a prompt or commit it.
2. Capture the baseline. Replace the example paths and URL with the selected
   installation and save receipts outside tracked content.

   ```sh
   python3 tools/check_catalog_update.py --root ./installation --url https://catalog.example.org/mcp --skill acceptance-format --remote-response ./before-remote.json --output ./before.json
   ```

3. Change the central skill/supporting content, re-import if needed, preview,
   and publish through the existing publisher. Fetch again in each client.
4. Export the new hosted tool result, then require a changed, matching package.

   ```sh
   python3 tools/check_catalog_update.py --root ./installation --url https://catalog.example.org/mcp --skill acceptance-format --remote-response ./after-remote.json --previous ./before.json --output ./after.json
   ```

Use `--token-file <private-file>` instead of `--remote-response` for a direct
OAuth-authenticated HTTPS MCP read. The script refuses redirects, suppresses
transport error details and never prints credentials or response content.
Exported results are labeled `operator-exported-tool-result`; they are operator
reports, not independently captured HTTP events. Direct reads are labeled
`authenticated-https`. Receipts contain SHA-256 hashes, endpoint, skill and
observation time. They prove content delivery, not a model's use of the skill.
Record the hosted task result separately. Omitted, truncated, stale or unchanged
content cannot pass an update check. Failure exits nonzero and writes no new
receipt; retain the exit code when collecting evidence.
