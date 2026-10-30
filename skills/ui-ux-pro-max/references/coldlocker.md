# Coldlocker integration

Coldlocker package maintainer: `agentlocker`.

Source: [Next Level Builder UI UX Pro Max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill/tree/28b3b8dee8c976df4fa3e94aed4e092ad43fcb03/.claude/skills/ui-ux-pro-max),
commit `28b3b8dee8c976df4fa3e94aed4e092ad43fcb03`, retrieved 2026-10-29.
The upstream MIT license permits this copy and modification with its notice
retained. `LICENSE` is copied unchanged from the source repository.

This is one runtime package from `.claude/skills/ui-ux-pro-max`, with its data,
references, and Python scripts. Upstream developer tests are omitted because
they depend on the larger upstream repository. No plugin installer, duplicate
CLI assets, or other cached version is included. Data provenance and font
license metadata remain in `data/`. Font and icon recommendations do not grant
rights to redistribute the assets themselves.

Local modifications:

- `SKILL.md` replaces the Claude plugin path with `UI_UX_SKILL_ROOT` and adds
  the marked Coldlocker integration paragraph. Set that variable to this
  package's actual absolute directory. Python examples can use `python3 -B`.
- `data/stacks/avalonia.csv` and `data/stacks/javafx.csv` replace example home
  paths with `/example` paths to satisfy the hub's private-path scan. Their
  guidance and source URLs are preserved.
- `scripts/design_system.py` removes trailing spaces from blank lines.
- This reference is added under the repository's MIT license.

For UI delivery, fetch `frontend-design` with `hub_get_item` and follow its
`references/design-qa.md` section. User and project requirements take priority
over this companion's suggested search counts and retries.

The package exceeds the endpoint's existing 1 MiB aggregate response budget.
After `hub_get_item`, inspect `omitted` and `truncated`. Fetch omitted files
individually with `resources/read`, using the URIs from `resources/list`.
All individual files in this pinned package fit that budget. MCP retrieval is
read-only and does not execute Python. To run a search, materialize the package
with its original relative layout or use a local checkout. Never treat a
truncated response as a complete runnable package.

For updates, select one upstream commit, review licenses and data provenance,
retain these documented adaptations, and refresh the manifest directory hash
and source id. Verify both a focused search and stdio retrieval, including
omitted supporting files. Do not change the endpoint's limits for this package.
