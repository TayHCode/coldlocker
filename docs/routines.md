# Routines

Follow [formats.md](formats.md). A routine definition has an `id` matching
`^[a-z0-9][a-z0-9-]{0,62}$`, a description, exactly one owner, one preferred
runner, a schedule, an IANA `timezone`, a relative prompt path, an `overlap`
policy, a positive `timeout_seconds` and `enabled: false`. Definitions stay
disabled in the shipped manifest and in imported catalog entries. Import and
link never register a schedule.

Runner labels: `t3-scheduler`, `systemd-timer`, `cron`, `launchd`, `claude-code`,
`codex`, `grok-bot`, `dots`, `muse`, `mausbot`, `n8n` and `schtasks`. Choosing a
runner at enable time does not rewrite the preferred runner in the definition.
Keep one active runner per intended occurrence to avoid duplicate work.

## Schedules

- `interval`: `every_seconds`, at least 60 for local runners.
- `fixed_time`: `time_of_day` and optional ISO `weekdays` (Monday=1 … Sunday=7).
  An omitted or empty list means every day.
- `cron`: `expression`, five numeric fields. Stars, lists, ranges and steps are
  supported; Sunday is 0 or 7; day-of-month and day-of-week use the usual OR rule.
  Named fields and macros need a platform handoff.
- `webhook`: platform handoff only.

`overlap` is `skip`, `queue` or `allow`. `timeout_seconds` is required.

[The example definition](../examples/routine.example.json) names its sibling
[routine-prompt.md](../examples/routine-prompt.md). Keep the prompt beside it:

```sh
python3 hub.py import-routine examples/routine.example.json
python3 hub.py check
```

Import copies the definition and prompt into `.hub-store/catalog/routines/<id>/`
and indexes them. There is no general `apply` command.

## Enable and disable

```sh
python3 hub.py routine enable inventory-digest --runner cron --agent-command 'codex exec'
python3 hub.py routine enable inventory-digest --runner cron --agent-command 'codex exec' --write --yes
python3 hub.py routine status
python3 hub.py routine disable inventory-digest --runner cron --write --yes
```

- Without `--write`, enable and disable print a JSON plan. `--yes` alone does not write.
- `--write` requires an interactive confirmation or `--yes`. A noninteractive
  call without `--yes` refuses before any mutation.
- Review the prompt, schedule, argv and destinations in the plan first. The
  routine runs the selected CLI under your existing account and permissions.

Per-runner records, pinned inputs and private timestamped backups live in
`.hub-store/routine-enablement/` (or the configured `store_path`), outside Git.
Repeating an unchanged enable changes nothing. Disable removes only recorded
artifacts; modified or unfamiliar files and marker conflicts cause refusal.
Foreign crontab entries and client settings are preserved. Keep the checkout and
local store at their installed paths until the jobs are disabled.

`routine status` reports recorded state. For Windows tasks it also runs
`/Query /XML`; `native_status` reports task existence, whether the installed
task still matches its record, or a query error. Native scheduler health beyond
that is **UNCONFIRMED** from this repository. Local native installs record
`enabled`; exports record `handoff-ready`.

## Local runners

Cron runs on Linux and macOS, systemd user timers on Linux, launchd LaunchAgents
on macOS, and Windows Task Scheduler (`--runner schtasks`) on Windows. Previews
work on any platform; writes check platform and command availability.

- `--agent-command` is a POSIX-quoted argv, parsed without a shell, even when
  previewing on Windows. The full prompt is appended as one argument. Examples:
  `codex exec`, `claude -p`, `'/path with spaces/agent' exec`. Put no credentials
  in it. The plan shows `agent_argv` and the wrapper `command`.
- Install and authenticate the CLI yourself; writes refuse a missing executable.
  The plan resolves the executable to an absolute path and pins the PATH it uses.
- Execution runs in the hub root with the prompt pinned at enable time. Re-enable
  to pick up prompt changes.
- All four adapters tick once a minute. The wrapper evaluates the definition's
  timezone without changing the scheduler's timezone. Non-UTC zones need system
  timezone data; without it, local setup refuses.
- Intervals run on the first tick after they elapse. Fixed times run in their
  matching minute, with no catch-up.
- Overlap uses private advisory locks: `skip` ignores a busy run, `queue` waits,
  `allow` runs concurrently where the scheduler does. systemd and launchd never
  start a second instance of a running service.
- `timeout_seconds` applies to the direct agent process (exit 124 on timeout).
  A slot is recorded before execution; failures do not retry it. Disable stops
  future ticks; a running agent may finish. Killing its descendants is outside
  this adapter.

