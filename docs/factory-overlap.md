# Factory boundary

How agentlocker relates to pstack-factory (the Factory): the hub holds portable
assets, and the Factory keeps its own routing, evidence and gates. No private
configuration is reproduced here.

| Factory responsibility | Boundary |
| --- | --- |
| Whole skill packages and supporting references | Import by copy; the hub records content hash, opaque source id, origin and one maintainer. |
| Relative native install links | `hub.py link` exposes hub-owned packages to chosen targets; keep Factory's own links until a deliberate ownership migration. |
| Source, link and router checks (`check_installation.py`) | The hub borrows the PASS/FAIL presentation only; `hub.py check` verifies hub content. |
| Evidence receipts (`check_evidence.py`) | Factory-owned. Hub content hashes do not replace them. |
| Catalog routing and integrity manifest | The hub manifest inventories portable assets; the Factory keeps source decisions, pinned upstream integrity and executable routing. |
| Generated overlays and runtime policy | Stay in the Factory. Import only intentionally portable guidance. |

Do not delete Factory copies or repoint Factory-owned links during import. Pick
one owner per skill before cutover so no skill has two active discovery routes:
compare content and provenance, test discovery in a disposable target, then
authorize any migration separately. Preserve upstream licensing when importing
third-party content.

Reference names (`pstack/install_native.py`, `check_installation.py`,
`check_evidence.py`, `catalog.py`, `manifest.json`, `overlay.py`) are patterns, not
vendored dependencies or workflows to run.
