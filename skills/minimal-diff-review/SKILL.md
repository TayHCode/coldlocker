---
name: minimal-diff-review
description: Review a proposed diff for correctness, requested scope and missing behavioral tests before handoff.
---

# Review the diff

Read the task, applicable project instructions and complete diff. Follow changed call sites and contracts far enough to identify observable regressions.

Check boundary inputs, error handling, preservation of existing data and compatibility with callers. Look for changes unrelated to the requested outcome. Ask whether tests distinguish the intended behavior from plausible failures rather than repeat the implementation.

Report actionable findings with file locations, triggering conditions and consequences. Separate confirmed defects from questions requiring evidence. If no defect is found, say so and name unverified behavior. Review alone does not authorize edits, publication or destructive actions.
