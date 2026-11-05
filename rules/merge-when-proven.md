# Merge when proven

The repository owner configures whether agents may merge verified pull requests
or must leave them ready for review. Verification alone does not grant merge
permission. Follow the current task's authorization and repository policy.

A pull request is proven when both hold:

1. The verify loop passes at that exact head ([verify before merge](verify-before-merge.md)).
2. The independent code review of that head is clean, or it found only P2/P3 issues and their
   fixes pass verification at the updated head under [shared policy](shared-policy.md).

Record the reviewed head and the verified fix commits when using the P2/P3 path.

When proven and merging is authorized, follow these defaults unless the repository
owner configures a different protected workflow:

1. If the pull request is a draft, mark it ready: `gh pr ready <N>`.
2. Merge it: `gh pr merge <N> --squash --match-head-commit <SHA>`.
3. Read `origin/main` back and confirm the merge commit and a clean tree.
4. Run repository-defined post-merge steps within the task's authorized scope.

A new head requires verification again. P0/P1 fixes and changes beyond the reviewed
work require another review. Verified P2/P3 fixes do not require another review.
Failed verification or an incomplete review does not permit merging. Never bypass
required checks or branch protection.

Show your work like a geometry proof: for each claim, state the command run and
the output that proves it. End every report with the next step.
