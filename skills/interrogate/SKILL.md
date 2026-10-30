---
name: interrogate
description: "Use for \"interrogate\", \"adversarial review\", \"multi-model review\", \"challenge this\", \"stress test this code\", \"find blind spots\", or \"tear this apart\". Multiple LLM reviewers challenge changes from independent angles."
---

Read [the Coldlocker host adapter](PACKAGE.md) before following this skill.

# Interrogate

Read [Agentfleet harness routing](PACKAGE.md) once per session before you follow this skill. It maps pstack tool and model names to the active harness. `BRIEFING.md` owns Agentfleet policy where source workflow text differs.

Use `reviewer` for the single primary reviewer. A second independent reviewer requires an explicit need. Read the live routes with `cold model get <role>` as described in `rules/harness-routing.md`. The deliverable is a synthesized verdict. Do NOT auto-apply changes.

## Review cost and explicit routing

Resolve reviewers via `cold model get reviewer --builder-model <actual-builder-model>`.
Resolve `cold model target reviewer` with the same repeated builder flags and pass
its explicit target on dispatch. Never inherit the parent's model, omit
the model, or use auto. Default to one reviewer and one round; more rounds only
for real issues found in the first. At most one additional non-builder
reviewer may be explicitly requested. Use the model that model routing selects for the applicable review role. No default three-provider panel.
Include fleet-role and fleet-builder-model in the worker brief.

## Step 1, Scope

Identify what to review from context:

- If the user points at specific files or a diff, use that.
- If on a feature branch, run `git diff main...HEAD` (or the appropriate base branch) for the full changeset.
- If the user's message references recent work, gather the relevant files.

Package the diff or file contents with any surrounding context files the reviewers need to understand the code.

## Step 2, Intent

Before spawning reviewers, state the intent in one clear paragraph from the user's message, commit messages, the PR description if one exists, and the code. If you're unsure about the intent, ask the user before proceeding.

## Step 3, Spawn Reviewers

Launch reviewers with the current harness worker tool. Use `reviewer` for one independent review by default; apply the cost and explicit routing rule above. Read each live route before dispatch and label the returned results.

Each reviewer gets the permissions and model route supported by the active harness. If a configured route is unavailable, report it and continue work that does not require that dispatch. Do not substitute another model.

Read `references/reviewer-prompt.md` and fill in the same template for every reviewer with the stated intent, the diff or file contents, the rubric from `references/rubric.md`, and the code-quality lens from `references/code-quality-review.md`. Use the model and effort that model routing selects for the reviewer role.

## Step 4, Synthesize

As results come back, build a unified picture. Parse every reviewer's findings. Merge findings that describe the same issue differently, and note which models raised each one. Findings two or more models raised independently are the highest signal. Read a lone model's finding, but weight it accordingly. Note disagreements. If one model flags something and another explicitly says the opposite, that is context for the verdict.

## Step 5, Lead Judgment

You are the lead reviewer, a pragmatic senior engineer, not a neutral aggregator. Read `references/lead-judgment.md` for the full framework. Put every finding in one of four buckets, with the model(s) that raised it and a one-line rationale.

- **Act on**. Real issues affecting correctness, security, or maintainability given the actual goals. They would block a real PR.
- **Consider**. Legitimate, but you're not sure they outweigh the cost of addressing them now. Worth the user's attention.
- **Noted**. Technically valid but not actionable. Context-dependent, premature optimization, or low-impact at this stage.
- **Dismissed**. Wrong, nitpicky, or missing context, with a brief reason.

## Output Format

### Intent
> [The stated intent paragraph from Step 2]

### Reviewers
- Reviewer [label]: [model name], [N findings] (one bullet per reviewer)

### Act On
[Each: description, which models raised it, why it matters.]

### Consider
[Each: description, which models raised it, the tradeoff.]

### Noted
[Brief list.]

### Dismissed
[Each with a brief rationale.]

### Agreement Map
[Where models agreed and diverged, and what that pattern tells us.]
