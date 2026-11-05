### Shipping

Verify each PR independently and land only the contiguous verified run from the bottom of the stack. Follow the current project's authorization and verification requirements.

1. **Record the stack and authorization.** Read each PR's head, base and dependencies. Keep one writer for stack topology. Operator-reserved merges stay with the operator. Never land an unverified parent to reach a verified child.
2. **Verify the current head and base.** Run the required checks and exercise the changed behavior against the real surface when available. Resolve independent review through live model routing for `auditor/verifier`, excluding every builder. Record the head SHA, base SHA, evidence and verdict. Only PASS permits landing; FAIL or INCOMPLETE requires resolution.
3. **Prepare the lowest unmerged PR.** Fetch current trunk. Integrate it when needed, resolve conflicts, push, and retarget only this PR to trunk. Every changed head or base needs affected checks rerun and a new verdict. Matching patch IDs or build outputs never transfer a verdict.
4. **Wait for required checks before requesting a merge.** Use the current harness's supported PR wait mechanism. Do not arm auto-merge or a merge queue: delayed landing would bypass the following live-base assessment. Once checks pass, read the current head and base again. If either changed, return to step 2.
5. **Assess the live base and merge the verified head.** Immediately before merge, fetch the base and assess any changes since verification to the patch's context, callers, dependencies and checks. Integrate and verify affected changes; otherwise record why the drift is unrelated. Merge with `gh pr merge <pr> --squash --match-head-commit <verified-head>`. Another forge is usable only if it supports the same expected-head guard and immediate merge; otherwise stop and report the missing capability. Never bypass required checks or protection.
6. **Read back the result before moving upward.** Confirm the PR is merged into trunk, fetch its merge commit and verify the shipped content. A queued or pending response is not a merge. Retarget the next PR to trunk and integrate the parent merge result through the topology owner. Return to step 2 for that PR's current head and base before landing it.
7. **Stop at the verified ceiling.** Leave later unverified PRs open. Report merged PRs, merge SHAs, evidence and the next unmet requirement. Extend the run only within the operator's authorization.

**Reply:** the verified run, what landed and how you confirmed it, and what remains.
