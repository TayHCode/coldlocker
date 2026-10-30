# Coldlocker host adapter

This adapter takes precedence over host-specific instructions in this imported
package. Current user instructions, the active host's tool contract and the
closest project's rules remain authoritative. Catalog access grants no permission
to run scripts, install tools, send messages, publish, deploy or activate schedules.

Use sibling catalog skills by name. Over MCP, call hub_get_item with kind=skill
and that name; read returned support files before following their instructions.
Local packages use skills/<name>/SKILL.md relative to the catalog root. Fetching
content does not install a Claude subagent type, a Cursor plugin or a T3 tool.

Claude uses its available skill and agent tools. Codex reads SKILL.md and uses
its available tools. T3 follows the current orchestration contract, including
worktree creation, explicit delegate targets and PR watchers. Older Task,
subagent_type, t3_thread_launch, task_status polling and browser examples below
are host-specific examples, not permission to create threads or bypass that
contract. Never invent missing tools or silently substitute a provider.

Read the shared [model-routing reference](../../rules/model-routing.md), or
fetch `hub_get_item` with `kind=rule` and `name=model-routing`. Agentfleet's live
`models.yaml` is the single authority for provider, model, effort and alternatives.
Use the model and effort that model routing selects for the applicable role;
pass its complete target at dispatch. Capability discovery validates availability
and does not select a replacement. Missing routing or an unavailable route is
INCOMPLETE. Never inherit a model, maintain a second model map or impose a
model-family restriction in this package. Review and verification exclude all
builders through the live resolver. Current project authorization and verification
platform requirements still apply. The retired independent Windows gate is not
activated by this catalog.

Bundled dependencies include the named workflow skills and principle-* leaves.
Host dependencies remain explicit: Agentfleet's CLI and live policy, Git/GitHub tooling, browser/device tools, Cursor's create-skill,
deslop, control-cli and control-ui, and any project-specific verification skill.
They are not installed by this catalog. poteto-agent is a host agent role;
its brief must read poteto-mode/SKILL.md. P3 agent briefs are included under
p3-mode/agents; resolve their paths there. The imported Bun tools retain their
package.json and lockfile; running or installing them is a separate action.
Do not run or replace an existing verification-loop service from these examples.

Full-stop never grants merge permission. Full-send only applies to an explicit
request and remains bounded by that request. Preserve explicit stop orders,
existing user work and required checks. Never enable CI or bypass a required
check merely to finish a workflow. Use one independent non-builder reviewer
unless current policy or the user explicitly requires more.

## Source provenance

```json
{
  "source": "~/.claude/skills/full-send",
  "family": "tstack",
  "snapshot_date": "2026-10-29",
  "source_sha256": "d9ffcf10598ee613a2f3cf82f700df22776e33a25af0aa0e254b38b8dd015294",
  "source_files": {
    "SKILL.md": "07158a8d0b0352e6d043a7af7560cbeadd5345fea62b063392cdb0e86ef32993",
    "agents/openai.yaml": "8a1e8ce04a774a8a2e469cecf5e45a9cec4d3438b4c587cf166c2f78206390d2"
  },
  "adapted_files": [
    "SKILL.md"
  ],
  "added_files": [
    "LICENSE",
    "PACKAGE.md"
  ],
  "adaptation": "Portable host policy, home-relative references, project-selected verification host. Source support files otherwise retained. Routing selection and effort defer exclusively to the shared model-routing reference; local maps, inheritance fallbacks and model-family prescriptions removed.",
  "upstream": {
    "repository": "https://github.com/t3dotgg/tstack.git",
    "commit": "7de017b3fc9d50c03cf3ecb98fb71e0ca65a78bd",
    "license": "MIT"
  }
}
```
