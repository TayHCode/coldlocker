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
  "source": "~/.claude/skills/architect",
  "family": "p3-stack",
  "snapshot_date": "2026-10-29",
  "source_sha256": "2d163c3a4c4d29924eea0d0541b93ac8f2d6cb10d54ca04d5ab968130b13eab1",
  "source_files": {
    "SKILL.md": "19e3691d9f84f4172352fe4f621e2627cf02be93f3a294f1cc596079fbcda557",
    "references/design-red-flags.md": "d0e639e4cc588ec865f930d3a4111faad60987ae36925917e319d7ceca7c69ce",
    "references/rationale-template.md": "0cedfccdc7b47fc881febd5a8293eecbc542c20f8df825639904a8102d2eb5d3",
    "references/runner-prompt.md": "fef73080b352805f07c859e9f3cd412bcf687255d1533dc8babb1d0dbd381f49"
  },
  "adapted_files": [
    "SKILL.md",
    "references/runner-prompt.md"
  ],
  "added_files": [
    "LICENSE",
    "PACKAGE.md"
  ],
  "adaptation": "Portable host policy, home-relative references, project-selected verification host. Source support files otherwise retained. Corrected relative rationale-template link. Routing selection and effort defer exclusively to the shared model-routing reference; local maps, inheritance fallbacks and model-family prescriptions removed."
}
```
