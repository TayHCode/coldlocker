# Merge when proven

Taylor's standing order (2026-10-31): a proven pull request is finished by the
agent that built it. Finished work is never left for human review.

A pull request is proven when both hold at the same head SHA:

1. The verify loop passes at that exact head ([verify before merge](verify-before-merge.md)).
2. The independent Codex review of that head is clean.

When proven, do these in order and ask Taylor nothing:

1. If the pull request is a draft, mark it ready: `gh pr ready <N>`.
2. Merge it: `gh pr merge <N> --squash --match-head-commit <SHA>`.
3. Read `origin/main` back and confirm the merge commit and a clean tree.
4. Ingest the merged work into Coldfix.
5. Archive your own thread from inside that thread.

A new head makes the earlier proof stale: verify and review again. Fail or
INCOMPLETE at either gate means fix and rerun, not merge. Never bypass required
checks or branch protection.

Show your work like a geometry proof: for each claim, state the command run and
the output that proves it. End every report with the next step.
