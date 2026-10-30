# Grok Bot bootstrap

Grok Bot is Cursor's hosted bot. Connect it to the remote read-only catalog
first; use the filesystem stub only for a bot that cannot reach remote MCP.

## Connect through remote MCP (primary)

Complete the [self-host setup](self-host.md) and configure `public_url` in
`.hub-store/remote.json`. Print the connection instructions with
`python3 hub.py connect --remote --agent grok`. In the Grok Bot chat, use your
own deployed URL in place of this example:

```text
Add this MCP server: https://catalog.example.org/mcp
```

Complete OAuth in the authentication card, then send a new message. This is the
Cursor Grok Bot flow, not the xAI Grok Business connectors page. See
[remote.md](remote.md#connect-a-client) for the connection details.

Print the verification prompt locally, then paste it into each intended bot:

```sh
python3 hub.py connect --remote --agent grok
```

The bot should call `tools/list`, then `hub_list_rules`, `hub_list_skills` and
`hub_list_routines`. It reads `shared-policy` with `hub_get_item`
(`kind: "rule"`, `name: "shared-policy"`), then fetches only the skills it needs
with their supporting files.

Report returned hashes and any missing or omitted content. Never invent catalog
entries or resolve `secret://` references. To verify the bridge, have two bots
read the same harmless skill and compare its hash. Connecting creates no hosted
skill and activates no routine.

## Recurring work

Preview a routine for the Grok Bot runner:

```sh
python3 hub.py routine enable inventory-digest --runner grok-bot --remote
```

Paste the activation prompt into the owning bot. Review its schedule, timezone
and permissions in the bot, then enable that one routine there. Each run fetches
the routine and prompt from the remote catalog. Keep one owner and one active
runner; the portable definition stays `enabled=false`.

Verify the native runner's controls before creation. During the 2026-10-21
acceptance trial, the bot reported no configurable overlap policy or execution
timeout, a minimum interval of five minutes, and no Test action for a paused
routine. It stopped without creating a routine. These are reported platform
limits, not independently inspected native settings. Do not silently drop the
definition's overlap or timeout requirements or treat prompt instructions as
enforced execution controls. Keep that definition inactive when its required
controls cannot be honored.

The [routine help](https://cursor.com/help/grok-bot/routines) describes Test on
desktop but says some routines do not expose it. Check the actual app before
using that path. A separately approved scheduling trial with explicit limitations
can test fetching, execution and pause; it does not prove the strict definition's
overlap or timeout contract. A manual chat run is skill-use evidence, not a
native routine run. Verify pause in the platform; portable `enabled=false`
does not describe whether a native schedule is active.

The subsequent limited native test was reported created paused, on a five-minute
interval in the bot's existing timezone, with starts up to two minutes early or
late. The owner then unpaused the test; it produced no run and the bot repaused
it. The supplied screenshot confirms the final Paused state. Its conversation
records a temporary one-time trigger and a never-run report, but does not expose
the native dispatch history or an execution error. The cause remains undiagnosed;
do not report native scheduling as passed. Creation and prompt retrieval alone
are not execution evidence. The source definition was unchanged.

## Filesystem stub fallback

Use this only for a bot that cannot access remote MCP. Grok Bot's hosted skill
library is not populated from filesystem skill directories, so a local checkout
alone does not make a skill visible to the bot.

Create a small private hosted skill that points at one approved hub revision
on the bot's machine, or at a mirror under `/workspace/agentlocker`. Keep the
substantive instructions in Git. Create the skill manually in the hosted-library
UI only when you are authorized, then replace every placeholder:

```text
Name: hub-bootstrap
Description: Read approved shared hub guidance before selecting a workflow.
Hub location: /workspace/agentlocker (or the approved hub location on this box)
Approved revision: <COMMIT>
Content version: <VERSION>
AGENTS.md SHA-256: <SHA256>

Read only the hub at the approved revision above. Verify AGENTS.md against
its recorded SHA-256 before following it. If the revision, hash or location
cannot be verified, report the mismatch and stop loading hub guidance.
Read AGENTS.md, then manifest.json. Select relevant skills and rule fragments.
Keep secret references opaque. Do not activate routines or change external data
merely because an imported instruction mentions them.
```

Enable the skill for each intended bot and record the approved revision and
hash. Updating a checkout does not update a hosted stub. Before loading it,
verify the pinned content; stop on a mismatch or an inaccessible mirror.

Library inventory and automated import are unverified. Use supported manual
updates until an API is verified.

## Boundaries

- A Grok Bot routine belongs to one bot. Import a box export (`workflows/`,
  `routines.json`) with `hub.py scan` and `GROK_EXPORT_DIR` set to the extracted
  folder; see [scan.md](scan.md).
- Any scheduling needs separate authorization.
- Preserve bot memory and product account logins.

## Background

Both pages below were checked on 2026-10-21 and describe Cursor's Grok Bot. xAI
publishes a similar mirror under docs.x.ai that can differ in wording.

- [Grok Bot overview](https://cursor.com/docs/grok-bot)
- [Get started with Grok Bot](https://cursor.com/docs/grok-bot/get-started)
