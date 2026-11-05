# Local catalog and gateway acceptance

## Offline local catalog proof

From the candidate checkout, run:

```sh
python3 examples/test-servers/local_catalog_proof.py
```

This standard-library proof requires Git and Python 3.11+ (3.12.4+ on Windows)
and uses a disposable copy of reviewed source and
fixture homes to exercise import, supporting-file reads over stdio MCP,
client configuration preservation, update history and owned removal. It needs
no Node runtime, product login or provider access. Its JSON result identifies
the tested source and separates fixture evidence from product-client evidence.
It does not start a gateway, enable a routine or contact a remote catalog.
The stdio exchange follows the [MCP initialization lifecycle](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle):
it reads and validates the initialization response before sending the initialized
notification or ordinary tool requests. A timeout or unsupported negotiated
version fails the proof.

The separate [local client acceptance record](release.md#recorded-local-client-acceptance)
covers one successful real Claude Code and Codex session on the synthetic
catalog. Repeat the intended task for a new supported client or journey using
the [release guide](release.md); an offline fixture pass alone is insufficient.

## Gateway fixture proof

The acceptance runner is fixture evidence. It uses the pinned MCPHub 1.1.7
runtime and a local fake OAuth/MCP upstream, no product account logins, and no
real tool provider. Provider consent and model-driven client acceptance are
separate gates (see the end).

## Run

Requirements: Git checkout, Python 3.10+, npm, Node 24 (the installer enforces
Node 20 minimum), and `ss` (Linux) or `lsof` (macOS).

```sh
python3 gateway.py install
python3 examples/test-servers/acceptance_proof.py > acceptance-summary.json
python3 examples/test-servers/acceptance_proof.py --install-runtime > acceptance-summary.json
```

The second form installs the runtime inside the temporary checkout and needs
registry access on each run. Both use `npm ci --ignore-scripts`.

The runner copies tracked working-tree source and the installed runtime into a
temporary checkout, so run it from the final integrated checkout. It copies no
Store, `config.json`, client config, logs or logins. HOME, USERPROFILE and XDG
directories point into the fixture, product credential variables are dropped,
and listeners use ephemeral IPv4 loopback ports. The summary is sanitized JSON
that records Git HEAD and a source digest; keep it out of commits. Missing
prerequisites or failed stages exit 1. Expect a few minutes.

## What the run asserts

- A fake HTTP MCP definition imports into a private catalog; the stdio endpoint
  answers all six `hub_list_*` tools without initializing the upstream.
- Discovery, dynamic registration, consent and PKCE run through the setup HTTP
  API with exactly one registration, authorization and code exchange.
- Two concurrent client processes call `add(2, 3)` through the aggregate
  gateway; the upstream counter rises by exactly two. Clients select
  `oauth-demo-add` explicitly, so the unprotected bundled server cannot satisfy
  the check.
- Stopping, restarting and rebuilding runtime settings from the Store keeps calls
  working on the original credentials.
- Expiry plus upstream revocation makes calls fail; a token-endpoint 503 makes
  refresh fail while credentials and settings are preserved; a retry rotates
  tokens and invalidates the old refresh token. An MCP-upstream 503 fails a call
  and recovers after restore and gateway restart.
- With `--auto-refresh`, the supervisor recovers an expired, revoked binding:
  tokens rotate and the Node child is replaced on the same port.
- Replacing OAuth with a fake bearer value leaves manifests and the imported
  definition byte-for-byte intact.
- Catalog responses, files and diagnostics contain no generated secret values;
  Store and runtime permissions are checked.
- Cleanup stops owned services, confirms ports are closed, and removes the
  fixture. A failed stop keeps it and reports its path; signals target only exact
  script paths inside the fixture after identity checks.

## Commands

```sh
python3 -m unittest discover -s tests -p test_acceptance_proof.py -v
python3 hub.py check
python3 -m unittest discover -s tests -v
```

CI runs these on a self-hosted Linux runner with Node 24 and does not deploy.
Contributors adding runners should keep them dedicated, clean the workspace
before and after each job, and run only same-repository PRs.

## Not covered

These gates need external prerequisites and are not claimed:

- Real provider OAuth or bearer, which needs an authorized, harmless integration
  and the private flow in [mcp-auth.md](mcp-auth.md).
- Model-driven calls in Cursor and OpenCode, which need an authorized login in a
  disposable client home.
- Native Windows and macOS. CI currently runs on Linux only. The gateway supports
  Linux and macOS; Windows uses the stdio route.
- Live Executor and Composio integrations.

The protocol processes share one raw HTTP client, so they prove shared
hub-credential use, not a model running a tool in a product client; the summary
records `product_agent_proof: false` and `real_provider_proof: false`. Never put
provider credentials in chat or evidence artifacts.
