---
name: hill-climb
description: Prepare every long task with a goal, an automated verifier, supplies, and periodic blocker check-ins. Use before sustained or unattended work and when repeated blockers slow progress.
---

# Hill-climb

Before starting any long task, fill in all four fields below in the task record.
Follow the closest project's instructions and authorization boundaries.
Name roles only. Read the separately indexed `model-routing` rule before
selecting models. Locally it is `rules/model-routing.md` at the catalog root.
Through MCP, discover `hub_get_item` with `tools/list`, then fetch it with
`{"kind": "rule", "name": "model-routing"}`. The active model-routing
configuration is the single authority for the builder, verifier, and agent judge.
If the rule or live routing is unavailable, report the blocker.

1. **Goal.** State what you want and the observable done condition. Prefer
   tedious, mechanical work with a clear result. Keep the goal bounded.
2. **Verifier.** Record the exact command that exits 0 for pass and 1 for fail.
   If a command cannot judge the outcome, name an independent agent-judge role,
   the artifacts it must inspect, and a rubric with explicit pass/fail criteria.
   A human check does not count. No verifier means no long run. First establish
   the verifier. An unavailable or broken verifier is not a pass.
3. **Supplies.** List tools, compute through coldbox, data, access, and any
   resource budget up front. Record what is available and what is missing.
   Check the configured coldbox limits and queue before requesting compute.
   If coldbox is unavailable, record that blocker rather than assuming access
   or creating infrastructure. Never place secret values in the task record.
4. **Blocker check-in.** Set a periodic cadence before starting, using elapsed
   time or completed iterations. At each check-in answer: what is slowing you
   down? Record evidence, impact, the next action, and changed supply needs.
   Feed the lesson to Coldfix through its authorized intake. If no intake is
   available, retain a local handoff and report the delivery blocker.
   Repeated blockers become new step-1 goals with their own verifiers.

Steps 3 and 4 are a loop. Recheck supplies, address the observed blocker within
authorization, and check again at the recorded cadence. Keep the original goal
visible. A blocker goal does not authorize unrelated work or external changes.

Run the verifier against the actual candidate result. Preserve the command,
exit status, and artifact revision, or the judge's rubric verdict and evidence.
Stop owned coldbox VMs after collecting the result, including on failure.
Report completion only when the done condition passes. Report missing supplies
or verifier failures as blockers, never as successful completion.

Use this record before the run:

```text
Goal: wanted outcome and done condition
Verifier: command, or judge role + artifacts + pass/fail rubric
Supplies: tools; coldbox compute; data; access; budget; missing items
Blocker check-in: cadence; evidence location; Coldfix intake or local handoff
```
