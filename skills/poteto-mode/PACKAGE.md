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
  "source": "~/.claude/skills/poteto-mode",
  "family": "pstack",
  "snapshot_date": "2026-10-29",
  "source_sha256": "550d2dc047762a30762b9f3a2a4a477af77c5b746e32f5a5a0460edc0ca02533",
  "source_files": {
    "SKILL.md": "2044530ee5bd4efbf821ad059cac79b8ea75655560e3da86e03a3539a26a807f",
    "playbooks/authoring-a-skill.md": "f6a13a445c360b48d6cd72780ac01955f45b82826476efe0e67c7559329407aa",
    "playbooks/autonomous-run.md": "6ac974fd2b09748add56493e89f578a35a6a9289e1026515878d81ef3cf129d1",
    "playbooks/autopilot-full.md": "acf4f4a58c11045f9d3e8807d907a877e61ca6432b079f1087d7d30fc931566f",
    "playbooks/autopilot-stack.md": "c6bcb18b2ce605e77a2a2225854590a4b9a687911f79331746d91a4fe9a52e60",
    "playbooks/babysit.md": "e261c1ff5581e0f8fc6c686f34de4b535f87473e77beabea7dd8323021214610",
    "playbooks/bug-fix.md": "f90aff55949cf9f7a371961c0231516bc3558587afcec1d7e5652256e4492b0c",
    "playbooks/eval.md": "a93d449a908236afa6645179651844094b1547dbab4e484cffcdda630e4b90bf",
    "playbooks/feature.md": "3af9735f3228a9dc6015ed57aec9f6ddb629ebab3dff91ffd13b4d7a1658c780",
    "playbooks/hillclimb.md": "9a12abd94aa5165a5a59ff659ca54ccab634da14e8e31c7dc83d06c73fdb2a62",
    "playbooks/investigation.md": "dc66bcf6af3a074fc7f67aa5e12b93ef571a4fb4d8e17f2f1af5ae5359f42e46",
    "playbooks/multi-phase-plan.md": "a184e9fe545204f778338ed4053595e5e08e724116d41388c156e1be8f455696",
    "playbooks/opening-a-pr.md": "a5aef646de8b651306c2d10974623650e4a013a245aec1c00525bbe0e31bfa76",
    "playbooks/orchestrate.md": "08dce6c9e7be1d13bc6944ab9b51665f935c531437f5e088bc53d2a62099ef05",
    "playbooks/pause-safely.md": "77968cfe5f4a71a8cd33e0ae8ffcdbd88dd693a212c240457773e6c40c96c21d",
    "playbooks/perf-issue.md": "45e4303f3b6f951abfe6ff5cabf381d5ad0358964a5ee6fb6f5d0140386e9f2b",
    "playbooks/prototype.md": "7aa1f52c691c3d46d5afc527b23f90977fce869ce33573c73c81a4c7f0cde962",
    "playbooks/refactoring.md": "14f4e223d0a6d052837c8674b2f80d03668f9df34d8ed549101c8b6d148a0219",
    "playbooks/runtime-forensics.md": "3463372b578c28f27c236f2627f09b5c5b1e85e86db7399fb984f3892fb450f5",
    "playbooks/session-pickup.md": "d194f2a7ef392c1598ef3f0e3b561370ca25c2c7a78308007af4aa46ada8ee12",
    "playbooks/shipping.md": "bf56339bdf7e0223bcff5abf4e8599e3f5b547b3fcc3b4ce9475e6e7dda14a9f",
    "playbooks/trace-forensics.md": "ef0bbf79856a4ae1a8993d54c1a1da159cb97623941b652c0069cf75a017e0f5",
    "playbooks/visual-parity.md": "950ca51999a6454c98eee6bcbc3d1bbe501a4e3a5d5439b6f6ddc378991b256c",
    "playbooks/worktree-cleanup.md": "be04ae0afe19d4a4da5bcc4e94455f68dbebd1cda6eb0f456ca0d358ecf76c00",
    "references/bugbot-triage.md": "b2d146c770d8d3593e1ef3d77b668039b8e1f78093ea1a86f1d45d7f99614309",
    "scripts/bootstrap.ts": "ccd2ed08fd9da9d0d942e5f2491cfc74bb92d8a08fa51755e0da1ac8de287361",
    "scripts/bun.lock": "667d9cf7222e9aae3fa42a4be383e657784216e9df115da02227fbc5ac7bbe46",
    "scripts/check-plan.mjs": "e58c3d871b09d477be00628d69dd23ffa6c2e3a1728f5b867a841baa8e90278b",
    "scripts/check-plan.test.ts": "c80f3ea043b81f219b92c4c8a3af6bdd4f8d32ea596ec15b5b9e0252645f2ae8",
    "scripts/orch/orch.test.ts": "5e80b416bdcd68e4067690ce857345852fd2e0db3dcf3e25607999cd55b963d2",
    "scripts/orch/orch.ts": "47c92c57e8276616d8de94665a126bd832e748036afa8d0ffdbacb7946decb5c",
    "scripts/orch/store.ts": "558cfda533e1ebe7c8d7842604f07e0be7618759cf129b4c2397a8e29516ecea",
    "scripts/package.json": "fd511a0c01f522e0c781ddd8eccea0e91697a0856c6e6a3261de78a6a0287c72",
    "scripts/watch-pr/cli.test.ts": "a35592b4c6da96caf8d47987e9cd503fab6c7774ab87d11a43f24b4b5df2a9d1",
    "scripts/watch-pr/cli.ts": "89c08863089181a232be782c4b2ed90a1552f44aeecb0dcb7aac2c623620e680",
    "scripts/watch-pr/fakes.test-helper.ts": "cf0c5ee61b4a73444c1b775cfb6c81a40d312e055dd21a1f5eae6276e54de13f",
    "scripts/watch-pr/github.test.ts": "70c9f03d8738228763846f2abdd8c7de7be68dba3035041482386a69cbf5639b",
    "scripts/watch-pr/github.ts": "a605b8531468dbe4c07b93663c0993a9c11dc7e9179adde970f5c740396451d9",
    "scripts/watch-pr/policy.test.ts": "146acabcfee9b6fa3342358eb0a9504aa65fd081bbc15b10adf9c1c5485067d2",
    "scripts/watch-pr/policy.ts": "f2a3e31223d05edc05e5473cc3f8f3cb9bd20c202c30c6abe7f21f0f7aee8fbb",
    "scripts/watch-pr/render.ts": "2b709e86628d5b8fdb61f860e60ababc342b0626c67576305442cf548283a604",
    "scripts/watch-pr/tsconfig.json": "1ecccc72320b086c63ce3f73c959896fd747363e63553b3b1136296ea4069e45",
    "scripts/watch-pr/types.compile.ts": "621af4c9a664fbf699a9737efccdc248abb416df8819c9dc5f824beae2b9b1d6",
    "scripts/watch-pr/types.ts": "0cac435294dc85ce1ba755e8e67f6c5ab163dfa252f2722d6e3a2d68c58fec59",
    "scripts/watch-pr/watch-pr": "d955603be6cc0e8b8ffcec722f635192b2261410b1f2929abea94480e47eb5d4",
    "scripts/worktree-audit.sh": "e0a94e5e5b1ff60638916e440600e15851f7b79bc1a1d1f602fde05d465e2ec0"
  },
  "adapted_files": [
    "SKILL.md",
    "playbooks/autonomous-run.md",
    "playbooks/autopilot-full.md",
    "playbooks/autopilot-stack.md",
    "playbooks/bug-fix.md",
    "playbooks/eval.md",
    "playbooks/multi-phase-plan.md",
    "playbooks/orchestrate.md",
    "playbooks/runtime-forensics.md",
    "playbooks/session-pickup.md",
    "playbooks/shipping.md",
    "playbooks/trace-forensics.md",
    "playbooks/worktree-cleanup.md",
    "scripts/check-plan.mjs",
    "scripts/check-plan.test.ts",
    "scripts/orch/orch.test.ts"
  ],
  "added_files": [
    "LICENSE",
    "PACKAGE.md"
  ],
  "adaptation": "Portable host policy, home-relative references, project-selected verification host. Source support files otherwise retained. Routing selection and effort defer exclusively to the shared model-routing reference; local maps, inheritance fallbacks and model-family prescriptions removed.",
  "upstream": {
    "repository": "https://github.com/cursor/plugins",
    "version": "0.15.27"
  }
}
```
