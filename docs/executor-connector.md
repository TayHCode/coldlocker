# Executor connector

agentlocker connects to an existing Executor streamable HTTP MCP endpoint. Executor
keeps its integrations, credentials, OAuth, accounts and tool policies. Selecting this
backend migrates nothing. MCPHub remains the default. Executor is
[MIT licensed](https://github.com/UsefulSoftwareCo/executor).

The verified version is npm **executor@1.6.10** (Node 20+), matching its package
README and `executor --help`. The [website](https://executor.sh/docs/run/cli)
describes a v2 beta interface that is **UNCONFIRMED** here.

The connector speaks MCP initialize, initialized, tools/list (with pagination) and
JSON or SSE responses. It bypasses proxies for loopback, refuses redirects, DNS
hostnames, non-loopback addresses, userinfo, queries and fragments, and requires an
explicit port. Numeric IPv4 and IPv6 loopback are accepted.

## Select the backend

Copy `config.example.json` to ignored `config.json`:

```json
{
  "store_path": ".hub-store",
  "gateway": {
    "backend": "executor",
    "executor": {
      "url": "http://127.0.0.1:4788/mcp",
      "headers": {
        "Authorization": "secret://executor-endpoint"
      }
    }
  }
}
```

- `python3 store.py set executor-endpoint` stores the complete endpoint header,
  including its scheme, through a private prompt. This credential reaches Executor
  only. Never copy Executor's integration auth into Store.
- All configured header values must be Store references. For an endpoint without
  auth, use `"headers": {}`.
- Get the endpoint credential from Executor's own local Connect interface. The
  connector never searches credential directories.
- Start Executor yourself, for example `executor web --foreground --hostname 127.0.0.1 --port 4788`.
  Avoid `executor install` for disposable runtimes; it installs a service.
- Stop any agentlocker-managed MCPHub runtime before switching. Switching config
  does not stop a running process.

```sh
python3 gateway.py status
python3 gateway.py url
```

`status` initializes an MCP session and lists tool names. Exit 0 means reachable with
successful discovery; exit 1 means unreachable or invalid configuration. Executor
exposes its own orchestration tools (`skills` and `execute`) rather than one entry per
integration. Discovery does not prove an integration is healthy or authorized.

With Executor selected, `install`, `prepare`, `start`, `stop`, `sync` and `refresh` are
no-ops that print a user-managed message. They never start, stop, modify or import Executor.

## Client snippets

Setup wizard step 6 checks the same endpoint. Snippets contain only the URL when no
headers are configured. With headers, Claude and Cursor snippets use environment
placeholders and Codex uses `env_http_headers`. Populate those variables privately
before launching the client. This connector never fills client environments or edits
client configs, and resolved values never appear in snippets.

An in-process client can use `executor_connector.Session`, which resolves only the
declared endpoint headers from the selected locker Store.

## Register a source (opt-in)

Executor 1.6.10 exposes `executor.mcp.addServer` and connection creation through its
`execute` API. agentlocker can register one credential-free manifest source:

```sh
python3 gateway.py register-executor --server example-server
python3 gateway.py register-executor --server example-server --apply
```

The first command is an offline preview. `--apply` creates a `locker-` prefixed
integration and an `agentlocker` user connection with template `none`. It accepts only
the exact empty approval forms for those two operations and fails closed on any other
interaction. Repeating it reuses matching sources and connections. Conflicting sources
are kept. A partial run can leave a source without a connection; retrying completes it.
Policies are not changed, and registration never calls source tools.

Env and header mappings are refused, including Store references. Executor has its own
credential providers, and no verified binding resolves Store references there. Configure
those integrations' auth in Executor directly. Stdio root arguments expand only for the
selected source.

## Fallback copy (optional)

```sh
python3 hub.py import-executor SOURCE_SLUG
python3 hub.py import-executor SOURCE_SLUG --apply
```

This reads only the named MCP integration through the live endpoint; it does not scan
Executor files or databases. A preview writes nothing. `--apply` records a nonsecret
manifest copy with `executor-copy` provenance and one owner, and preserves existing
entries. It refuses credentialed auth templates, literal env or header values,
cwd and env-var requirements, URL queries and unsafe fragments. Existing `secret://`
mappings are kept as references only and never fetched. Copies do not include
connections, policies or OAuth, and live access does not need them. Non-MCP
integration kinds cannot be imported.

## Verification

Unit tests use a fake loopback MCP endpoint and need no external network. The real
proof installs into `.scratch/` with isolated HOME and XDG directories, starts the
foreground runtime on a free loopback port, registers the shipped no-auth stdio server,
calls `add(2, 3)` through the selected backend and checks for 5. It then tears down the
process group and checks for leftover listeners and processes. Diagnostics are kept only
after endpoint-token redaction.

```sh
mkdir -p .scratch
executor_install_home="$(mktemp -d "$PWD/.scratch/executor-install.XXXXXX")"
mkdir -p "$executor_install_home/home" "$executor_install_home/config" \
  "$executor_install_home/data" "$executor_install_home/cache" "$executor_install_home/state"
env -i PATH="$PATH" HOME="$executor_install_home/home" \
  XDG_CONFIG_HOME="$executor_install_home/config" XDG_DATA_HOME="$executor_install_home/data" \
  XDG_CACHE_HOME="$executor_install_home/cache" XDG_STATE_HOME="$executor_install_home/state" \
  npm_config_userconfig="$executor_install_home/npmrc" \
  npm_config_globalconfig="$executor_install_home/global-npmrc" \
  npm_config_cache="$executor_install_home/npm-cache" \
  npm install --prefix "$PWD/.scratch/executor-runtime" executor@1.6.10
python3 examples/test-servers/executor_proof.py <IGNORED_EVIDENCE_DIRECTORY>
python3 -m unittest discover -s tests -v
python3 hub.py check
```

The proof uses a fresh ignored home on every run and never installs a service. No
account, credential or login is needed.

**UNCONFIRMED / not live-verified:** vendor OAuth and API-key integrations, hosted
endpoints, the v2 beta, and client environment-header interpolation.
