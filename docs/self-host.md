# Share one library across local agents and hosted bots

Import your skills and routine instructions once. Connect agents to the library.
After changing an item, re-import it and publish the update for hosted readers.
Existing Composio connections can stay as they are.

The recorded clients are Claude Code, Codex CLI and Grok Bot. Owner staging
proved shared skill use and updates; controlled Codex and Claude runs also
executed the current routine. An independent human installation is still
unverified. See [evidence and limits](product-acceptance.md).

## 1. Install and import

Get the approved source checkout or reviewed source archive from the maintainer;
there is no published release yet. Extract an archive into a permanent directory
and open a terminal there. Install Git and Python 3.11+; Windows requires Python 3.12.4+
and uses `py -3` in place of `python3`.

```sh
python3 hub.py doctor
python3 hub.py scan --import
python3 hub.py list
python3 hub.py connect --agent claude codex
python3 hub.py connect --agent claude codex --write
```

Review the import choices and connection entries before confirming. Imported
content stays in the private local Store. Connection writes preserve other
settings and back up existing files they change. Keep this directory in place:
local connections refer to its absolute path. Other clients have
[their own connection instructions](clients.md).

Ask a local agent to find an imported skill, read its supporting files and use
it for a small task. Its own login and model access stay with that product.

## 2. Deploy your hosted endpoint once

For hosted bots, deploy the catalog Worker to your own Cloudflare account and
domain. This needs Node 22+ and npm. Follow the
[deployment steps](remote.md#deploy-on-your-own-domain) to create the two KV
namespaces, owner-only Access sign-in and private Wrangler configuration.
The deployment serves one owner's library.

Set `.hub-store/remote.json` to your deployment values. Keep this file private
(mode 600 where supported):

```json
{
  "public_url": "https://catalog.example.org/mcp",
  "catalog_namespace_id": "REPLACE_WITH_CATALOG_NAMESPACE_ID"
}
```

Use your own URL and namespace ID. Keep credentials in the private login or
environment flow described in the deployment guide.

## 3. Publish and connect your hosted bot

From the installation root:

```sh
python3 hub.py publish-remote --include-local --dry-run
python3 hub.py publish-remote --include-local --via wrangler --yes
python3 hub.py connect --remote --agent grok
```

Review the publication preview first: authorized clients can read everything
published. The second command uploads it using the installed Wrangler and your
private deployment configuration. Other transports are in
[publishing options](remote.md#publish).

Follow the generated connection instructions in the intended bot and complete
OAuth sign-in privately. Ask it to use the same skill and supporting file you
used locally. Each client signs in separately to the same catalog URL.

## 4. Use it and keep it current

Ask a connected agent to fetch a skill or routine by name and follow its current
instructions. To update the library, edit the original source and re-import it
with the same owner and origin. Local readers see the import. Repeat the
publication commands above for hosted readers, then ask both agents to fetch
the changed item. Keep one maintained source instead of editing copies in each
agent.

A routine is an instruction package. Asking an agent to follow it works without
creating a schedule. If you want automatic timing, use the selected agent's
scheduler or an [agentlocker local runner](routines.md#local-runners); review its
permissions and supported controls first. Scheduling is optional. Grok's native
timer trial did not run and remains paused; native timers are not claimed by
the recorded content-sharing evidence.

For backups and recovery, see the [release guide](release.md#private-state-upgrade-and-rollback).
For issues, see [security reporting](../SECURITY.md). The [release record](release.md)
keeps engineering evidence and unverified release claims separate from setup.
