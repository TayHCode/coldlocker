# Product release readiness and local evidence

## Candidate and scope

Delivered source baseline: `9517ac934939c3a137597242a4fa9ac3ed426114`.
Release-readiness changes remain unreleased and must be identified by their
source digest as well as that baseline until committed. The earlier proposal
to release a personal local beta as `v1.0.0-beta.1` is superseded: it does not
satisfy the motivating cross-agent product request. No release tag has been
created or version approved. The endpoint's existing `serverInfo.version` value
`1.0.0` identifies its implementation; it does not attest to this candidate's
release status. See the [changelog](../CHANGELOG.md) and [security policy](../SECURITY.md).

The product target is an easy central skills and routines library that another
user can set up and reach from local agents and hosted bots, alongside existing
Composio MCPs. [Product acceptance](product-acceptance.md) defines the required
journey. A self-hosted installation is possible; a managed multi-user service
has not been selected.

The initial release shares skills and routine instructions for agents to fetch
and execute on request. Automatic timers remain optional runner integrations;
the unsuccessful Grok native timer trial does not block that content-sharing
scope. The initial supported evidence covers Claude Code, Codex CLI and Grok
Bot, with the limitations recorded below.

The verified local engineering milestone covers Linux with Claude Code and
Codex. The dated result below records their model-driven catalog acceptance;
it does not complete the product release target. Its local journey is:

1. Use a clean checkout and run `python3 hub.py check`.
2. Import reviewed skills, rules, routines, and MCP definitions into the private
   catalog. Read the import plan and provenance; import does not run servers,
   authenticate providers, install native skills, or enable routines.
3. Start the read-only stdio catalog from the checkout and connect a local
   client using `python3 hub.py connect` output or the documented manual entry.
   Verify that the client lists the catalog and can read an imported item.
4. Keep private imports and the Store local. Enable a local routine only as a
   separate, explicit action after reviewing its plan.

See [install](install.md), [clients](clients.md), [formats](formats.md),
[routines](routines.md), and [acceptance](acceptance.md) for commands and limits.
The local evidence does not prove the gateway or remote catalog journey.
Hosted reachability is part of the product acceptance target and cannot be
excluded solely to call the existing local milestone a release.

The additional full-journey fixture with a symlinked temporary directory is
quarantined on Windows: native CI repeatedly fails its history-retention
assertion, and canonicalizing the scan paths did not resolve it. This variant
remains unresolved and provides no accepted Windows evidence. The normal
lifecycle, CRLF history, interpreter-alias and Windows ACL tests remain required.

## Recorded cross-agent staging and routine execution

The 2026-10-21 staging trial used an isolated deployment of the reviewed
331-file source snapshot with SHA-256
`4256c6a27c25219aa50dcf7b03ae7d31e04079065d278da323264d1390428cf8`.
Later documentation edits are not part of that deployed snapshot. Its isolated
Worker build passed typechecking, 184 remote tests and a Wrangler dry-run.
Metadata returned 200, anonymous catalog access returned 401, and authorization
redirected to owner sign-in. Private evidence retains the Worker version and
catalog revisions without publishing account or authentication details.

A user-supplied Grok Bot Mac screenshot showed a connected catalog account.
User-pasted task responses correctly used two synthetic skills with supporting
files, then retrieved an updated guide and routine prompt without reconnecting.
Local Claude independently observed that update. These are bounded owner-trial
results; raw hosted tool events and an independent user's setup were not captured.

A single native T3 manual trigger then invoked controlled local jobs using the
isolated stdio catalog. Codex CLI, requesting `gpt-6.1-sol`, exited 0 in 15.164
seconds; Claude Code, reporting `claude-haiku-5-5`, exited 0 in 8.850 seconds.
Both freshly fetched the routine, skill and supporting files and returned the
exact source-derived result. Each endpoint log records three `hub_get_item`
calls and one each of the skills, rules and routines listings. The thin
instruction contained neither the substantive prompt nor the expected answer.
Codex's transcript identifies the routine and skill arguments; its actual model
identifier is unavailable. Claude's terminal JSON lacks individual tool arguments,
so its argument-level evidence is weaker than its endpoint counts and result.

The local wrapper supplied the 120-second timeout and overlap guard; native T3,
Codex and Claude app equivalents are not established by this run. Both jobs
ended inactive. The T3 task remained disabled and had no next run before and
after its manual trigger. This proves that manual-trigger execution path and
disabled state, not automatic interval firing or an enabled-to-disabled timer
transition. The portable definition remained disabled and unchanged.

Inspected Codex configuration hashes were unchanged. Claude's own JSON state
file hash changed during its session. No matching pre-run backup was found, so
field-level preservation for that file is unverified; no manual configuration
edit or restore was performed. Normal CLI cache and authentication-refresh
effects are not inventoried. Do not reuse the earlier local trial's stronger
backup comparison as evidence for this later session.

