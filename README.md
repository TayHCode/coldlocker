# agentlocker

One place to share skills and routine instructions across your AI agents. Scan what
your agents already have, import it into a private local catalog, and connect each
agent to that catalog over MCP. Setup needs Python and Git; no Python packages to install.
MIT licensed; see [LICENSE](LICENSE).

Release status: unreleased. Local catalog acceptance is recorded; the broader
[cross-agent product journey](docs/product-acceptance.md) remains to be proven.
See the [evidence and release gates](docs/release.md).

To share the library with hosted bots as well as local agents, follow the
[self-host setup](docs/self-host.md). Existing Composio MCPs stay in Composio.

## Quickstart

Use a permanent source directory and Python 3.11+ (Windows: Python 3.12.4+;
use `py -3` instead of `python3`). Get the approved source using the
[install instructions](docs/install.md).

1. **Check and import your library.** The scan finds existing agent content;
   import lets you choose what to copy into the private catalog.

   ```sh
   python3 hub.py doctor
   python3 hub.py scan --import
   python3 hub.py list
   ```

2. **Connect your local agents.** Review the printed entries, then confirm
   the write. Existing settings are preserved; changed files are backed up.

   ```sh
   python3 hub.py connect --agent claude codex
   python3 hub.py connect --agent claude codex --write
   ```

   For Cursor, OpenCode and other local clients, use the
   [client instructions](docs/clients.md).

3. **Use the library.** Ask a connected agent:

   > Find a skill in agentlocker, read its supporting files, and use it for my task.
   > List the routines and read one when I ask you to follow it.

   To reach the same library from a hosted bot, complete the one-time
   [self-host setup](docs/self-host.md), publish it, and connect the bot to your
   catalog URL. Claude Code, Codex CLI and Grok Bot have recorded acceptance
   evidence; other clients have their own support limits.

Edit the original item and re-import it to update the local library. Publish
again to update hosted readers. Each agent fetches the current content rather
than maintaining a separate substantive copy.

For a skill imported by the Claude scan, preserve its recorded origin when
updating it:

```sh
python3 hub.py import-skill <SKILL_DIRECTORY> --origin scan-claude
```

Use the entry's recorded owner and origin for other sources. The CLI rejects
an update with different provenance and names the required origin.

## Imports and links

Shipped entries are indexed in [manifest.json](manifest.json). The always-verify package includes
`principle-prove-it-works`, `create-verification-skill`,
`maintain-verification-skill`, and `principle-test-behavior-not-implementation`
alongside `evidence-before-done`. Each imported pstack skill includes its MIT
license and provenance; creation includes the feature-map examples. Fetch a
package with `hub_get_item` using `kind: "skill"` and its name. To avoid reading
the whole list first, call `hub_search_skills` with a plain-language request; if
it returns nothing useful, call `hub_list_skills`. See
[catalog search](docs/catalog-search.md).

In the manual client examples, `<HUB_DIR>` is your permanent installation directory.

Single items: `python3 hub.py import-skill <path>`, `python3 hub.py import-rule <path.md>`,
`python3 hub.py import-routine <file.json>`, `python3 hub.py import-mcp <file.json>`;
options `--owner O`, `--origin L`, `--name N`. Definitions use `secret://<name>` references, never values
([MCP auth](docs/mcp-auth.md)). Preview with
`python3 hub.py link --target <root> --dry-run`, then run without `--dry-run`.
`python3 hub.py remove <kind> <name> --yes` removes an import; re-importing a
changed source keeps the old copy in `.hub-store/history/`.

## Routines

A routine is saved instructions for recurring work, with a suggested schedule.
Import it once; ask any connected agent to fetch it and follow its current
prompt. Importing does not turn on a timer.

Automatic timing is optional and belongs to the chosen runner. See
[routine scheduling](docs/routines.md) when you need it. Existing Composio
connections can stay as they are. Optional [skill links](docs/reference.md) and
the [shared-tools gateway](docs/gateway.md) are separate from this setup.

Contributors must run `python3 hub.py check` and the Python test suite.
The installation's `doctor` command does not replace maintainer source checks.

## Auto-pick PreToolUse hook

`hooks/auto_pick.py` is a Claude Code PreToolUse hook for `AskUserQuestion`. When
every question has exactly one option marked `(Recommended)`, it refuses the
question and tells the agent to choose that option and continue. Questions that
match a keyword list for spending money, publishing, deleting or messaging people
still reach you. The list is a best-effort filter, so the refusal also tells the
agent to ask in plain text when its choice does one of those things. Add it
to `~/.claude/settings.json`:

```json
{"hooks": {"PreToolUse": [{"matcher": "AskUserQuestion", "hooks": [
  {"type": "command", "command": "python3 -I /path/to/agentlocker/hooks/auto_pick.py", "timeout": 10}]}]}}
```

Codex asks in plain text, so this hook does not apply there.

## Drift-check Stop hook

`hooks/drift_check.py` is a Stop and SubagentStop hook. When the branch has changes
since `origin/HEAD`, including uncommitted and untracked files, it reviews the
latest user request and diff for drift, unnecessary complexity and KISS.

The default resolves `reviewer` through `cold model get` in `~/projects/agentfleet`
on each check, excluding every observed builder model. Builder identities come
from the hook payload's `model`, Claude assistant messages and Codex turn contexts.
For transcripts without model metadata, set `AGENTLOCKER_DRIFT_BUILDER_MODELS`
to the actual builder IDs separated by spaces. Include any builders whose work
is absent from the transcript. Missing identities or routing are INCOMPLETE.
The resolved Codex model and effort run with a read-only sandbox. Other providers
are INCOMPLETE until a supported adapter exists.

