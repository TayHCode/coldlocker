# Model routing reference

The owner's live `models.yaml` is the single source for provider, model ID,
effort and ordered alternatives. Read the live routing map through the `cold`
CLI on PATH before selecting a model. The repository owner configures these
defaults for their installation; the live map owns execution targets.
Do not copy pinned IDs, effort settings or fallback lists into consuming skills.
If the live source or a valid route is unavailable, report INCOMPLETE.

## Roles in plain language

Use the model and effort that model routing selects for the applicable role.
Skills, rules and hooks must name roles, not assign models or effort themselves.
This applies to every invocation, including building, review and local work.

| Task | Role to resolve |
| --- | --- |
| Quality-sensitive or uncertain implementation and project ownership | `builder-default`, `coordinator`, `subagent` or `second-frontier` |
| Known-easy code or routine CI diagnosis | `builder-cheap` or `ci` |
| Independent review and verification | `reviewer` or `auditor/verifier`, excluding every builder |
| Trivial edits, cleanup, status watching or mechanical work | `builder-trivial`, `cleanup`, `pr-babysitter` or `bulk-codex` |
| Small fast builds or coordination of a known plan | `builder-fast`, `swarm-worker` or `orchestrator` |
| Justified difficult implementation or UI work | `builder-hard` or `builder-ui` |
| Collection, documentation from established facts or vault organization | `bulk`, `learning-collector`, `docs` or `vault` |
| High-stakes audit or high-risk verification | `auditor-high-stakes` or `gate-high-risk`, with the evidence required by the live policy |
| Local model work | `local`, using the live route and its execution prerequisites |

Changing a route must not require editing a consuming skill or hook. The same
authority selects local models and enforces any per-repository launch limits.

## Resolve at dispatch

1. Choose the role by the task. Run `cold model get <role>` to inspect the live
   choice. Use `cold model get <role> --explain` for the selection reason and
   observed usage state, and `cold model target <role>` for the complete target.
   Angle-bracket values are placeholders to replace before running a command.
2. For review, use `cold model get reviewer --builder-model <actual-builder-model>`
   and `cold model target reviewer --builder-model <actual-builder-model>`.
   Repeat `--builder-model` for every model that wrote the change. Verification
   uses `auditor/verifier` with the same exclusions. Never inherit the builder's
   model or treat a client default as the per-role route.
3. Check the active host's capability catalog and pass the resolved target
   explicitly, including provider, model and effort. Follow the live briefing's
   review workflow. Configured alternatives alone may handle unavailable routes;
   never invent a substitute. Apply the subagent restrictions below before dispatch.
4. Treat unavailable tools, exhausted routes and failed reviews as INCOMPLETE,
   not PASS. Model selection grants no authority to merge, publish, message,
   schedule or deploy. The current task and project still control those actions.

Coldlocker's drift hook resolves the live reviewer role through the `cold` CLI
on PATH on each check and excludes observed builder models.
It passes the resolved model and effort explicitly to Codex. Host command
adapters receive the resolved route and exclusions and must honor them. See the
repository README for builder metadata, adapter configuration and review reuse.
A missing or failed reviewer remains INCOMPLETE. Fetching this rule through the
catalog installs or activates nothing.

## Usage limits and recovery

Resolve again immediately before each dispatch. Do not cache a target in a skill,
rule, hook or handoff. The router selects only alternatives declared in the live
map when an observed provider limit blocks a route, and re-evaluates eligibility
after the reported reset or a newer recovery event. It may spread eligible work
across providers where the role permits. Never implement a second fallback list,
launch counter or cooldown in a consumer. Use the routed launch path so repository
launch limits are checked at the point a thread starts.

Use `cold model availability` to inspect sanitized provider window state and
`cold usage` for observed activity. Counts of started work do not measure quota,
and missing token, cost or limit telemetry is unknown. A lookup does not start a
thread. An exhausted route with no eligible configured alternative is INCOMPLETE.
Leave running work in place; recovery applies to the next dispatch.

`python3 consistency_check.py` checks delivered skills, rules, hooks and routine
support files for model assignments. Run it before delivering changed instructions.


## Configurable delegation defaults

The repository owner can configure task roles and delegation limits in the live
routing policy. The following examples describe one possible configuration;
they do not override the installing user's policy or authorize routing edits.

- Reserve research and audit roles for tasks that need them.
- Use light helper roles for bounded collection and cleanup.
- Resolve implementation roles from the live map.

An owner may require subagents to use a lower tier than their parent. For example:

| Parent | Allowed subagent models |
| --- | --- |
| Astra or Opus | Sol, Terra, Sonnet, Haiku, Luna |
| Sol | Sonnet, Terra, Luna, Haiku |
| Sonnet or Terra | Haiku, Luna |
| Haiku or Luna | None |

In this example, Sonnet and Terra share a tier, and Haiku and Luna share the light
tier. If the installed policy forbids a resolved target, report the dispatch
INCOMPLETE rather than silently substituting. A fallback does not override an
explicit delegation restriction.

For a review or research role outside the allowed child tiers, follow the host's
orchestration contract and obtain authorization before opening a separate
top-level thread. Supply the question and evidence and check the live capability
catalog. An incompatible or unavailable route is INCOMPLETE, not permission to
change routing or bypass a host restriction.

### Example builder responsibilities

| Model | Proposed build responsibility | Basis and limits |
| --- | --- | --- |
| Sol | Uncertain implementation, cross-cutting changes and project ownership | Current builder-default/coordinator policy; no paired build evidence establishes Sol superiority. |
| Sonnet | Bounded small builds and implementation of a known plan; difficult/UI builds only with a concrete reason | Current builder-fast/hard/ui roles and the five-task Sonnet/Terra sample favor small-build latency, not universal quality. |
| Terra | Known-easy code, mechanical fixes and routine CI diagnosis with explicit acceptance checks | Current builder-cheap/ci roles and the same sample favor economical bounded work; no evidence supports uncertain architecture ownership. |

Historical measurements are recorded in
`docs/model-routing-evidence-2026-10-31.md`. They do not establish a universal
ranking. Use the installing user's configured roles and limits.
