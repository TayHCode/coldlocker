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

Run these checks before submitting a change:

```sh
python3 hub.py check
python3 -m unittest discover -s tests -v
python3 examples/test-servers/local_catalog_proof.py
```

The developer checkout includes its maintainer commit-identity policy under
`config/`. Use a repository-local Git identity accepted by that policy; ask the
maintainer to review an addition when contributing with another identity.
`doctor` remains the installation check for users with their own Git identities.
Runtime downloads omit maintainer-only inputs and do not promise the complete
developer suite. Five live-gateway tests skip when the pinned optional gateway
runtime is absent; the Windows ACL test skips on other platforms.

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