Installation details:

- **Cron:** entries carry a checkout-specific `# agentlocker-...` marker and call the wrapper.
- **systemd:** user `.service` and `.timer` under `~/.config/systemd/user/`, then
  `systemctl --user daemon-reload` and `enable --now`.
- **launchd:** plist under `~/Library/LaunchAgents/`, via `launchctl bootstrap`
  and `bootout` in the current GUI user domain.
- **Windows:** task `\agentlocker-<checkout-hash>-<routine-id>`, created with
  `schtasks.exe /Create /TN <name> /XML <file>`. The XML runs as the current user
  with `InteractiveToken` and `LeastPrivilege`, so that user must stay logged in.
  No password or elevated account is configured. Arguments use Windows quoting;
  the prompt and argv stay in a private JSON job file. Commands use argument
  lists without a shell. Creation never passes `/F`. `/Query /TN <name> /XML /HRESULT`
  separates a missing task from a query failure. Updates and `/Delete /TN <name> /F`
  require the exported XML to match the owned record. Failed operations try to
  restore the previous task and files and report any recovery errors.
  References: [schtasks create](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/schtasks-create),
  [schtasks query](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/schtasks-query).

No system-wide service is installed.

## Platform handoff

```sh
python3 hub.py routine enable inventory-digest --runner t3-scheduler
python3 hub.py routine enable inventory-digest --runner claude-code --write --yes
python3 hub.py routine enable inventory-digest --runner grok-bot
```

The handoff adapter prints the prompt, schedule in words, overlap, timeout and
remaining steps. agentlocker never calls hosted services. Confirm the
destination's timezone, overlap and timeout support before activating. `--write`
records a local handoff only; it does not prove the remote task exists. Disable
clears the local record and prints removal steps; remove hosted schedules in
their own platform.

- **T3:** exports the `schedule_task` payload: `prompt` plus a structured
  `schedule` (interval `everyMs`, fixed-time `timeOfDay`/`weekdays`, or webhook).
  Cron is refused; translate it in T3. There is no timezone field, so verify the
  app timezone. Review and invoke it in T3.
