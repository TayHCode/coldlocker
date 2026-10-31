# agentlocker CLI

Run `python3 hub.py` from the repository root. Python 3.10+ and the standard library are enough; verification also uses Git. Every command takes `--help`. Exit codes: 0 success, 1 rejected input or failed checks, 2 invalid syntax.

Commands: `scan`, `connect`, `connect-composio`, `list`, `remove`, `import-skill`, `import-rule`, `import-mcp`, `import-routine`, `import-plugin`, `import-connection`, `import-executor`, `link`, `deliver-rules`, `routine`, `publish-remote`, `agents-import`, `agents-export`, `agents-sync`, `serve`, `check`.

On Windows, `python3` may be a Store stub; use `python` or `py -3`. File locks use `msvcrt`. The private store is owner-only on Windows only when Python 3.12.4+ creates it (`mkdir` mode `0o700` sets a protected ACL for SYSTEM, Administrators and the owner); older Pythons leave the parent folder's permissions, so use 3.12.4+. Starting the gateway on Windows is covered in [gateway.md](gateway.md).

## Scan and import

```sh
python3 hub.py scan
python3 hub.py scan --json
python3 hub.py scan --import
python3 hub.py scan --import --only sc-0123456789ab --yes
python3 hub.py scan --safety
python3 hub.py scan --import --approve-risky sc-0123456789ab --yes
```

`scan` is a read-only inventory grouped by agent. `--import` confirms and imports all eligible items; non-interactive runs need `--yes`. `--only` and `--yes` require `--import`. `--approve-risky ID...` (requires `--import`) imports flagged items, never one with an embedded secret. `--safety` prints the [safety report](safety.md) and cannot combine with import options. Discovery flags (`--project`, `--projects-root`, `--home`, `--agent`, `--platform`, `--all`) and statuses are in [scan.md](scan.md). Invalid input exits 1.

`import-plugin <metadata.json>` and `import-connection <metadata.json>` accept strictly validated metadata-only records with `--owner` and `--origin` (see [formats](formats.md)). They never install code, authenticate or contact endpoints.

## Import a skill

```sh
python3 hub.py import-skill .scratch/incoming/example --owner agentlocker --origin upstream
```

The package needs `SKILL.md` with closed `---` frontmatter holding a `name` and nonempty `description`. The name must match the source directory or `--name`: lowercase letters, digits and hyphens, starting with a letter or digit, at most 63 characters. Symlinks anywhere in the package are rejected. Every regular file to copy is checked for secrets and private text, including directories scan skips, such as `node_modules`. The package is copied, minus `.git`, to `.hub-store/catalog/skills/<name>/`.

Reimporting with the same owner and origin replaces the whole package after verifying its stored hash. The previous copy is kept until publication succeeds and restored on failure. An unchanged indexed destination is a no-op.

## Import a rule

```sh
python3 hub.py import-rule .scratch/incoming/policy.md --name shared-policy --origin authored
```

Copies a Markdown file to `.hub-store/catalog/rules/<name>.md`. It applies scan's private-text validation: your own home path prefix becomes `~`, and any other finding (IP, hostname, email, secret, another user's home path) refuses the import with `REFUSED: definition contains <category> at line <n> (<file>)` and writes nothing. The default name is the lowercased source filename without `.md`; an invalid name asks for `--name`.

`import-skill` and `import-rule` take `--name`, `--owner` (default `agentlocker`) and `--origin` (default `local-import`). Owner and origin are short labels, never paths. Each import records a content SHA-256 and an opaque `src-` id (first 12 hex characters). Hashing and manifest layout follow [formats.md](formats.md).

## Import MCP metadata

```sh
python3 hub.py import-mcp .scratch/incoming/example-server.json --origin upstream
```

Records a nonsecret stdio or HTTP definition ([formats](formats.md)) that the gateway and listing endpoint can serve ([gateway](gateway.md)). It never starts or contacts the server or resolves a secret. Sensitive env and header keys need `secret://<name>` references; secret-looking values, private paths or names and unknown fields are rejected. The result goes to `.hub-store/catalog/mcp/servers/<name>.json` with a manifest entry. Set secret values separately in the local store. Supports `--owner` and `--origin`.

Reimports keep existing source-path bindings while the portable command and args still match. Changing a bound field clears that server's bindings and unused private references. An orphan binding without a manifest entry is refused; reconcile that local metadata before reusing the name.

