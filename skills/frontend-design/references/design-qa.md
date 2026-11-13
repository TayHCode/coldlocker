# Design QA verification

Use this section when a task changes UI. A catalog import alone does not prove
that a screen matches its reference or meets accessibility requirements.
Follow the project's existing verification workflow. Do not install another
verification loop, start a service, or publish evidence without authorization.

## Reference screenshots

1. Identify the approved reference and its revision before changing the UI.
   Record the screen, state, theme, viewport, browser, and fixture data.
2. Capture the implemented screen with the same content and viewport on desktop
   and mobile. Use the reference dimensions. If none are supplied, start with
   1440 × 900 and 390 × 844, then cover the actual supported breakpoints.
3. Compare the reference and result side by side. Inspect hierarchy, alignment,
   type, spacing, wrapping, overflow, and control placement. Record image paths
   and each intentional difference. Include changed loading, empty, error,
   disabled, and focus states. Check light and dark themes when supported.
4. Exercise keyboard navigation, visible focus, reduced motion, zoom, and touch
   interactions. A screenshot does not prove that these behaviors work.

Use sanitized fixtures. Never capture account data or credentials for a report.
If the reference or screenshot tooling is unavailable, record that gap rather
than claiming visual parity.

## Contrast

Measure rendered foreground/background pairs, including muted text, links,
errors, controls, and focus indicators in every supported theme. Record both
colors, the element and state, the tool, and the unrounded ratio. Composite
transparency against the actual background before measuring.

WCAG AA requires at least 4.5:1 for ordinary text and 3:1 for large text.
Large text means at least 18 pt, or 14 pt bold. Required non-text control and
state indicators need 3:1 against adjacent colors. Record any applicable
exception rather than quietly excluding a failed pair. These checks do not
constitute a full accessibility audit.

See W3C's [text contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)
and [non-text contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html).

## Impeccable check

Impeccable is an external CLI from
[pbakaus/impeccable](https://github.com/pbakaus/impeccable).
This package does not supply or invent an Impeccable skill. Use an approved,
explicit CLI version and record its actual `--version` output. Do not run its
skill installer as part of QA.

The following commands were exercised from the Coldlocker repository root with
CLI 4.8.0. Change the target to the affected files in another project.

```sh
npx --yes impeccable@4.8.0 --version
npx --yes impeccable@4.8.0 detect --help
npx --yes impeccable@4.8.0 detect --json ui/
```

The first use can download npm packages. If package downloads are not allowed,
use an already approved local installation and record its version, or mark the
check incomplete. Do not substitute an unversioned `npx impeccable` result.

For 4.8.0, exit 0 means no primary findings, exit 1 means a target could not be
scanned, and exit 2 means primary findings. Advisory findings can remain at exit
0. Review the complete output and any project or inline suppressions. Save the
command, target revision, exit code, and finding dispositions. Verify these exit
semantics again when upgrading the CLI.

A static file scan does not render responsive states or measure every contrast
pair. Pair it with the screenshot and accessibility checks above. If an
authorized rendered scan is available, record the viewport and target too.
Do not start or expose a service just to complete this checklist.

## Evidence to attach

Record the revision, reference/result image paths, desktop/mobile dimensions,
contrast measurements, interaction results, Impeccable version and command,
exit code, findings, and remaining gaps. Mark each check PASS, FAIL, or
INCOMPLETE. Fix findings within the task's scope and document accepted
exceptions. Do not claim completion for a failed or unperformed required check.

The 2026-10-29 package-import receipt is in
`docs/evidence/coldlocker-design-package.md` in the source repository. Its scan
of the existing UI is a baseline, not a claim that the UI passed this checklist.
