---
name: swarm
description: "Fan out N parallel workers, drain them, and return one report. Use for /swarm, 'swarm this', or parallel coverage, races, gauntlets, and exploration."
---

Read [the Coldlocker host adapter](PACKAGE.md) before following this skill.

# Swarm

Read [Agentfleet harness routing](PACKAGE.md) once per session before you follow this skill. It maps pstack tool and model names to the active harness. `BRIEFING.md` owns Agentfleet policy where source workflow text differs.

## Start

Open a todolist with one entry per phase before launching anything.

1. Frame
2. Fan out
3. Aggregate
4. Report

## Phase A: Frame

1. State the done predicate and the artifact or report the swarm must return.
2. Choose the shape. Partition into slices, race N workers on identical briefs, or mix both. For a race or mixed shape, declare `first pass`, `rank all`, or `best-of` before spawning.
4. Route routine workers through `builder-default`; use `builder-hard` or `builder-ui` for difficult or UI work. Resolve each complete explicit target with `cold model target <role>` as described in `rules/harness-routing.md`. For a model race, name each arm's role up front and use the current harness worker tool.
5. Give each worker its own writable output when it writes. When workers verify or measure commits, each brief names the exact SHAs. A measurement brief also names the method (sample count, what one sample is, order). The worker records both in its result.

## Phase B: Fan out

Use the current harness dispatch tool with each complete explicit target from step 4. Never leave the model unset or use auto or inherit-parent. Cross-provider routes require delegate_task. Every brief includes `fleet-role: <resolved-role>`, including non-review workers and model-race arms. Hard/UI roles also include fleet-reason with concrete evidence.

When a worker must start from a non-default pushed branch, pass `cloud_base_branch`.

Every brief stands alone. Include the goal, scope, exact slice or race arm, how to verify, and what to report. Reports use `PASS`, `ISSUES`, or `BLOCKED` with evidence. A worker that can prove a defect reports `ISSUES` and lists every issue it can prove, not only the first.

If a worker drops out, proceed with N-1 and note it.

## Phase C: Aggregate

Read the terminal results. Drop a result that does not record the SHAs and method its brief names, and respawn that worker once. After a second miss, record a gap. A gap does not count as a pass. For coverage, every required slice needs a result. For a race, apply the selection rule declared up front. Use first pass, rank all, or best-of. Do not paste raw worker dumps.

## Phase D: Report

Return one consolidated in-chat report with the table, issue one-liners, gaps or dropouts, and the race rule when used.