## Import routine metadata

```sh
python3 hub.py import-routine .scratch/incoming/routine.json --origin authored
```

Records a definition only; it never schedules, enables or runs anything. Input needs one owner (a different `--owner` is rejected), one preferred runner from [routines.md](routines.md), a valid schedule (`interval`, `fixed_time`, `cron`, `webhook`), an `overlap` of `skip`, `queue` or `allow`, and `enabled: false`. The definition goes to `.hub-store/catalog/routines/<id>/routine.json`. `prompt` must name an existing ordinary sibling file. It is validated and copied beside the definition; a missing prompt is rejected before the catalog changes.

The manifest hash covers `routine.json` only. Prompt files are not hash-indexed, so a same-owner, same-origin update may replace the prompt, including local edits; save those first. A new prompt name cannot overwrite an unrelated sibling, and a new routine cannot adopt an existing routine directory. Renaming a prompt keeps the old file. Definition and prompt bytes are restored on write failure.

## Safety rules for imports

All import commands refuse shipped names, unindexed destinations, owner or origin changes, inconsistent provenance, missing indexed content, symlinks, nonordinary destinations, and content that differs from its recorded hash. A stored skill containing `.git` content is refused because those bytes are outside the v1 hash; empty skill directories are also outside it and may vanish on update. Conflicting files are never adopted: preserve them separately before retrying.

Same-owner, same-origin updates are allowed when the stored copy matches its manifest. Content, manifest and explicit MCP path bindings are prepared before publication with rollback copies; a failed write restores the previous bytes and private references, and a failed new import removes its partial content. If restoration fails, the command names the `.import-*` directories holding recovery copies. Snapshots are rechecked before each replacement to refuse intervening changes. Imports take no locks and have no crash recovery, so avoid concurrent catalog writes.

## List, update and remove

```sh
python3 hub.py list [--kind skills|rules|mcp_servers|routines|plugins|connections] [--json]
python3 hub.py remove skills example --yes
```

`list` shows shipped and imported items with kind, name, owner, origin and shipped/imported. `remove <kind> <name>` deletes an imported item's files and local manifest entry with the same staged, rollback-safe publish as import. It refuses shipped items and items owned by another owner (pass that `--owner`), and needs `--yes` or an interactive confirmation. Links made by `hub.py link` that would dangle are listed; link-target files are never deleted.

Reimporting a changed source with the same owner and origin updates the item in place (the command `scan` prints for a changed item). Replaced and removed content is kept under `.hub-store/history/<kind>/<name>/<UTC timestamp>/`. A different owner or origin is refused and the message names the values to pass.

## Link skills

```sh
python3 hub.py link --target .scratch/test-root --dry-run
python3 hub.py link --target .scratch/test-root
```

Creates relative symlinks under the target's `.claude/skills/` and `.agents/skills/` for every skill in the merged catalog: shipped skills and skills imported into `.hub-store/catalog/`. All destinations are checked before any write. Refused: conflicting files, directories or links; symlinked parent directories below your home or temp directory (system links such as macOS `/var` above them are fine); the home directory itself; targets under home that are not inside a project containing `.git`; and targets inside home agent config trees. Correct existing links are kept, and a second run prints `Nothing to do`. `--dry-run` prints proposed links, one line each.

To link into your own home skill folders, opt in with `--home`:

```sh
python3 hub.py link --home --dry-run
python3 hub.py link --home --yes
```

`--home` links every skill into `~/.claude/skills` and `~/.agents/skills`. It confirms at a terminal and needs `--yes` otherwise. Unfamiliar files, directories and links there are reported as `Conflict` and skipped while the rest link. Created links are recorded in `.hub-store/links.json` so `remove` can warn about dangling ones.

## Connect clients

```sh
python3 hub.py connect
python3 hub.py connect --agent claude codex
python3 hub.py connect --write --yes
python3 hub.py connect --remote [--agent grok chatgpt ...] [--url https://catalog.example.org/mcp]
```

