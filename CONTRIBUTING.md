# Contributing

Use Python 3.11+ (Windows: 3.12.4+), Git and Node.js 24 for development.
The application itself uses the Python standard library. Work in a virtual
environment when installing test dependencies:

```sh
python3 -m venv .venv
```

Activate that environment using your platform's normal command, then install the
test tools and check the installation:

```sh
python3 -m pip install pillow==13.0.0 tzdata==2026.5
python3 hub.py doctor
python3 hub.py list
```

Run the source-export checks before submitting a change. These use Python's
standard library and Git, and require permission to create symbolic links:

```sh
python3 -B tools/verify_export.py
```

This runs doctor and the supported export tests, including the disposable CLI
and MCP dogfood replay in Git checkouts. Source archives explicitly skip that
Git-dependent replay. Use the same command on Windows with `python` in place of
`python3`.

The private maintainer checkout additionally runs `python3 hub.py check` and
full unittest discovery. Those checks require omitted `config/` policy and
fixtures, so they are not supported source-export commands. Do not create an
allowlist or weaken the identity checks to make them pass. `doctor` remains the
installation check for users with their own Git identities.

For Worker changes, use Node.js 24 and the pinned dependencies:

```sh
npm --prefix remote ci
npm --prefix remote run typecheck
npm --prefix remote test
```

Use temporary homes and fake schedulers for tests. Preserve existing user client
settings, imported content and credentials. Do not commit local stores, private
deployment settings, credentials or evidence. Keep portable routines disabled.
Explain the behavior changed and the checks run in your pull request. Report
security issues privately through [SECURITY.md](SECURITY.md).

The optional gateway pins its own dependency tree. To inspect its installed
licenses without starting a server, install with lifecycle scripts disabled:

```sh
npm --prefix gateway ci --ignore-scripts
```
