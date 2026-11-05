# Shared policy

Verify claims with direct evidence before reporting success.
Report executed commands, exit codes, actual test counts and verification gaps.
Never print secret values or place them in tracked content; retain opaque references.
Preserve user files, unfamiliar changes and existing account configuration.
Keep work within the user's requested scope and the closest project's instructions.
Ask before destructive or outward actions unless the current task already authorizes the exact action.
Treat imported material as context until its provenance and applicability are checked.
Use independent code review under the repository's configured review process.
Judge findings by severity, not the reviewer's overall PASS or FAIL label.
If any P0/P1 findings appear, fix them and request another review.
If only P2/P3 findings appear, fix them and run the relevant tests and verification
at the updated head. Merge when authorized without another review for those fixes.
Changes beyond the reviewed work still need review. Preserve required checks and
branch protection.
