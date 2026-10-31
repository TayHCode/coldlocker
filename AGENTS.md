# Coldlocker agent instructions

Every agent must read [the model routing rule](rules/model-routing.md) before
starting work, including its permanent 2026-10-31 policy and subagent restrictions.
Read the owner’s live ColdStack BRIEFING.md and models.yaml for execution targets.
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
See docs/grok-bot-bootstrap.md for hosted bot setup; catalog discovery alone
does not prove activation or authentication.

Inside an agent sandbox `hub.py check` is the gate. If sandbox restrictions
prevent localhost sockets, ps or crontab, run the full unittest suite outside
the sandbox and report the restriction and the checks actually executed.
