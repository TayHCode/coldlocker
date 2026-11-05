---
name: evidence-before-done
description: Gather direct verification evidence before reporting a task complete, including actual commands, exit codes and test counts.
---

# Verify before completion

Choose checks that exercise the changed behavior and the project's required suite. Execute them against the final change. A command being present in a plan is not evidence of execution.

Read the result and exit code. Report the runner's actual test count, including failures or skips; do not infer a count from source methods or substitute a previous run. Distinguish an unavailable check from a passing one.

Inspect the final diff and workspace status for unintended changes. State what changed, the exact verification commands and exit codes, and remaining limits. Claim completion only for outcomes supported by the observed evidence. Preserve logs when useful without exposing secrets or private data.
