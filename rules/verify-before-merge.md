# Verify before merge

Before merging any pull request, run the verify loop in the pull request worktree
(`skills/verify-loop`). It tests the committed head merged with the current base
branch and posts the result to the head SHA with `--post`.

- Fail: read the log, fix, commit and run again until pass or a real blocker.
- Pass: merge through the project's review and merge command, or its own policy.
- Rerun when the base branch moved after the check, or when the head changed.
- Zero tests is a failure. Never skip, weaken or delete a test to pass.
- Post the check only through the runner. Never post or edit it by hand.
- A `verification changed` note means tests, recipes or workflows were edited;
  review that diff before merging.
- Resolve any model or effort for a fix or review from
  [model routing](model-routing.md). Name roles, never models.
- Changing rulesets, required checks or branch protection needs the owner's
  approval. Propose the change; do not apply it.
