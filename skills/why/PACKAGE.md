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
  "source": "~/.claude/skills/why",
  "family": "p3-stack",
  "snapshot_date": "2026-10-29",
  "source_sha256": "4085c837fa41966058bdb3aa67317763b5a61d25f4e198a5cd761c274747d74b",
  "source_files": {
    "SKILL.md": "a9bdf1092bda14777725e3999a2e385844d859fff8f9706b3de5641ed91d0370",
    "references/epistemics.md": "fcc0f559c072832c869a5ada72434b4849320a637ae63c09e8ee14f7ad787059",
    "references/investigator-prompt.md": "bfc4a1f1e02e8aa4eb408ebee0ee2c5cc610a5baa802028d0ac0971625bb7d37",
    "references/source-playbook.md": "ca1e5636bdc3ebbec442d50260a47824ec3f1b83a66bd833c0ec2d2eb88db8e5",
    "references/sources/code-archaeology.md": "8c14aa382f0de40829b2668d43e3f54814480bb93a486c9282ada4003f6b9dbd",
    "references/sources/databricks.md": "236ede7aab6454662a992db088a5393537a5233a4c26719c93188f9e174488f6",
    "references/sources/datadog.md": "ebe9c07bac28e47757ee7f16fb93b0c01d427a08c501957d047f395a2f37b7e7",
    "references/sources/incident-postmortem.md": "a899470f05f86029630a13a2696491cc708ca7b56a6e822a69ff6e954d6ff8cf",
    "references/sources/linear.md": "01304e7f1badfcfb78babbb0edcadeb3cc8dd3f713793420cdff24ba190f907c",
    "references/sources/notion.md": "3bcfa695c49dfad4ac036d5d7181c12585f55ac04f1936198d16ab75a2788997",
    "references/sources/sentry.md": "6f292bc4fb1548e956f95e1900b9481abf5134254419f1cd9edec95428c82d73",
    "references/sources/slack.md": "58fee61d024b3700b407e7acbed367944720ff05adfddb6954d36c88368f3ba7",
    "references/synthesizer-prompt.md": "6931642f5587c67b4ec48c4fd432186cba3e30dc120d757c37fb41155dfe9344"
  },
  "adapted_files": [
    "SKILL.md"
  ],
  "added_files": [
    "LICENSE",
    "PACKAGE.md"
  ],
  "adaptation": "Portable host policy, home-relative references, project-selected verification host. Source support files otherwise retained. Routing selection and effort defer exclusively to the shared model-routing reference; local maps, inheritance fallbacks and model-family prescriptions removed."
}
```
