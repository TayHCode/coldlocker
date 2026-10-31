# Routing evidence — 2026-10-31

This is a proposal record, not an applied routing change. No models.yaml was edited.

Sources read from ColdStack’s main worktree:

- `models.yaml`: Sol is builder-default/coordinator, Sonnet is builder-fast/hard/ui,
  Terra is builder-cheap/ci, and Haiku/Luna have light roles.
- `lessons/L15-model-routing.md` (the model-routing lesson): resolve at dispatch,
  respect the owner’s latest instructions and exclude builders from review.
- `lessons/evidence/2026-10-26-routing.json`: historical resolver probes, including
  refusal when no independent reviewer remained. These prove resolver behavior,
  not model quality; historical Opus/Astra review roles are not current defaults.
- `STATE.sonnet-terra-build.md`: five small fixes in one repository, strict
  acceptance 4/5 for both candidates; both failures share a historical row-shape
  expectation. This supports a bounded Sonnet/Terra split, not a general ranking.
- `BRIEFING.md`: summarizes Sonnet’s small-build latency advantage and Terra’s
  economical build/review results. Its referenced `evidence/sonnet-terra-build`,
  `evidence/sonnet-terra` and `evidence/verifier-benchmark` reports are absent from
  this main checkout. Raw benchmark reports were not independently revalidated.

The proposed split is Sol for uncertain/cross-cutting ownership, Sonnet for
bounded known-plan builds, and Terra for known-easy fixes and routine CI.
Sol’s quality role remains a policy choice, not a measured superiority claim.
No new benchmarks or paid model experiments were run.

## builder-fast: which is right?

At observation time, `models.yaml` says Sonnet while `cold model get builder-fast
--explain` returns Sol. Both are correct at different layers: Sonnet is the
configured primary; Sol is the effective live selection. The explanation says
“configured equivalent with fewer observed model thread starts in its tier in
the last five hours.” Both providers were reported unblocked. The file lists
builder-default and builder-cheap as builder-fast equivalents, so this is
intentional balancing, not a stale file or an unauthorized default change.
Use the live resolved target for dispatch, subject to the parent/child policy.

The parent/child table makes unspecified comparisons conservative: Sonnet and
Terra are peers; Haiku and Luna are peers. Neither pair can spawn each other.
The explicit owner examples for Astra, Opus and Sol are preserved exactly.
