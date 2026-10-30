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
  "source": "~/.claude/skills/p3-mode",
  "family": "p3-stack",
  "snapshot_date": "2026-10-29",
  "source_sha256": "85810f9a0bd055cd01e8fadc1e0d3e86e7f702cd67cad8ba542ba8d4c42e6de0",
  "source_files": {
    "SKILL.md": "7e5b05ca317fd04878a6beabf2085be0ada7b5ae6fc8ee6344958b35ab64fe2b",
    "SKILL.md.bak-agentfleet-20261026T151153.010801Z": "804902c52be09d48429a4e67f95d4243fce6b198dea2012ba41d1fe0f1622cba",
    "playbooks/authoring-a-skill.md": "f42bb8667748e9e24681fe29d743e7fb524fac5d65ac359febea975e83fba241",
    "playbooks/autonomous-run.md": "508f7ef4313f8159f85b2387492d4dced9dd8456c44dfe7d17a8294fe42f4894",
    "playbooks/autopilot-full.md": "7768de572185a6f7b6eecc3d71233d78c9e4cf8520beb66bebe755a369b22e78",
    "playbooks/autopilot-stack.md": "d346275ff82d04760698be43dd882f4a76b4b469f70d011029b586f83e5767b5",
    "playbooks/babysit.md": "19cf1d07209031a5535f82e0cab297abf4fe55a1020e70340485671eab25f9d4",
    "playbooks/bug-fix.md": "05512bdb654aec93c7930ea52ded1053ba3d3b73ffc2250880be14d24fa1bb3b",
    "playbooks/eval.md": "af8f44c67f402b671b91095121ecba3d644fef9c46e773d78d3e1f6f01003dde",
    "playbooks/feature.md": "099a4c4a68658a5f2549f054179dac8a07f6ba9adc18b6b09fd64dd82ad8cfe1",
    "playbooks/hillclimb.md": "3aa3dc6fc79ea0b7b5e3d172734211a58cbea310748d4bb90dd1d1188b108a2f",
    "playbooks/investigation.md": "dc66bcf6af3a074fc7f67aa5e12b93ef571a4fb4d8e17f2f1af5ae5359f42e46",
    "playbooks/multi-phase-plan.md": "a9fc81a28ecd304cdbe65598476b6a13653d0428b21b387b44f451a4132a1a59",
    "playbooks/opening-a-pr.md": "471691c65781694fe56aafbbb6489846ad3cf88a06d0d2f70642110a8b1b8b69",
    "playbooks/orchestrate.md": "ae1f7ecb89b3a5151d156b60a87feabe00ae7efa6fb2ffd1e9780a84be8123b4",
    "playbooks/pause-safely.md": "63b213adcf2e5439c8e0c97098968e654c0ea733611e979011a23a30a0d84119",
    "playbooks/perf-issue.md": "fafb19fa3027a69dd888b869cea23cc5c829bd77dc1de22485e4b7434160900c",
    "playbooks/prototype.md": "cea104f1424532d11832745c566ce9e02fd693aee3a8695e2e968921c3d58ae5",
    "playbooks/refactoring.md": "b20737d443e99e544be26ff7ce79694d42a4432946d4382ece87ad3ee7de5236",
    "playbooks/runtime-forensics.md": "a711a78bea94b6a55d86793cb987fdaa3e5bef27b6e8cb5a269ca64d01fee516",
    "playbooks/session-pickup.md": "2dea20f12055d616e11d5c8481b3da65e5eecd3ff8c24e6cba72f03c3215008a",
    "playbooks/shipping.md": "42696ce4dc81fcf6c9e4f37e1a99fbeca3d76ac4945aebe2f24bb8c3be033eb3",
    "playbooks/trace-forensics.md": "187991a0a9bfbe4188aa92ef04d1d08b8dd1c440d4b151eac32448b61dd802f3",
    "playbooks/visual-parity.md": "784b982c90d1aaa7130f692b53a3d2864c56ad2143201342d088c6894274437c",
    "playbooks/worktree-cleanup.md": "3360c573c288488f68cbded7a14c1a00dbfb00652c1b1a45c9b368f5e1e8e99e",
    "references/bugbot-triage.md": "829277d9d4f770c5563b273321f49a94a6a8cc7093c555b822b1faa11d298f21"
  },
  "adapted_files": [
    "SKILL.md",
    "playbooks/autopilot-full.md",
    "playbooks/autopilot-stack.md",
    "playbooks/bug-fix.md",
    "playbooks/eval.md",
    "playbooks/feature.md",
    "playbooks/hillclimb.md",
    "playbooks/multi-phase-plan.md",
    "playbooks/orchestrate.md",
    "playbooks/perf-issue.md",
    "playbooks/refactoring.md",
    "playbooks/shipping.md"
  ],
  "added_files": [
    "LICENSE",
    "agents/p3-agent.md",
    "agents/comment-sicko.md",
    "PACKAGE.md"
  ],
  "adaptation": "Portable host policy, home-relative references, project-selected verification host. Source support files otherwise retained. Routing selection and effort defer exclusively to the shared model-routing reference; local maps, inheritance fallbacks and model-family prescriptions removed.",
  "excluded_files": [
    "SKILL.md.bak-agentfleet-20261026T151153.010801Z"
  ],
  "support_source": "~/.agents/agents"
}
```
