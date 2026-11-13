# Coldlocker agent instructions

When the optional policy pack is installed, read [the model routing rule](rules/model-routing.md)
before starting work, including its configurable role defaults and subagent
restrictions, and follow the owner rules it names.
Do not change live routing to implement the builder proposal.

Use README.md for project context and VISION.md when the developer checkout
includes it. Catalog entries live in manifest.json: skills use
skills/<name>/SKILL.md and rules use rules/. Read docs/formats.md before changing
entry formats. The hub_list_* tools discover entries through mcp_endpoint.py;
load relevant entries before applying them.

Follow [the import prompt](docs/IMPORT-PROMPT.md) for inventory and approved
imports. Keep credentials in the private store behind secret:// references.
Keep imported routines enabled=false with one owner and one runner. Use
`python3 hub.py link` for approved links and `python3 hub.py check` to validate.
See docs/hosted-bot-bootstrap.md for hosted bot setup; catalog discovery alone
does not prove activation or authentication.

`python3 tools/context_budget.py` measures what Coldlocker injects (hook
context per client and the MCP tool list) against `context-budget.json`; CI
runs the same command. After a change to injected text, run it with `--write`
and commit the baseline. Growth needs the `agent-context-increase` PR label.
Hook context is capped at 10,000 characters, because Claude Code spills larger
hook output to a file. Tool descriptions are capped at 4,096 characters, where
Claude Code cuts them off. The same command runs the real hook with 8,000
characters of lessons (the hook's lesson allowance) and fails when session or
subagent start would exceed 9,600 characters, which leaves room for the
chained-output pointer. With `COLDLOCKER_LESSONS` set it also fails when the hook
would cut those lessons or cannot read them.

In a private maintainer checkout containing `tools/guard_replay.py`, run `python3 tools/guard_replay.py` to replay the scrubbed corpus.
Denials may only decrease; a rise needs a fix, never a higher baseline. Public exports omit this maintainer check.

Inside an agent sandbox `hub.py check` is the gate. If sandbox restrictions
prevent localhost sockets, ps or crontab, run the full unittest suite outside
the sandbox and report the restriction and the checks actually executed.
