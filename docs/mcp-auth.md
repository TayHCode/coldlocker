# MCP authentication boundary

The setup wizard ([ui.md](ui.md)) collects credentials and runs OAuth consent; the gateway ([gateway.md](gateway.md)) injects them into upstream servers. This page defines the credential store and the rules both follow.

The repository holds names and `secret://<name>` references only. Secret names match `^[a-z0-9][a-z0-9._-]{0,62}$`.

## Store

`FileStore` keeps values at `.hub-store/secrets/<name>` under the repository root. `.hub-store/` and `secrets/` are mode 700; values are mode 600 and replaced atomically. Store paths reject symlinks at or below the trusted base (repository root, home or system temporary directory). This is local owner-only storage: it is not encrypted at rest and does not protect against another process running as the same OS user.

To relocate the store, copy `config.example.json` to gitignored `config.json` and set `store_path` (relative paths resolve against the repository root; an absolute path is allowed for operator-managed storage and must be kept out of version control by the operator). The default location and `config.json` are already ignored. Check:

```sh
git check-ignore config.json .hub-store/secrets/example-token
git ls-files -- config.json .hub-store/
```

The first command lists both paths; the second prints nothing.

`python3 store.py` commands:

- `set example-token` reads the value from stdin or an invisible prompt (a final line ending is removed). Never pass a value in argv.
- `has example-token` prints yes/no and exits 0/1.
- `list` prints names only; `path` prints the store directory.
- `delete example-token` removes a value idempotently.

Failures use generic messages, and no command prints a value.

`FileStore` offers `put`, `has`, `names`, `delete`, `resolve` and `redact`. Only an in-process deployment adapter calls `resolve("secret://example-token")`. `redact(text)` replaces known values, longest first, for local diagnostics. The endpoint and renderer never resolve secrets.

## How the gateway uses secrets

The adapter reads the store in process and passes only referenced secrets to the MCPHub process environment. A name maps to `AGENTLOCKER_SECRET_` plus its uppercase UTF-8 hex encoding, and the renderer emits `${VARIABLE}` placeholders with that mapping. Values never appear in argv, output, generated config, tracked files or client responses. An HTTP `Authorization` secret holds the complete header value, including any scheme.

Credentials on a stdio command line follow the same path. Import moves the value into the store and leaves `secret://` in `args` (`--flag=secret://name` for an inline flag); the renderer emits an `${AGENTLOCKER_SECRET_...}` placeholder inside the argument and MCPHub expands it when starting the server. Neither the catalog, `hub_list_mcp_servers` nor the rendered config holds the value. The upstream server itself receives the expanded argument, so another local user may see it in the process list; prefer `env` for new servers. A definition with a literal credential in `args` is refused with `credential in command-line args`.

Sign in lists each reference like an `env` variable, named by its flag (`--api-key`) or `arg <index>`, reports it missing when the store lacks it (the gateway then starts without that server and names the missing reference; see [gateway.md](gateway.md)), and saves a typed value under the referenced name without returning it.

A scanned HTTP server whose bearer token or header comes from an environment variable (Codex `bearer_token_env_var`, `env_http_headers`, or a `${VAR}` header value) imports with that header set to a `secret://scan-...` reference and no stored value. The scan never reads the variable. Sign in shows the server as `needs-auth`.

## OAuth once

Authorize an upstream account once, in the wizard (discovery and PKCE consent) or through MCPHub's OAuth client. Clients then authenticate to the gateway, not to each upstream. MCPHub persists OAuth tokens in its writable settings, so `gateway.py sync` copies the OAuth object into the store and `prepare` re-seeds it. The runtime directory is mode 700 and gitignored with mode-600 files; the runtime settings copy is sensitive and is not a nonsecret export. Refresh, binding and sync behavior is in [gateway.md](gateway.md).

Provider and model logins, native app sessions, approvals and product-owned connectors stay product-owned; the hub never reads or merges vendor credential trees. A hosted deployment can swap `FileStore` for tenant-scoped storage while keeping the manifest, reference syntax, read-only tools and nonsecret renderer; see [hosted-later.md](hosted-later.md).

## Read-only inventory endpoint

`python3 mcp_endpoint.py` speaks newline-delimited JSON-RPC 2.0 over stdio. It handles initialize (echoing the client's protocol version if it is 2025-06-18, 2025-03-26 or 2024-11-05, otherwise answering 2025-06-18), initialized notifications, ping, tools/list, tools/call, prompts/list, prompts/get, resources/list, resources/read and resources/templates/list. Tools:

- `hub_list_skills`, `hub_list_rules`, `hub_list_mcp_servers`, `hub_list_routines`, `hub_list_plugins` and `hub_list_connections` list entries of one kind. Arguments must be empty; callers cannot select paths.
- `hub_get_item` reads one skill, rule or routine by `kind` (`skill`, `rule`, `routine`) and `name`, verifies the indexed content hash and returns file contents, so a remote client can load a skill without filesystem access. A skill returns every file in its directory (SKILL.md, `references/`, `scripts/`, `assets/`) in `files` as relative path to UTF-8 text. Non-text files, symlinks, `.git` entries, unsafe names and anything beyond the 1 MiB total cap are listed in `omitted` with `path`, `reason` (`non-text`, `symlink`, `excluded`, `unsafe-path`, `size-cap`) and, where known, `size`; `truncated` is true when the cap cut content.

Clients with prompt and resource support can use skills natively. Each skill is a prompt: `prompts/get` returns its SKILL.md as a user message and lists its supporting files. Skill files, rules and routines are resources at `agentlocker://skills/<name>/<path>`, `agentlocker://rules/<name>` and `agentlocker://routines/<id>/<file>`; `resources/read` verifies the hash and returns text, or a base64 `blob` for a non-text file within the cap. `resources/list` omits unreadable items; the listing tools report them with an `error` field (`unreadable file`, `invalid JSON` or `invalid text encoding`).

Errors carry no absolute paths or secret values: `-32602` for an unknown item, prompt or resource or a malformed URI, `-32603` for `Content hash mismatch for <kind>: <name>`, `Invalid manifest`, `Hub manifest unreadable`, `Unreadable file: <path>` and `Unsafe path: <path>`.

Entries carry manifest identity, path, owner, provenance, digest and a description when readable. Results merge the shipped index with the imported `.hub-store/catalog/` layer. MCP listings add a `server` object with transport and connection fields, keeping secret references unchanged. Listing never executes server commands or reads store values; symlink paths are refused. This endpoint is stdio-only; the gateway serves tool execution over loopback HTTP, and the hosted read-only catalog at `https://agentlocker.dev/mcp` uses OAuth (see [remote.md](remote.md)).
