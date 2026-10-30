# Safety scan

Every scan checks the content it finds before anything is imported. Items with risks become **Needs review** with a plain-language reason. `Import all`, the CLI `--import` and the wizard skip them unless you explicitly approve that one item.

```sh
python3 hub.py scan --safety
python3 hub.py scan --safety --json
python3 hub.py scan --import --approve-risky sc-0123456789ab --yes
```

`--safety` is a read-only report grouped by agent. Each item shows its status, then one line per finding with category, reason and location (`SKILL.md:12`, or `definition` for MCP server definitions). `--json` returns `{version, summary, agents}`; every finding has `category`, `check`, `reason`, `location` and `blocking`. It accepts the same discovery options as `scan` (`--home`, `--project`, `--projects-root`, `--agent`, `--platform`) and rejects `--import`, `--only`, `--yes` and `--approve-risky`.

The scan is local heuristics in `safety.py`. It makes no network calls, runs no imported code and never prints a secret value, only its type and location.

## What is checked

| Category | Check | Flags |
| --- | --- | --- |
| `prompt-injection` | `invisible-characters` | Zero-width, bidirectional-override or invisible-separator characters in skill or rule text. Joiners inside emoji sequences and Persian/Indic words, and a leading byte-order mark, are allowed. |
| | `hidden-instructions` | Instructions to the agent inside HTML comments, `<details>` blocks, `display:none` spans or markdown reference comments. Ordinary maintainer notes pass. |
| | `override-instructions` | "Ignore previous instructions", "disregard the system prompt", "do not tell the user", "ignore the user", role-reset phrases, "exfiltrate". A mention after "do not", "never", "must not" or "should not", or in quotes on a line that refuses it, is defensive and passes, as does "do not tell the user to ...". |
| | `exfiltration-instruction` | Prose telling the agent to send, upload or post API keys, credentials, tokens or private keys to an address. |
| `dangerous-command` | `pipe-to-shell` | `curl`/`wget`/`irm` piped to a shell, interpreter or `iex`; `bash <(curl ...)`. |
| | `eval-download` | `eval`/`source`/`exec` of content fetched from the network. |
| | `base64-exec` | Base64-decoded content piped to a shell or passed to `eval`/`exec`. |
| | `rm-rf-broad` | `rm -rf` on `/`, `~`, `$HOME`, `*`, `.`, `..` or top-level system folders. `rm -rf ./build` and `/tmp/...` pass. |
| | `credential-read` | Reading SSH private keys, `.env` files, cloud credentials, `.netrc`, GPG, keychains or browser profiles. Public keys, `ssh/config` and `.env.example` pass. |
| | `outbound-post` | Posting `env`/`printenv` output, secret-named variables or local files with `curl`, `wget` or `nc`. |
| | `env-injection` | An `env` key that loads extra code at startup: `NODE_OPTIONS`, `LD_PRELOAD`, `PYTHONSTARTUP` or `DYLD_*`. |
| `mcp-package` | `unpinned-package` | `npx`, `bunx`, `pnpx`, `npm exec`, `pnpm dlx`, `uvx`, `pipx run` or `uv tool run` without an exact version (`@1.4.2`, `==2.0.1`). `latest`, ranges and partial versions count as unpinned. |
| | `package-from-url` | A package or `--from` source that is a URL or `git+` address. |
| | `install-step` | Server commands that run `npm install`, `pip install` and similar at startup. |
| `mcp-remote` | `unknown-remote` | A remote URL that is not loopback and not an already-known upstream, in `url`, `args`, `env` or `headers`. `0.0.0.0` is not loopback. |
| | `unencrypted-source` | A plugin source over plain `http`. |
| `secret` | `embedded-secret` | The secret detector (`checks/scan.py`) on skill and rule text. Reports type and location only. |

A remote URL is known when its host appears in the locker manifest's MCP server definitions or in a scanned upstream endpoint. Connection records are metadata only and are not trusted as known hosts.

### Secret detector

One table in `checks/scan.py` is shared by `hub.py check`, the scanner and the safety check. It matches GitHub, OpenAI, AWS, Slack, bearer and private-key shapes, plus Stripe `sk_live_`/`sk_test_`/`rk_live_`/`rk_test_` keys, Google `AIza` keys, GitLab `glpat-` tokens, Hugging Face `hf_` tokens, npm `npm_` tokens and three-part JWTs. Placeholders such as `<your-token>`, `${TOKEN}`, `$TOKEN`, `secret://name`, `...`, `xxxx` and `changeme` never match. There is deliberately no generic `key = value` rule: it cannot tell a literal from a variable or an example and would flag the repository's own `secret://` references.

### Env, headers and args

Scans review original `env` and `headers` values before replacing them with opaque `secret://` references; nonempty values are stored privately on import. Risky commands or remote URLs in a nonsecret setting still need review. Values under a sensitive key, or matching a known secret pattern, count as credentials; a short setting such as `NO_COLOR=1` does not.

In command-line `args`, a credential is the value after `--api-key`, `--token`, `--secret`, `--password`, `--auth` (and their `access-`/`auth-`/`bearer-`/`client-` variants), the value of an inline `--flag=value`, or any argument matching the detector. The scan moves it into the private store, rewrites the argument to `secret://scan-<hash>` (`--flag=secret://scan-<hash>` inline) and counts it in the item's `secrets`. Existing `secret://` references, `$VAR`/`${VAR}` expansions, `<placeholder>` text and other flags are left alone. A definition that still holds a literal credential in `args` is refused with `credential in command-line args` by `validators.mcp` and `hub.py import`, and `hub.py check` reports token-shaped ones. Pass such a value through `env` instead.

A locker imported before this check may still hold such a copy. A rescan lists it as `new` (`replaces the stored copy that had a credential in its args`) and `python3 hub.py scan --import` overwrites it and moves the value into the private store. Until then Sign in marks the server "Cannot be used" and the gateway renderer and `hub_list_mcp_servers` omit its definition.

### Warnings are not findings

A dangerous command on a line that starts with an explicit negation (`Never`, `Do not`, `Avoid`, `Warning:`), with no sentence break before the command, is treated as documentation. A comma, `then`, `and` or `but` ends the negation like a sentence break, except a lead-in such as `for example` or `such as`. A warning on another line, or after the command, suppresses nothing.

## Results and approval

| Finding | Status | Import all | Explicit approval |
| --- | --- | --- | --- |
| Non-blocking (injection, command, package, remote) | `needs_review` | Skipped | `--approve-risky ID` or the row's "Import anyway" checkbox |
| Embedded secret | `skipped` | Skipped | Not possible; remove the secret from the source |

Clean items import as usual. Approval is per item: an approved ID imports only that item, and an ID selected without approval is rejected and reported. Items already in the locker, duplicates and conflicts keep their status, and their findings still appear in `--safety`.

A connection derived from an MCP server inherits that server's status and reasons. It is skipped by `Import all` and imports only together with its server; approving the server approves both, and the connection cannot be approved alone.

The wizard's Scan screen shows **Needs review** with reasons and an unchecked "Import X anyway" checkbox; `Import all` sends only clean items, and `/api/scan/import` takes an `approved` list that must be a subset of the selected IDs.

## Limits

This is heuristic triage, not a guarantee. It does not detect obfuscation beyond base64, instructions in other languages, malicious supporting scripts that match no pattern, or a compromised pinned package. Needs review means a person should read the item; a clean result means only that nothing matched. The module is stdlib-only with no network use, so it avoids existing scanners that start upstream servers or call hosted classifiers. Fixtures are generated at runtime in `tests/safety_fixtures.py` and asserted in `tests/test_safety.py`.
