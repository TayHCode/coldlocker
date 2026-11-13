# Coldlocker

Share skills, routine instructions, MCP servers, connections and plugin metadata
across your AI agents. Scan existing agent setups into a private local catalog,
then connect each agent over MCP. Connections and plugins are listed, not run.
MIT licensed; see [LICENSE](LICENSE).

Release status: unreleased. Local catalog acceptance is recorded; the broader
[cross-agent product journey](docs/product-acceptance.md) remains to be proven.
See the [evidence and release gates](docs/release.md).

## Quickstart

You need Git, Python 3.11+ and Claude Code or Codex CLI. On Windows use Python
3.12.4+ and `py -3` in place of `python3`. Clone into a permanent folder:
connected agents remember its path. Other ways to get the source are in the
[install details](docs/install.md).

1. **Install.** `doctor` should print only `PASS` lines.

   ```sh
   git clone https://github.com/TayHCode/coldlocker
   cd coldlocker
   python3 hub.py doctor
   ```

2. **Scan.** `scan` lists the agent content it found and changes nothing. The
   [scanner](docs/scan.md) covers each client; the [guided import](docs/IMPORT-PROMPT.md)
   lets an agent walk you through it.

   ```sh
   python3 hub.py scan
   ```

3. **Import.** `scan --import --yes` copies what the scan found into the private
   catalog, holding back anything that needs review. Every import runs the
   [safety scan](docs/safety.md): high findings block it unless you pass
   `--approve-risky` after review, and embedded secrets never import.

   ```sh
   python3 hub.py scan --import --yes
   python3 hub.py list
   ```

   Single items: `python3 hub.py import-skill <path>`, `python3 hub.py import-rule <path.md>`,
   `python3 hub.py import-routine <file.json>`, `python3 hub.py import-mcp <file.json>`;
   options `--owner O`, `--origin L`, `--name N`. Definitions use `secret://<name>`
   references, never values ([MCP auth](docs/mcp-auth.md)). A new install lists only
   `hub-discover` and your imports; [skill packs](docs/cli.md#skill-packs) add the
   shipped skills indexed in [manifest.json](manifest.json).

4. **Connect.** On Linux and macOS the first line starts the [gateway](docs/gateway.md)
   (Node 20+) so the agents `connect` writes can call your imported MCP servers;
   `connect` reminds you while it is stopped. `connect` previews; `--write` applies
   with backups. The client you imported from may list each tool twice; see
   [imported MCP servers](docs/clients.md#imported-mcp-servers). On Windows, without the
   [gateway prerequisites](docs/install.md#optional-shared-authenticated-tools) or with no
   imported MCP servers, skip the first line; on Windows `--write` then adds each
   imported stdio server to every client it writes.

   ```sh
   python3 gateway.py install && python3 gateway.py prepare && python3 gateway.py start
   python3 hub.py connect --agent claude codex
   python3 hub.py connect --agent claude codex --write --yes
   claude mcp list
   ```

   `claude mcp list` should show `coldlocker` as connected. For Cursor, OpenCode
   and other local clients, use the [client instructions](docs/clients.md); manual
   examples write your installation directory as `<HUB_DIR>`. Hosted bots, such as
   the [Hosted Bot bridge](docs/hosted-bot-bootstrap.md), connect to a
   [self-hosted](docs/self-host.md) read-only [remote catalog](docs/remote.md).

5. **Use.** Ask a connected agent:

   > Find a skill in Coldlocker, read its supporting files, and use it for my task.
   > List the routines and read one when I ask you to follow it.

   Agents fetch entries with `hub_get_item` from `mcp_endpoint.py`. To put skills
   on disk instead, preview `python3 hub.py link --target <root> --dry-run`, then
   run it without `--dry-run`. Routines are instructions, not timers
   ([scheduling](docs/routines.md)); the optional [gateway](docs/gateway.md) runs
   imported MCP servers behind one endpoint, and [Composio](docs/composio.md) MCPs
   stay in Composio.

6. **Update.** Edit the original item and run the import again; Coldlocker keeps
   the old copy in `.hub-store/history/`. A skill the Claude scan imported keeps its
   origin: `python3 hub.py import-skill <SKILL_DIRECTORY> --origin scan-claude`.
   `python3 hub.py remove <kind> <name> --yes` removes an import. To update
   Coldlocker itself, run `python3 hub.py update` ([upgrade guide](docs/upgrade.md)).

## Checks and limitations

A public source export excludes private maintainer configuration, workflow
files and local state. For the exported catalog, verify local health and the
safety and MCP endpoint behavior with:

```sh
python3 hub.py doctor
python3 -B -m unittest discover -s tests -p test_safety.py -v
python3 -B -m unittest discover -s tests -p test_endpoint.py -v
```

Source-export contributors run `python3 -B tools/verify_export.py`. See
[Contributing](CONTRIBUTING.md) for the private maintainer checks, `python3 hub.py check`
and full unittest discovery. These checks do not approve publication.

## Reference

- [Overview](docs/OVERVIEW.md) and [vision](VISION.md)
- [Install details](docs/install.md), [commands](docs/cli.md), [imports and links](docs/reference.md), [file formats](docs/formats.md)
- [Client setup](docs/clients.md), [capability matrix](docs/capability-matrix.md), [wizard](docs/ui.md), [Executor](docs/executor-connector.md), [agents sync](docs/agents-sync.md)
- [Related projects](docs/related.md), [catalog search experiment](docs/catalog-search.md), [acceptance](docs/acceptance.md)
- [Changelog](CHANGELOG.md) and [security reporting](SECURITY.md)
- Optional owner policy pack, not needed for anything above: [hooks](docs/hooks.md), [core and policy split](docs/core-vs-policy.md)