The limited native Grok test was reported created paused, with unavailable
overlap and timeout controls explicitly excluded. The owner subsequently
unpaused it; no run appeared and the bot repaused it. A screenshot confirms the
final Paused state. Native dispatch and background catalog fetching remain
undiagnosed; this trial did not pass. Native Codex and Claude app scheduling
and T3 remote OAuth remain optional integrations; an independent fresh-user journey is still
unverified. See
[product acceptance](product-acceptance.md) and [runner limits](routines.md).

## Permanent installation

Use the owner's primary clone as the permanent installation, after its update
is approved. Keep that path stable: generated connection entries contain an
absolute endpoint path. Disposable review worktrees are for development;
`connect` warns for linked Git worktrees as well as `.scratch` and `.worktrees`
paths. The warning does not prevent an explicitly confirmed write.

After the product acceptance gates are met, the source release sequence is:
approve the candidate, commit and open a PR,
pass CI on that exact head, merge, verify the reviewed tree and create the
approved annotated tag. Before updating the primary clone, privately back up
its Store, configuration and evidence as described below. With a clean primary
clone on the approved branch, use `git pull --ff-only`, verify that its source
matches the approved release revision, then run `python3 hub.py check`.
Following a moving branch alone does not prove the installed release version.

From that permanent checkout, preview the two local connections:

```sh
python3 hub.py connect --agent claude codex
```

Verify the endpoint path before any owner-approved `--write` action. Then
check discovery with `claude mcp get agentlocker` and `codex mcp list`; these
checks do not require another model session. Compare other client settings
against their private backups without printing account or authentication
values. Import only reviewed owner-selected content and leave routines
disabled. Copy private evidence out of a worktree before any later authorized
retirement; Git does not preserve ignored `.scratch` files in a release tag.

## Runtime and client requirements

- Git and Python 3.10+; use Python 3.11+ when importing or writing Codex TOML
  configuration. On Windows, use Python 3.12.4+ so the private Store receives
  owner-only permissions. Use `py -3` or `python` if `python3` is unavailable.
- Node/npm and network access are not needed for the local stdio catalog.
- Select two distinct real local client products for acceptance. Each must
  discover the same catalog, read a skill and one supporting file plus a
  routine, then complete the intended user task; record product versions and
  exact actions separately below. Repeat acceptance for a changed supported
  client or journey. Product logins and model/provider access are independent
  of the catalog connection. Hosted
  clients and other integrations remain unverified unless their own acceptance
  records say otherwise.

## Recorded local client acceptance

On 2026-10-21, two actual local products completed the same read-only task on
Linux/Python 3.14.4 using existing product logins and a synthetic imported
catalog in a copied runtime:

- Claude Code 2.1.306 reported model `claude-haiku-5-5`; session exit 0.
- Codex CLI 0.163.0 requested `gpt-6.1-sol` explicitly; session exit 0. Its
  captured event stream does not expose the returned model identifier.

Each client called `hub_list_skills` and `hub_list_routines` once, then
`hub_get_item` for the imported skill and routine. Client tool inputs/results
and the catalog's request log agree. Both listed four skills and two routines,
quoted the exact supporting-file line, and correctly reported the routine's
owner, preferred runner, timezone, disabled state and prompt. No shell or other
execution tools were used.

Execution used baseline `9517ac934939c3a137597242a4fa9ac3ed426114` plus the
reviewed local changes. The copied runtime/catalog/proof digest was
`1eaf45fca8d8bda2f06bea5079d24551e218ce0b9eab7215284bcf36788ad833`.
The private acceptance record retains the whole-candidate fingerprint, exact
argv, versions, outputs, call logs and preservation checks; credentials and
account identifiers are excluded from this documentation.

Synthetic catalog bytes and real MCP/account settings were preserved. Claude
Code refreshed six cache/onboarding fields in its own JSON state file; an
automatic backup exactly matched its pre-run bytes, and all other fields were
unchanged. Codex configuration was byte-identical. No manual real configuration
write or restore was performed.

This establishes the local stdio read-and-use task for these two client
versions. It does not establish a fresh user's clean installation, permanent
client configuration, native skill activation, other operating systems, shared
upstream-provider authentication, hosted access or scheduled execution.

## Local proof and product proof

Run the offline local catalog proof from the candidate checkout with Git and
Python 3.11+ (3.12.4+ on Windows):

```sh
python3 examples/test-servers/local_catalog_proof.py
```

It exercises a clean fixture install, import with supporting files, stdio
catalog reads, client configuration preservation, update and remove, and emits
a JSON summary. Record the actual exit status and summary for each candidate;
the existence of the script is not a passing result. The proof uses fixtures and
does not establish real client behavior, product authentication, provider calls,
remote deployment provenance, or hosted scheduling.

Keep the proof summary in ignored private evidence. Record source SHA, OS and
Python version, exact command and exit status, JSON summary path, fixture
boundary, and skipped checks. Keep this record separate from the two actual
client records below.