One review normally suffices. The same goal, diff, route, builders and command
reuse their result. Changed work after real findings gets another review, with
no round limit or automatic approval. A changed goal or reviewer starts a new
review. Route resolution still runs before reuse, so routing failure blocks even
previously approved work.

Add it to your host's supported Stop and SubagentStop configuration. This Claude
example only documents registration and does not install it:

```json
{"hooks": {"Stop": [{"hooks": [{"type": "command", "timeout": 180,
  "command": "python3 -I /path/to/agentlocker/hooks/drift_check.py"}]}],
  "SubagentStop": [{"hooks": [{"type": "command", "timeout": 180,
  "command": "python3 -I /path/to/agentlocker/hooks/drift_check.py"}]}]}}
```

Existing host adapters can set `AGENTLOCKER_DRIFT_CMD` to an argument-parsed
command. It receives the prompt on stdin, `AGENTLOCKER_DRIFT_ROUTE` as JSON and
`AGENTLOCKER_DRIFT_BUILDER_MODELS` as space-separated exclusions. It must invoke
the resolved provider, model and effort. It prints exactly `OK`, or `DRIFT` on
the first line followed by specific findings. The former
`AGENTLOCKER_DRIFT_MODEL` shortcut remains unsupported.

Missing tools, launch errors, timeouts, nonzero exits, invalid verdicts and empty
DRIFT findings block as INCOMPLETE. Reports omit raw provider output and command
arguments. Routing has a 10-second deadline. Reviewer execution has a 120-second
deadline. On POSIX it kills the reviewer process group. Wrappers must keep
provider children in that group. Windows uses `taskkill /T` with a further
10-second deadline. Keep the host timeout above those deadlines, as in the
180-second example. The [model-routing reference](rules/model-routing.md) owns
the shared routing instructions.

## Keep-going Stop hook

`hooks/keep_going.py` is a Stop hook for Claude Code and Codex. After a turn that
used tools, it asks the agent to continue unless its final message ends with
`Goal:`, `Done condition:`, `Evidence:`, `Remaining:` and `Status: DONE` (with
`Remaining: none`) or `Status: BLOCKED`. `Status: PARTIAL` is never accepted.
Chat-only turns are not affected.

`python3 hooks/install.py` prints a safe summary of the change to
`~/.claude/settings.json` and `~/.codex/hooks.json`; add `--write` to back up
and merge it, keeping other hooks.
Codex may ask you to review the new hook before it runs. The hook expects a
status block, so don't install it where replies must be strict JSON. After moving
the checkout, remove the old Stop entry before reinstalling.

For pinned pull updates, read [coldlocker sync](docs/coldlocker-sync.md). It previews
by default and includes private receipts, backups and rollback.

## Remote catalog

The optional Cloudflare Worker serves an owner-only, read-only catalog for
hosted bots. Set your deployment's `public_url` using the [self-host guide](docs/self-host.md);
each client needs its own OAuth grant. Source merges do not deploy the Worker
or publish local imports. Deployment and real-client acceptance have separate
[release gates](docs/release.md#release-gates). See [remote catalog](docs/remote.md),
[Composio](docs/composio.md) and the [Grok Bot bridge](docs/grok-bot-bootstrap.md).

## Limitations

The local catalog does not activate hosted routines or deploy the remote
catalog. Each hosted client needs its own setup and authorization. The broader
cross-agent journey remains subject to the acceptance checks linked above.

A public source export excludes private maintainer configuration, workflow
files, local state, and unreviewed site screenshots. The full maintainer suite
runs in the private checkout. For the exported catalog, verify local health
and the safety and MCP endpoint behavior with:

```sh
python3 hub.py doctor
python3 -B -m unittest discover -s tests -p test_safety.py -v
python3 -B -m unittest discover -s tests -p test_endpoint.py -v
```

These checks do not approve publication or replace dependency-license review.

## Reference

The catalog includes the official [frontend-design](skills/frontend-design/SKILL.md)
skill and its [design QA checklist](skills/frontend-design/references/design-qa.md).
The optional [UI UX Pro Max](skills/ui-ux-pro-max/SKILL.md) companion supplies
searchable UX guidance. Fetch either with `hub_get_item` using `kind: "skill"`.
Their upstream Apache-2.0 and MIT licenses remain with each package. See the
[import and verification receipt](docs/evidence/coldlocker-design-package.md).

- [Overview](docs/OVERVIEW.md) and [vision](VISION.md); the MCP endpoint is `mcp_endpoint.py`
- [Install details](docs/install.md), [upgrade guide](docs/upgrade.md), [commands](docs/cli.md), [imports and links](docs/reference.md), [file formats](docs/formats.md)
- [Guided import](docs/IMPORT-PROMPT.md), [scanner](docs/scan.md), [safety scan](docs/safety.md), [wizard](docs/ui.md), [client setup](docs/clients.md)
- [Gateway](docs/gateway.md), [gateway research](docs/mcp-gateway.md), [Executor](docs/executor-connector.md), [agents sync](docs/agents-sync.md)
- [Related projects](docs/related.md), [factory overlap](docs/factory-overlap.md), [hosted catalog](docs/hosted-later.md), [acceptance](docs/acceptance.md)
- [Release candidate and evidence](docs/release.md)
- [Changelog](CHANGELOG.md) and [security reporting](SECURITY.md)
