# End-to-end dogfood verification

This report checks the public source export on Linux. PASS means the named replay
observed the result. FAIL means an advertised path failed. DRIFT means a statement
needs a narrower scope or could not be established within this offline run.
Historical client acceptance is not new evidence for this head.

## Replay

From a Git checkout with Python 3.11+ and Git on PATH:

```sh
python3 -B -m unittest discover -s tests -p test_e2e_dogfood.py -v
bash .fleet/verify.sh
cold verify --no-cache
```

The first command starts real CLI and stdio MCP subprocesses in a disposable
source copy and synthetic home. It uses the existing local catalog proof rather
than a second implementation. Both tests assert file bytes and protocol results.
No provider, real client, service, schedule or remote catalog is activated.
The doctor step permits local Git subprocesses; the remaining product steps use
the proof's offline audit guard. Temporary state is removed after each test.

The Bash and PowerShell recipes both run `tools/verify_export.py`. That script
includes the new replay and verify-loop regressions alongside the existing public
export tests. Source archives retain the existing export checks and verify-loop
regressions, but explicitly skip the Git-metadata-dependent dogfood source copy.
Full unittest discovery is a separate maintainer check, not the
public-export recipe. `cold verify` currently routes this repository to Windows;
the Bash command above is the native Linux proof. Changing central host routing
is outside this task.

## Claims

| Source and claim | Result | Evidence |
| --- | --- | --- |
| README quickstart, `doctor` validates the installed catalog without extra Python packages | PASS | `python3 -B hub.py doctor` exits 0. The scan replay also runs doctor against its copied catalog after an import and update. |
| README and scan guide, scan inventories existing skills without changes | PASS | `DogfoodTests.test_scan_import_update_doctor_and_links` scans a synthetic Claude home, checks the discovered skill, compares home bytes, and asserts no store exists. |
| Scan guide, confirmed import is repeatable | PASS | The scan replay imports with `--import --yes`, checks `scan-claude` provenance, repeats import, and compares the local manifest. |
| README, connect previews by default and preserves existing settings with backups | PASS | `DogfoodTests.test_import_connect_fetch_update_remove` drives `Fixture.prove()`. It checks preview bytes, rejects unconfirmed writes, compares exact backups, preserves unrelated settings, and repeats the confirmed write. |
| README, connected agents can discover skills and supporting files | PASS | The replay launches the configured Claude and Codex endpoint commands and completes MCP initialization. It discovers all six listing tools and reads the exact imported support file and routine prompt through `hub_get_item`. This proves configuration and protocol, not a native client session. |
| README, updates become readable without maintaining another substantive copy | PASS | Fresh MCP processes read the changed guide after reimport. The scan replay rejects wrong-origin reimport, accepts `--origin scan-claude`, and checks the returned guide text. |
| Reference, imports retain prior revisions and owned removal preserves unrelated content | PASS | The lifecycle replay checks original and updated support bytes in history, removes skill and routine, checks their absence from listings, and compares unrelated rule and client settings. |
| Reference, project links preview without writes and are repeatable | PASS | The scan replay checks preview creates no agent directory, reads updated content through both native skill symlinks, and requires `Nothing to do` on repeat. |
| Routines guide, import does not activate a timer | PASS | The lifecycle replay reads `enabled=false` through MCP. The offline guard prevents subprocess or network effects inside those product steps. No scheduler execution is claimed. |
| README, catalog checks detect changed package content | PASS | The registered `test_endpoint.py` suite includes content-hash rejection and actual stdio requests. Doctor validates shipped hashes. |
| Linux verify loop should use the public export recipe | PASS | `.fleet/verify.sh` now exists, so the runner selects the repository recipe instead of generic full-suite discovery. All 66 `test_verify_loop.py` regressions pass with the restored fake-GitHub fixture. No live CI is submitted. |
| Maintainer instructions, `hub.py check` passes in this export | FAIL | `python3 hub.py check` exits 1 because `config/commit-email-allowlist.txt` is excluded. Doctor passes. Restoring private maintainer policy in a public export is deferred. |
| Maintainer instructions, full unittest discovery passes in this export | FAIL | Baseline discovery encounters the omitted CI helper and many fixtures that copy omitted `config/`. `test_ci.py` now explicitly skips only when its private helper is absent. This does not establish a full-suite pass. |
| CLI guide, generate a CI review copy using `ci_template.py` | DRIFT | That entrypoint is absent from the public export. The guide still describes the maintainer checkout. Exporting or removing this feature needs a separate scope decision. |
| README and release guide, hosted bots and native clients have recorded acceptance | DRIFT | Those are historical, bounded claims. This task does not repeat native agent sessions, OAuth, hosted publication, gateway execution, cron, or deployment. Their current operation remains unverified. |
| Ordinary `cold verify` runs on Linux for this repository | DRIFT | Current central routing selects the PowerShell recipe on Windows. Linux Bash proof is separate. No routing files, runners or services were changed. |

## Fixes and larger follow-ups

The Linux recipe prevents generic verify-loop discovery from pulling private-only
tests into the public gate. The restored `tests/fixtures/verify-loop/fake_gh.py`
makes the shipped verify-loop regressions replayable without contacting GitHub.
The missing CI helper is now an explicit maintainer-only skip rather than an
import error. The new replay is registered in the shared export verification script
and the export file list.

The public README already distinguishes doctor from maintainer checks, but its
contributor commands and CLI guide still need a consistent export story. A future
change should make maintainer fixtures self-contained or clearly separate them
from shipped tests. Do not hide those failures under a claim that the full suite
passes. Central Linux routing requires a separately authorized change outside
this repository. Hosted and native-client acceptance requires authorized live
sessions and remains outside this offline proof.

## Local evidence

The Linux public recipe exits 0 and runs 198 tests across seven discovered suites.
The focused dogfood replay exits 0 with two tests. The focused verify-loop suite
exits 0 with 66 tests. The private-CI import regression exits 0 with one explicitly
reported maintainer-only skip. `hub.py check` exits 1 for the omitted allowlist.
Baseline full discovery exits 1 with 1,304 tests, 392 errors, nine failures and six
skips. That exploratory run overlapped the new export-list edit, so one failure
was the then-unwritten report path. These are not final-head full-suite counts.

The standalone release-check audit reports FAIL for inherited source/history and
release-readiness findings. It does not approve a new product release. A separate
comparison uses that tool's Scanner and the existing private denylist on every
changed file against `origin/main`, reports inherited findings separately, and
requires zero new findings before the source PR push. Private reports and raw
logs stay outside the public source. No finding values or secrets are included
here. The PR records the exact tested head and final gate outcome.
