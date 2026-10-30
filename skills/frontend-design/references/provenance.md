# Source provenance

Coldlocker package maintainer: `agentlocker`.

The source is Anthropic's official `frontend-design` package at
[claude-plugins-official](https://github.com/anthropics/claude-plugins-official/tree/f8c5e7a1dcb71b10cb74586224b5401f3de5db28/plugins/frontend-design/skills/frontend-design),
commit `f8c5e7a1dcb71b10cb74586224b5401f3de5db28`, retrieved 2026-10-29.
One source revision is retained. `LICENSE.txt` is the unchanged upstream
Apache-2.0 license. That license continues to govern the upstream material;
the hub's MIT metadata does not relicense it.

The upstream package contains `SKILL.md` and `LICENSE.txt`. Coldlocker appends
a marked QA section to `SKILL.md` and adds the files under `references/`.
The original design guidance remains unchanged. The new references are
Coldlocker-authored material under the repository's MIT license.

The optional companion `ui-ux-pro-max` has its own MIT license, data provenance,
and source revision in `references/coldlocker.md` within that package. Fetch it
separately when searchable UX or stack guidance is useful. The official skill
does not depend on its Python scripts.

For updates, review one new upstream revision and its license. Replace the
upstream files, reapply the marked QA addition, and update this source pin.
Recompute the manifest directory hash with `checks.validators.digest` and set
`provenance.source_id` to `src-` plus its first 12 hex characters. Keep the same
owner and `upstream` origin. Run the repository checks and stdio retrieval
proof before merging. Do not copy cached revisions into the catalog.
