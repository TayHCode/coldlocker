# Product acceptance

## The problem to solve

The motivating request is one easy place to centralize skills and routines
across agents such as Grok Bot, Dots, Claude, Codex, Cursor, MausBot and Hermes.
The requester's MCPs already live in Composio. The missing product is a shared
skills and routines library those agents can reach and use.

The release target is a usable product for another person with that problem.
The existing local catalog proof is a foundation; a private personal setup
alone does not satisfy the target. Self-hosting can satisfy it. A managed,
multi-user cloud service is not implied by this requirement.

## Required journey

1. A new user follows the documented setup from clean state, imports reviewed
   skills with supporting files and routine definitions, and understands what
   is held for review. Setup must not require reading implementation code or
   receiving machine-specific help from the project orchestrator.
2. The user connects a declared set of agents, including at least one local
   agent and one genuinely hosted bot. Both reach the same approved library;
   hosted access cannot depend on the bot seeing the user's local filesystem.
3. Each accepted agent finds the relevant skill, reads its supporting content
   and uses it to complete the same representative task. Listing tools or
   printing configuration is insufficient evidence of this outcome.
4. The user updates the central skill and routine prompt. Connected agents
   obtain the intended updated content without separate substantive copies
   maintained in every agent. Record any publish step, revision pin, cache or
   native-library limitation plainly.
5. Routine content and suggested timing are centralized. An accepted agent can
   fetch the current routine and execute its prompt on request. Record actual
   execution evidence and content-update behavior. Automatic schedules are an
   optional runner capability with separate activation, control and disable
   checks; they are not required to use the shared instruction library.
6. Existing Composio MCP connections and account settings remain intact. The
   skills/routines journey must not require replacing an already working MCP
   system. A direct remote catalog connection and a Composio custom toolkit
   are separate possible delivery paths; the latter requires its own live
   interoperability proof before being claimed as supported.

## Compatibility and evidence

The initial acceptance targets and current evidence are:

| Agent | Delivery path | Current product evidence | Remaining acceptance |
| --- | --- | --- | --- |
| Claude Code | Local stdio catalog | Real synthetic skill/supporting-file use; fresh model session observed the shared guide and routine-prompt update | Independent fresh-user setup |
| Codex CLI | Local stdio catalog | Real model-driven read/use task; later T3-triggered CLI run fetched and executed the current routine and supporting skill | Independent fresh-user setup; native app scheduler remains separate |
| Grok Bot | User's remote catalog and platform routine handoff | Mac app connection screenshot; user-reported initial and updated skill/supporting-file results match source; routine and prompt fetched after an update | Independent fresh-user setup; native scheduling is optional and unproved |
| T3 with local Codex and Claude Code | Native T3 manual trigger invoking controlled local CLI jobs and stdio MCP | Both clients fetched the central routine and returned its expected result; task verified disabled afterward | Automatic interval firing, T3 remote OAuth and native Codex/Claude app schedulers not proved |
| Dots, hosted Muse, MausBot, T3 remote OAuth | Capability-specific handoff or fallback | Remote capability unconfirmed in the current project records | Establish a supported path before advertising compatibility |
| Cursor, Hermes and other agents | Client-specific local or remote connection | Setup adapters/documentation do not establish product acceptance | Record actual discovery, skill use and update behavior per supported surface |

The first release must prove the declared local-plus-hosted set. Expansion to
other agents needs its own evidence; names in an adapter list are not proof.

Maintain an explicit matrix for the named target agents: connection method,
skill discovery and use, supporting files, content updates, routine handoff or
execution, and actual test evidence. Distinguish verified product behavior,
documented capability, manual fallback, unsupported behavior and unknowns.
Choose and publish a useful supported initial set; do not claim every agent
works merely because it accepts MCP. Unsupported platform capabilities must
remain visible rather than being hidden behind a universal compatibility claim.

The recorded Claude Code and Codex sessions establish model-driven local
catalog use on synthetic content with existing logins. The isolated hosted
trial used the reviewed source snapshot and synthetic imports. Its metadata
endpoints responded, anonymous catalog access returned 401, and authorization
redirected to owner sign-in. A user-supplied Mac app screenshot shows the
Grok Bot connector connected. User-pasted task responses correctly used two
skills and their supporting files, then retrieved the changed guide and
routine prompt without reconnecting. Local Claude independently observed the
same update. Raw hosted tool events were not directly captured. These results
do not establish independent new-user setup or native Grok routine execution.
See [release evidence](release.md).

A native T3 manual trigger subsequently ran the current routine through one
ephemeral Codex CLI session and one Claude Code session. Both returned the exact
source-derived result; local MCP logs show the required catalog reads. The thin
scheduled instruction did not contain the substantive prompt or expected answer.
Both private jobs ended inactive, and the T3 task remained disabled with no next
run. This establishes that local execution fallback, not automatic interval
firing or the clients' own app schedulers. Wrapper timeout and overlap controls
are separate from native platform controls. See the bounded evidence and
configuration caveats in the [release record](release.md#recorded-cross-agent-staging-and-routine-execution).

The bot stopped routine creation after reporting unavailable native overlap
and timeout controls. No hosted routine was created or run in that attempt.
Keep strict definitions inactive when their required controls are unsupported;
do not turn runner limitations into implicit best-effort execution. A separately
approved limited scheduling trial must state which controls are unenforced and
cannot be reported as satisfying the strict definition. A subsequent limited
Grok test was created paused, then unpaused by the owner; it produced no run
and the bot repaused it. The final Paused state is visible in the supplied
screenshot. Native dispatch and background fetching remain undiagnosed, and
the scheduling trial has not passed. See the
[Grok Bot capability notes](grok-bot-bootstrap.md#recurring-work).

For remote acceptance, identify the deployed source and published catalog
revision, verify owner authorization and access boundaries, and record the
actual hosted bot's task result. Use nonsecret representative content. A
successful local Worker test or an unauthenticated rejection is insufficient.

## Release boundary

The first release shares skills and routine instructions; agents execute them
on request. It does not depend on Grok's timer or add a scheduler. Keep the
native timer trials as recorded limitations, separate from this core scope.

The local proof, tests and connection safeguards remain required engineering
gates. They do not substitute for this product journey. Resolve the first-user
installation/distribution path and security reporting route before distributing
to an external tester. Review the actual artifact and keep private evidence and
credentials out. A source-only archive excludes Git history; distributing the
repository or any history-bearing bundle additionally requires resolving the
reachable-history privacy finding. Publication still requires owner approval.

Real accounts, deployments, content publication and schedule activation still
require their specific authorization. Product scope does not itself activate
them. No public release, hosted service or account change has happened merely
because these criteria are documented.
