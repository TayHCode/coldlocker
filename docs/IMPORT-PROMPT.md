# Import what you use

Paste the block below into Claude Code, Codex, Cursor, OpenCode, Grok Bot or any
other agent for a guided scan, import and connect. Replace `<HUB_DIR>` with your
agentlocker checkout. Clone it with `gh repo clone <OWNER>/agentlocker` or
`git clone <HTTPS_URL> agentlocker`; if neither works see
[No repo access](#no-repo-access-on-this-machine). The agent asks what to import
before it writes anything. No shell access? Use the last line of the block.

```text
Find the skills, rules, routines, MCPs, plugins and connections I currently use
and bring the approved inventory into agentlocker. Work from <HUB_DIR>.
Read AGENTS.md, docs/scan.md, docs/formats.md and docs/mcp-auth.md first. On Windows use python instead of python3 in every command below.
1. Run python3 hub.py scan (dry-run; no writes). Include known project roots
   with --project <PROJECT_DIR> when needed; do not assume home discovery is complete.
   Show per-agent/per-type counts and new/connect/conflict/skipped items with IDs
   and reasons. Vendor/plugin_cache/duplicate/already_in_locker rows are collapsed;
   use python3 hub.py scan --all to inspect them, or --json for structured detail.
   Flag vendor/plugin-cache/system content (including Claude's synced hosted skills); never claim or copy its code.
2. Use judgment to look for misses: other agent folders, project-level configs,
   crontab, systemd, launchd, T3 schedules and hosted-agent exports (not scanned: Task Scheduler).
   Grok Bot export: extract skills.tgz into its folder, set GROK_EXPORT_DIR to it, scan again.
   Read only relevant nonsecret settings/exports; never dump credential files. Prefer live
   Executor/MCPHub/Composio/amtiYo-agents upstream references, not copies; read docs/executor-connector.md,
   docs/gateway.md, docs/composio.md, docs/agents-sync.md. Show manual additions; ask what to import; wait for approval.
3. Only after approval run python3 hub.py scan --import for all eligible items,
   or python3 hub.py scan --import --only <ID> <ID> for the approved selection.
   Use --yes only to carry that approval into a non-interactive import.
   For approved misses, prepare validated inputs per docs/formats.md and docs/cli.md:
   python3 hub.py import-skill <SKILL_DIR>
   python3 hub.py import-rule <RULE_MD>
   python3 hub.py import-mcp <MCP_JSON>
   python3 hub.py import-routine <ROUTINE_JSON>
   python3 hub.py import-plugin <PLUGIN_METADATA_JSON>
   python3 hub.py import-connection <CONNECTION_METADATA_JSON>
   Record provenance/one owner; keep routines enabled=false with one owner and one runner.
   Plugins/connections are metadata only; no plugin install or OAuth success is implied.
   Preserve conflicts and unfamiliar destinations; manual imports can replace same names.
   Never ask for, print, paste or log secret values. Use only the private store and
   secret:// references through the approved private auth flow in docs/mcp-auth.md.
   Never write real ~/.claude*, ~/.codex, ~/.cursor, ~/.config/opencode, crontab,
   systemd units or T3 state. No destructive ops, publishing or schedules without approval.
4. Run python3 hub.py check, then python3 hub.py connect to print client settings.
   Show settings for detected clients and the Grok Bot bridge; report other agents as manual.
   Only explicit user approval permits python3 hub.py connect --write (backed-up merge).
   That is the sole real client-config write exception; never edit those files yourself.
5. Report found/imported counts per agent/type, skipped items + why, conflicts,
   check command/exit/result, connection steps still needed and what you could not verify. In an agent sandbox python3 hub.py check is the gate; if the sandbox denies localhost sockets, ps or crontab, run the full unittest suite outside the sandbox and say so in this report.
Without shell access: read these docs, produce the inventory/proposed list, and ask me to run the commands; claim no execution.
```

The agent inventories readable local content, shows grouped results and asks what
to import. Approved imports copy content with provenance, record plugins and
connections as metadata, and leave routines disabled. Live tools stay owned by
their upstream. It then checks the locker and prints connection settings for
detected clients and Grok Bot bootstrap guidance; writing them needs separate
approval. For hosted bots, `python3 hub.py connect --remote` prints instructions
for the remote catalog. Catalog discovery does not prove skill activation, tool
execution, provider authentication or hosted sync.

## Running under Codex

`scan --import` writes `.hub-store/` inside the checkout, so Codex needs a
writable workspace. On Windows in a non-interactive session the workspace-write
sandbox did not work; the only unattended invocation found was
`--ignore-user-config -s danger-full-access -c approval_policy=never` inside a
memory-and-time bounded wrapper. That drops sandbox confinement, so use it only on
a checkout you trust. Codex also writes a project trust entry into
`~/.codex/config.toml` even with `--ignore-user-config`; for unattended runs point
`CODEX_HOME` at a scratch directory.

## No repo access on this machine

Without GitHub credentials, bundle the repository on a machine that has them:

```sh
git bundle create agentlocker.bundle --all
```

Copy `agentlocker.bundle` here, then:

```sh
git clone agentlocker.bundle agentlocker
git -C agentlocker remote set-url origin <HTTPS_URL>
```

Otherwise the clone's origin keeps pointing at the bundle file. Set a repo-local
`user.email` from `config/commit-email-allowlist.txt` before committing.
