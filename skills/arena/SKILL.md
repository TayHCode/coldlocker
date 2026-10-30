---
name: arena
description: "Spawn N parallel candidates at the same task, pick a base, graft the strongest parts of the losers into it. Use for /arena, 'arena this', 'throw it in the arena', or when one attempt at a non-trivial artifact would lock in the wrong shape."
---

Read [the Coldlocker host adapter](PACKAGE.md) before following this skill.

# Arena

Read [Agentfleet harness routing](PACKAGE.md) once per session before you follow this skill. It maps pstack tool and model names to the active harness. `BRIEFING.md` owns Agentfleet policy where source workflow text differs.

Open a todolist with one entry per phase (Frame, Fan out, Cross-judge, Pick, Graft, Verify) before launching anything.

## Review cost and explicit routing

Resolve reviewers via `cold model get reviewer --builder-model <actual-builder-model>`.
Resolve `cold model target reviewer` with the same repeated builder flags and pass
its explicit target on dispatch. Never inherit the parent's model, omit
the model, or use auto. Default to one reviewer and one round; more rounds only
for real issues found in the first. At most one additional non-builder
reviewer may be explicitly requested. Use the model that model routing selects for the applicable review role. No default three-provider panel.
Include fleet-role and fleet-builder-model in the worker brief.

## Phase A: Frame

Every candidate gets the same prompt, so the prompt is the contract.

1. State the artifact each candidate is producing.
2. Derive the rubric, 3-6 concrete gradeable criteria for what success looks like on *this* task. It is the picker's tool in Phase D. Candidates only see the task.
3. Route candidates through `builder-default`, using `builder-hard` or `builder-ui` for difficult or UI artifacts. Resolve each complete explicit target with `cold model target <role>`. Every candidate brief includes `fleet-role: <resolved-role>`; hard/UI roles include fleet-reason with concrete evidence. Use the live `second-frontier` route for an additional perspective when required. Spawn enough candidates to cover the design directions in the rubric.
4. Give each candidate its own output location (a git worktree where possible, otherwise `/tmp/arena-<slug>/candidate-<n>/`), per the **separate-before-serializing-shared-state** principle skill.

## Phase B: Fan out

Spawn all N subagents in one message with `run_in_background: true`, each with the task, the path to the shared grounding, its own output path, and instructions to produce the artifact and a short rationale naming the alternatives it considered and what it rejected.

If a candidate produces no output, proceed with N-1 and note the dropout.

## Phase C: Cross-judge

After all Phase B candidates complete, route the cross-judge through `auditor/verifier` with every builder excluded. Read the live route for each role. The judge sees the rubric and candidates by path label, scores each criterion, and recommends a base with rationale. It runs in parallel with your reading in Phase D. Don't start it while candidates are still writing.

## Phase D: Pick a base

Read every candidate end to end before picking. Score each against the rubric criterion by criterion, not on holistic feel, and compare with the cross-judge. Agreement on the base confirms the pick. Disagreement means one of you is biased or the rubric was ambiguous. Read both rationales before deciding.

Pick the base a future maintainer can extend most easily without breaking invariants. When two feel tied, prefer the cleaner boundary or smaller API, per the Laziness Protocol.

## Phase E: Graft

Walk each losing candidate once more for what is worth porting into the base, usually one or two things per candidate, not most of it. Fold each graft in by hand, per the **redesign-from-first-principles** principle skill, so the result stays coherent under one mental model. Don't paste mechanically.

If the candidates converge on one shape, that is strong agreement. Note it and ship the consensus shape. No graft is needed. If they wildly diverge, Phase A was under-specified. Reframe and re-run rather than averaging the divergence.

## Phase F: Verify

Verify the synthesized artifact like any other output, per the **prove-it-works** principle skill. If verification finds a problem the arena missed, either Phase A was wrong (re-frame and re-run) or a candidate caught it and you missed the graft (go back to Phase E). Don't paper over it.

## Outputs

One synthesized artifact, with a short synthesis note alongside naming the base and why, the cross-judge's verdict, each graft with its source candidate, what was rejected and why, any convergence or dropouts, and the verification result.