`connect` prints settings for detected clients and writes nothing. `--write` is the one opt-in exception: after `--yes` or interactive confirmation it backs up and merges only the `agentlocker` and `agentlocker-gateway` entries into detected user configs, keeps other settings, and rejects symlinked or non-ordinary destinations. `--remote` prints OAuth settings for hosted clients independent of local detection, never reads or writes client settings, and refuses `--write`. Supported clients and the hosted-bot status are in [clients.md](clients.md) and [remote.md](remote.md#connect-a-client).

Remote catalog entries use `public_url` in `.hub-store/remote.json` or the
explicit `--url` override. `routine enable --remote` and `connect-composio
--catalog` use the same setting/override. Missing configuration gives setup
instructions; no owner endpoint is assumed. `--url` is rejected for local
`connect` and nonremote routine actions.

## Composio

```sh
python3 hub.py connect-composio --catalog
python3 hub.py connect-composio --catalog --write --yes --api-key-ref secret://composio-key
python3 hub.py connect-composio --url <MCP_URL> --name <NAME>
```

`--catalog` prints the remote-catalog OAuth registration and next steps, so locker skills are reachable wherever Composio reaches. `--write --yes` is the opt-in that registers only the catalog toolkit through the Composio API using a `secret://` project-key reference. Without `--catalog`, the command registers a Composio upstream definition. See [composio.md](composio.md).

## Routines

```sh
python3 hub.py routine enable inventory-digest --runner cron --agent-command 'codex exec'
python3 hub.py routine enable inventory-digest --runner cron --agent-command 'codex exec' --write --yes
python3 hub.py routine status
python3 hub.py routine disable inventory-digest --runner cron --write --yes
python3 hub.py routine enable ID --runner RUNNER --remote
```

`enable` and `disable` print plans by default; `--write` needs `--yes` or interactive confirmation. Local adapters install owned jobs with backups; hosted adapters print prompts and schedules for activation in the chosen app. Definitions stay disabled; per-runner state lives in the private store. `--remote` prints an activation prompt that fetches the published routine on each run; it refuses local runners, `--write` and disable. See [routines.md](routines.md).

## Publish, serve, agents-sync

- `publish-remote [--dry-run] [--include-local] [--yes] [--via cf|api]` builds and verifies the manifest-listed catalog and uploads an immutable revision to the remote server's Workers KV store. `--include-local` also publishes private `.hub-store` imports, listed and confirmed first. See [remote.md](remote.md).
- `serve [--port N]` opens the local setup wizard ([ui.md](ui.md)).
- `agents-import`, `agents-export`, `agents-sync` delegate to amtiYo/agents ([agents-sync.md](agents-sync.md)).
- `import-executor` makes an optional nonsecret copy from a live Executor ([executor-connector.md](executor-connector.md)).

## Check the hub

For an installed catalog, use `python3 hub.py doctor`. It checks schema, MIT
license, catalog hashes, private-state layout and catalog privacy, including
routine prompts. It works from a source snapshot without Git metadata and
does not enforce maintainer commit identities or scan unrelated source and
dependency files. It does not establish hosted connectivity or routine execution.
In a Git installation it still refuses tracked or unignored private state.

Source-export contributors run the supported verification recipe:

```sh
python3 -B tools/verify_export.py
```

It requires Git and symlink permission. See [Contributing](../CONTRIBUTING.md)
for scope and platform prerequisites.

The private maintainer checkout runs the stricter checks below. They require
omitted `config/` policy and fixtures and are not supported in this source export.
`doctor` does not replace them or weaken the maintainer identity policy.

```sh
python3 hub.py check
python3 -m unittest discover -s tests -v
```

`check` prints PASS/FAIL for six checks, and every one must pass for exit 0:

1. manifest schema;
2. MIT title and permission grant;
3. manifest paths exist and content hashes match;
4. `config.json` and `.hub-store/` are ignored and untracked;
5. secret and private-path scan;
6. commit emails.

Commit emails: every author email in the checked-out HEAD history must be in `config/commit-email-allowlist.txt` (one address per line). Committers must be listed too, except the exact GitHub web-flow address (`noreply` at `github.com`) when the same commit's author is listed. This reads local commit metadata only, with no network or signature check, and never permits an unlisted author. In pull-request CI, runtime jobs test the merge tree; only when HEAD's two parents match the event's base and head does validation use the base..head range, excluding the synthetic merge identity. Other runs scan HEAD and its ancestors, including detached HEAD. Unrelated branches, remote-tracking branches and tags are outside this checkout check. A branch with no commits has no history to validate. The email check prints SKIP outside a git checkout.

Commit-text CI accepts an allowlisted email only in a complete `Co-authored-by`
trailer. Known automation trailers retain their existing exceptions. Inline
addresses, private hostnames, tokens and local private patterns remain checked.
CI currently runs on Linux only. The Windows verification helper requires Bash beside the Git installation and exports that
directory to later job steps, ahead of the system WSL launcher.
The Windows helper uses the runner's job temp directory for test fixtures so long
checkout paths do not push routine imports and history beyond Windows limits.

Scan failures report file, line and rule name, never the match. Git repositories scan `git ls-files`; elsewhere the scan walks files except `.git`, `.scratch`, `.worktrees`, `.hub-store` and `.agent-hub-ops`. The ignore check needs a Git repository.

The scanner detects GitHub, OpenAI, AWS and Slack token shapes, bearer values, private key blocks, absolute home paths, IPv4 and tailnet addresses, plaintext emails and private hostnames. Numeric single-label hostnames match alone or near host/network context. `.internal`, `.lan` and `.ts.net` match in prose; `.local` and `.home` need nearby host/network context. Semver-shaped package names and common encoding labels are exempt, as are the reserved email domains (`example`, `invalid`, `test`, `example.com`, `example.net`, `example.org`), the fixture labels `example-01`, `fixture-01`, `test-01`, and `config/commit-email-allowlist.txt`. The scanner cannot prove arbitrary text holds no secret; keep values in the ignored local store.

Add your own names, handles and host labels to the ignored `.hub-store/private-patterns.txt`, one case-insensitive regular expression per line. `check` reports tracked lines matching any as `private-pattern` without printing the match, and fails on an invalid pattern. The file itself is never tracked or scanned.

## Deliver rules and proven lessons

```sh
python3 hub.py deliver-rules --target <repo> --rule <name> --dry-run
python3 hub.py deliver-rules --target <repo> --rule <name>
```

Import records content only. Delivery copies selected rules into
`.coldlocker/rules/` and maintains an `AGENTS.md` section directing agents to
read them. No human review flag is required. Preview shows the selected files
and proposed section. Commit the delivered files, `.coldlocker/rules.json`
ownership hashes, and instructions in the target repo.

Coldfix lessons must carry a versioned four-part JSON record containing source
evidence and what went wrong, the fix, the prevention rule, and replay evidence.
The proof names the before and after commits and trees, expected failure,
FAIL/exit 1 before, PASS/exit 0 after, receipt reference, and the check digest.
Privacy and release-check must both have passed. Incomplete records are refused
before writes; the Coldfix event handler retains them in its private pending list
and log and retries on a later event. There are no approval cards or schedules.

Delivery marks eligible lessons for the verify loop. Ordinary fragments remain
instructions only. Repeating delivery is idempotent. An unchanged owned snapshot
may be updated automatically; foreign or modified files and symlinks are preserved.
Existing instructions remain. Repository-specific exports are explicitly bound to
the selected target by the event handler's configuration. Private source details
remain in Coldfix; opaque references and sanitized summaries travel with the rule.
The local exporter and checker are trusted code. These records preserve their
proof; they are not a cryptographic signature for arbitrary third-party files.

Failed, unsafe, or retired latest revisions are withdrawn automatically by the
Coldfix event handler. `deliver-rules --target <repo> --rule <name> --withdraw`
archives an unchanged owned snapshot under `.coldlocker/archive/` and removes
its managed instruction link and active registration. Imported Coldfix copies
are also removed from the catalog with their history retained. Foreign or edited
files are preserved and the item remains pending. Interrupted writes stay pending
and fail verification until a later event completes them.

Ownership fingerprints normalize CRLF to LF across Git checkout conversion.
Each replacement rechecks its destination to preserve edits made during delivery.
The managed instructions tell agents to apply only completed registrations.

Rule delivery retains replaced file inodes under `.coldlocker/backups/` before
installing with a no-clobber operation. Concurrent replacements are preserved;
conflicts leave the registry pending for retry. Unchanged delivery creates no backup.
Durable write intents restore a missing destination from its retained version
before a later delivery reads instructions or ownership.

Backup contents are ignored by Git. Atomic recovery records and a pending marker
keep an interrupted delivery recoverable and prevent verification until retry.