For each client, import a reviewed skill package containing a supporting text
file and a disabled routine with a prompt. Use a stable installation checkout,
not a disposable review worktree. Print and inspect its connection entry before
any approved configuration write. In both clients, request the same task:

> List the locker skills and routines. Read the imported skill and its
> supporting text, quote a known nonsecret line from that supporting file,
> then follow the skill to summarize the approved catalog. Read the imported
> routine and report its owner, preferred runner, timezone and disabled state.
> Do not enable or execute the routine or any upstream server.

Check the quoted line and resulting summary against the approved source files;
seeing a tool name alone is insufficient. For each client, capture:

```text
Client and exact version:
OS and Python version:
Candidate source SHA:
Catalog entry method and exact UI/CLI actions:
Expected and observed listed tools/items:
Skill, supporting file, and routine read; observed results:
Intended task and observed outcome:
Login/provider state (describe status only; never include credentials):
Exit status or client error:
Evidence path and timestamp:
Unverified behavior and follow-up owner:
```

Do not copy tokens, account identifiers, private config contents, or raw
authentication responses into tracked documentation.

## Private state, upgrade, and rollback

Before an upgrade, close catalog clients and make a private, access-controlled
backup of `.hub-store/` and ignored `config.json`, plus the configured Store
directory if it lives elsewhere. This preserves catalog content, import
history, source-path bindings and private state together. The default Store is
under `.hub-store`; `config.json` can point elsewhere. Preserve restrictive
directory/file permissions (700/600
where supported), keep backups outside Git, and verify the backup can be read
without exposing its contents. Back up client configuration separately only
when changing it; connect already makes a timestamped backup before modifying
an existing file.

Before relying on that backup, restore it into a separate directory and fixture
home. Repoint a copied `config.json` to the restored Store before running any
command, so an absolute `store_path` cannot reach the live Store. Check catalog
integrity and permissions there without connecting clients, starting a gateway
or activating schedules. Record the restore result privately; the fixture
catalog proof alone does not establish backup recovery.

Upgrade from reviewed source and run `python3 hub.py doctor` before importing.
Maintainers additionally run `hub.py check` on their source checkout.
Use the documented same-owner/same-origin update path; it keeps prior imported
content in `.hub-store/history/` and preserves compatible MCP source bindings.
Review conflicts and changed prompts before proceeding. Do not reset or replace
the entire Store to solve a catalog issue.

For rollback, stop the affected local process and identify the exact changed
item or binding. Prefer the import lifecycle's retained history and recovery
copies; preserve state written since the backup. Restore only the affected
catalog item/manifest entry and its matching private binding from a verified
backup, retaining permissions, then rerun `hub.py doctor` and inspect the item
list. Do not blanket-restore an older Store over the live Store: that can erase
new credentials, consent, history, or routine records. If the changed scope
cannot be isolated safely, keep both copies private and request maintainer
review before replacing live state.

## Release gates

Require the [product journey](product-acceptance.md), passing local checks on
the final candidate, passing CI on the exact proposed release head, a reviewed
merge tree, the approved tag and an installation record. That record
must include the source/tag, CI URL, actual test counts and skips, permanent
checkout path, connection backup locations, imported item identifiers and
remaining exclusions. The fixture restore drill and existing-login acceptance
do not establish a real-state recovery or a clean new-user installation.

Before distributing an artifact, review the files actually included and obtain
publication approval. A reviewed source-only archive excludes Git history,
private state and evidence; it does not require distributing the repository.
For a separate public source copy, use the exact paths in
[`public-export.txt`](../public-export.txt). Preparation notes and review
receipts remain in the private maintainer checkout. The list alone does not
establish export readiness or authorize publication.
Before distributing repository history or a Git bundle, resolve its historical
private-material finding and approve the publication policy. Current scanners
do not sanitize old blobs. Do not publish history while that gate is open.

Proposed roles: the repository owner approves the release and any
privacy/history disposition; the project orchestrator prepares the candidate,
executes acceptance and retains private evidence. These responsibilities do
not imply that release, history, real-configuration or publication approval
has been granted. Confirm ownership and the reporting route before release.

The isolated staging record establishes bounded hosted catalog reachability
and source/content provenance, not a supported production installation. Routine
evidence is limited to the recorded T3 manual-trigger path; native app scheduling
and an automatic timer run remain unproved. Composio custom
toolkit interoperability is required if that delivery path is advertised;
preserving existing Composio connections is required regardless. Replacing
existing MCP infrastructure or proving every optional gateway and Executor
feature is not a prerequisite for the central skills/routines journey.
Executor, MCPHub, Composio, agents-sync, the setup wizard and the remote Worker
remain in the tree; inclusion alone is not acceptance for their live journeys.
No import, connection preview, or documentation step
automatically activates an external service, publishes content, or enables a
schedule.