- **Claude Code:** with `--write` and confirmation, writes
  `~/.claude/scheduled-tasks/agentlocker-<id>/SKILL.md` (frontmatter plus prompt).
  This is content for the local Claude Code Desktop scheduling path. Writing the
  file does not schedule it; select the folder, timing and enabled state in the
  app. Unfamiliar files refuse replacement. Cloud `/schedule` is a different
  feature with repository and account requirements; its minimum recurring
  interval is one hour. Desktop supports a one-minute minimum. Verify task
  connectors and controls separately; an in-session `/loop` is not an independent
  cloud schedule. See [cloud tasks](https://code.claude.com/docs/en/routines) and
  [Desktop tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks).
- **Codex:** gives the prompt and schedule for an automation. Review the workspace and enable it in Codex.
- **Grok Bot:** create the routine in the owning bot and review its instruction
  and timing. Optionally save the prompt as a private skill and enable it per bot.
  Filesystem links do not populate the hosted library. See [the bootstrap bridge](grok-bot-bootstrap.md).
- **n8n:** gives the Schedule Trigger or Webhook plus agent action steps. Activate the workflow in n8n.
- **Dots:** ask the Dot to run the check with this prompt and schedule. Manage it
  under Recent activity or ChatGPT Scheduled. A public creation API and Dot custom
  MCP parity are **UNCONFIRMED**. [Dot scheduling](https://help.openai.com/en/articles/20001530-getting-started-with-your-dot).
- **Muse:** hosted recurring scheduling and a public creation API are **UNCONFIRMED**.
  Muse Code's interactive `/loop` (intervals or five-field cron, skips busy
  occurrences, expires after seven days) is not durable. For durable execution,
  use a local runner with `--agent-command` pointing at the installed, authenticated
  Muse Code headless CLI. A stable `muse serve` scheduling API is **UNCONFIRMED**.
  Hosted Muse custom MCP support is **UNCONFIRMED**, so connectivity cannot be assumed.
  [Muse](https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/),
  [Muse Code loop](https://dev.meta.ai/docs/muse-code/interactive),
  [Muse Code extensions](https://dev.meta.ai/docs/muse-code/extending).
- **MausBot:** in routines/calendar, select the bot and its computer, paste the
  prompt, and choose one-time, selected weekdays, or an interval of 5–1,440 minutes.
  Enable and manage it in the app. A public routine creation API is **UNCONFIRMED**.
  [MausBot](https://github.com/dr-biko/MausBot), [overview](https://mausbot.com/).

The setup wizard keeps showing imported definitions as disabled, for example
`cron · 0 9 * * 1 · America/Chicago · imported disabled`. Local enablement
records never change those definitions.

## Remote catalog handoff

```sh
python3 hub.py routine enable inventory-digest --runner t3-scheduler --remote
python3 hub.py routine enable inventory-digest --runner grok-bot --remote
python3 hub.py routine enable inventory-digest --runner dots --remote
python3 hub.py routine enable inventory-digest --runner muse --remote
python3 hub.py routine enable inventory-digest --runner codex --remote
python3 hub.py routine enable inventory-digest --runner claude-code --remote
python3 hub.py routine enable inventory-digest --runner mausbot --remote
```

Each `--remote` call prints a JSON plan only. It refuses `--write`, disable, and
local scheduler runners. It writes no configuration, enablement record, hosted
skill or schedule. The local definition must be valid and listed in the manifest,
and the owner must already have published the routine and prompt to the remote
catalog; previewing does not publish. See [remote publication](remote.md#publish).
A missing remote routine stops execution.

Use `plan.activation_prompt` verbatim in the target platform. It contains the
exact schedule, timezone, owner, overlap, timeout and instruction. Each run
discovers the remote tools, calls `hub_get_item` with
`{"kind":"routine","name":"inventory-digest"}`, reads the definition and its
prompt, and stops if an approved field changed (including runner and prompt
path), content is omitted or truncated, or tools are missing. The prompt is
read from the catalog on every run, so publication is the owner's update
boundary. Task permissions still apply to changed prompt content. Portable
`enabled=false` is not a prohibition on a separately approved hosted schedule,
and the definition is never edited.

- **T3:** pass the returned `plan.payload` to `schedule_task` after review.
  Supports interval, fixed-time and webhook; cron needs translation. Verify the
  app timezone and unattended MCP access. T3 remote OAuth is **UNCONFIRMED**.
  A bounded native manual-trigger trial using local CLI wrappers and stdio MCP
  passed; it does not establish remote OAuth, automatic interval firing or
  native overlap/timeout enforcement. See the [execution record](release.md#recorded-cross-agent-staging-and-routine-execution).
- **Grok Bot:** paste `activation_prompt` into the owning bot. Review Instruction
  and When to run under Details > Routines. Pause, resume and edit there. A hosted
  skill is optional; if used, enable it for the intended bot. [Routines guide](https://cursor.com/docs/grok-bot/work).
- **Dots:** paste `activation_prompt` into the Dot conversation and review the task
  under Recent activity or ChatGPT Scheduled. Custom MCP availability in that Dot
  and a creation API are **UNCONFIRMED**. Require a successful catalog read before enabling.
- **Muse:** paste `activation_prompt` as a conditional capability check. Hosted
  recurring schedules, custom MCP OAuth and a creation API are **UNCONFIRMED**
  (see [remote.md](remote.md#connect-a-client)). If unavailable, report the gap
  and use a local runner with authenticated Muse Code.
- **Codex:** create and review an automation with the scheduled instruction and
  timing in the chosen workspace. Verify remote MCP access there; CLI OAuth does
  not prove cloud or unattended access. The installed CLI's `exec` command runs
  a task; it does not install a durable app schedule. A T3-triggered CLI run is
  recorded separately from app scheduling. See the current
  [scheduled-task documentation](https://learn.chatgpt.com/docs/automations?surface=app).
- **Claude:** `claude-code` prints a remote-fetch instruction and creates no
  scheduled skill file. Choose explicitly between a local Desktop scheduled task
  and a cloud `/schedule` task, then configure timing and catalog access there.
  The cloud path's one-hour minimum cannot honor a five-minute routine. A
  connector alone does not create a schedule; a controlled local runner invoking
  the authenticated CLI is another execution path.
- **MausBot:** paste the instruction into the bot or computer's routine editor and
  review timing. Engine grants and unattended OAuth must be checked there; the
  creation API and custom remote MCP setup are **UNCONFIRMED**.
- **Local cron, systemd, launchd, Task Scheduler:** available without `--remote`,
  with preview first and explicit writes. They pin a local prompt and call the
  selected authenticated CLI. Configure that CLI's remote MCP separately if the task needs catalog tools.

Before enabling anywhere, pause copies on other runners. Confirm one owner, one
active runner, timezone, overlap, timeout and account access. If a platform cannot
honor these controls, report the gap and choose a supported runner rather than
relaxing them. `routine status` cannot prove hosted activation or detect duplicate
schedules in another account. Pause or delete hosted work in its own platform;
the catalog stays read-only.
