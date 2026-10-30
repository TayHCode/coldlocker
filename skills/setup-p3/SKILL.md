---
name: setup-p3
description: Inspect the live model routes used by p3-stack and validate host support. Use for /setup-p3 or questions about p3 model routing and effort.
disable-model-invocation: true
---

Read [the Coldlocker host adapter](PACKAGE.md) before following this skill.

# Setup p3

Model routing is the single authority for provider, model and effort. Read
[the routing reference](../../rules/model-routing.md), or fetch it with
`hub_get_item` using `kind=rule` and `name=model-routing`.

1. Locate and read Agentfleet's live `models.yaml`, `BRIEFING.md` and harness
   routing instructions. If unavailable, report INCOMPLETE.
2. Choose the applicable role from the routing reference. Use the model and
   effort that model routing selects for that role. Resolve its complete target
   with `cold model target <role>` at dispatch time. Review and verification
   exclude every builder using repeated `--builder-model` flags.
3. Check that the active host's capability catalog supports the returned target.
   Capability discovery validates a route; it does not choose one. Report an
   unavailable route as INCOMPLETE rather than inheriting a model, picking a
   substitute or changing its effort.
4. Report the selected role and observed target. Do not write a local model map,
   budget ladder, fallback list or skill default. Requests to change selection
   belong in the maintained model-routing authority, under its owner's policy.

This skill does not modify routing or user configuration merely by being read.
