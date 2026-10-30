# Agents sync

agentlocker drives [amtiYo/agents](https://github.com/amtiYo/agents) instead of
reimplementing its client writers. The upstream npm package `@agents-dev/cli` is
Apache-2.0, tested at **0.9.2** with Node 20+. agentlocker remains MIT and vendors no
upstream code. See upstream's [system documentation](https://github.com/amtiYo/agents/blob/main/docs/agents-system.md).

## Two-way flow

- `agents-export` adds locker skills and MCP definitions to a project's
  `.agents/agents.json` and `.agents/skills/`.
- `agents-sync` exports, then runs the installed `agents sync --path` writer.

Existing integrations, profiles, options, unrelated servers and skills stay upstream-owned.
A new target enables Claude Code, Codex, Cursor and OpenCode, uses project-scoped Claude
MCP and `commit-generated` mode, and disables automatic Cursor approvals.

```sh
npm install --prefix .scratch/agents-runtime @agents-dev/cli@0.9.2
python3 hub.py agents-export --target <PROJECT> --gateway-url <GATEWAY_URL>
python3 hub.py agents-sync --target <PROJECT> --agents-bin .scratch/agents-runtime/node_modules/.bin/agents --gateway-url <GATEWAY_URL>
python3 hub.py agents-sync --target <PROJECT> --agents-bin .scratch/agents-runtime/node_modules/.bin/agents --gateway-url <GATEWAY_URL> --check
```

- The optional gateway entry is `agentlocker-gateway` (transport `http`). Sync neither
  starts nor probes it. Other servers stay direct upstream definitions.
- Bundled script arguments resolve against the selected locker root. Generated files
  containing those paths are machine-local, not portable source.

Import is an optional fallback. It copies a project's public upstream definitions and
whole skill packages into the locker with `agents-sync` provenance:

```sh
python3 hub.py agents-import --target <PROJECT> --owner <MAINTAINER>
```

`--locker-root <LOCKER>` selects an alternate manifest root for all three commands.
Imports go through `import-skill` and `import-mcp`. Nested skill packages flatten by
package name with their supporting files; duplicate names are refused. There is no
watcher, schedule, automatic import during sync, or replacement of upstream tools.

## Secrets

The connector never resolves Store values.

- `secret://<name>` exports as `${AGENTLOCKER_SECRET_<UPPERCASE_UTF8_HEX_NAME>}` and
  imports back to the same reference.
- Other whole `${VAR}` values become `secret://agents-<first 24 hex of sha256(VAR)>`.
  Find the name in the imported nonsecret definition and populate it privately through [Store](mcp-auth.md).
- Header references cover the complete header value, including any auth scheme.
- Literal sensitive values, composed secret templates and templates with defaults are
  refused. No value is echoed. The source is never rewritten or scanned for credentials.

`agents-sync` refuses a target that contains `.agents/local.json`, including the empty
file upstream initialization creates. Use a placeholder-only export target. Keep any
credential-bearing upstream project unchanged and sync it separately. Inherited
credential variables and `AGENTS_*` overrides are removed from the child environment.
HOME, XDG and temp directories point into an isolated directory under the locker's
ignored `.scratch/` (`--throwaway-root` selects one there). Project and child HOME must
differ. Upstream output is suppressed to prevent credential logging; the connector reports the exit code.

Targets must be explicit. Real home client trees, their ancestors, `.claude*` roots and
symlink escapes are refused even when HOME is overridden. Symlinks inside the target
remain allowed for upstream skill bridges. This is path validation and environment
isolation, not an OS sandbox: the configured binary is trusted, so review upstream
definitions before syncing.

## Preservation and exit codes

- Imports and exports check conflicts before copying. Differing content is never
  replaced, including a changed item the connector exported earlier; reconcile it, then retry.
- Equal items are left untouched. Unindexed destinations are not adopted. No automatic removal or bidirectional resolution.
- Exports keep upstream integration and profile selections, so an active profile can omit exported servers.
- The connector cannot tell whether a client has loaded a generated config.
- Schema 4 is required. Import accepts public stdio and HTTP servers with env, headers and descriptive
  metadata. Disabled servers and advanced options need migration or stay upstream.
- Secret-looking env or header literals of 24+ characters require references, even
  under a neutral key. Binary credentials, symlinked packages and oversized packages are not imported.

Exit codes:

- `--check` checks export drift first without writing files or creating directories.
  Export drift exits **2** before upstream runs; run `agents-export` to export it.
- Otherwise upstream returns **0** when current and **2** for drifted client files.
- A missing CLI, conflict or unsafe input exits **1**. Subprocess failures keep upstream's code.

Offline config sync needs no signup or API key. Running servers and authenticated client
sessions are separate checks.

## Verified scope

Verified with `@agents-dev/cli` 0.9.2: import, export, idempotence, a real `agents sync`
writing Claude Code, Codex, Cursor and OpenCode configs, and `--check` exits 0 and 2.

**UNCONFIRMED:** a real client loading those configs, authenticated MCP tools, and
gateway availability. A driven sync requires a placeholder-only target without `.agents/local.json`.
