# Hosted catalog

The hosted catalog is built: a read-only, OAuth-protected MCP endpoint at
`https://agentlocker.dev/mcp`, served by a Cloudflare Worker. Setup, publishing and
client connection are in [remote.md](remote.md); Composio access is in
[composio.md](composio.md).

## What is hosted

- The manifest-listed catalog: skills (with supporting files), rules, routines
  and nonsecret MCP definitions. Your private local imports are published only with
  `publish-remote --include-local` after a listed confirmation.
- Manifest paths stay repository-relative POSIX paths and provenance ids stay
  opaque (`src-` plus 12 hex characters), so no source machine path or hostname is
  encoded. The server resolves paths against a versioned snapshot and exposes no
  arbitrary filesystem access.

## What is not hosted

- No secret values. Definitions keep `secret://<name>` references, and catalog
  tools, logs and CLI output never return resolved values.
- No execution of upstream MCP servers, no hosted skill installation and no
  scheduler. A hosted caller cannot reach a local tool; the local
  [gateway](gateway.md) is a separate integration.
- No multi-tenant access control. A single owner authorizes each client with
  OAuth and a `catalog:read` scope.

## Open design questions for multi-user hosting

Tenant boundaries, caller identity, per-server grants, consent and revocation,
token encryption and rotation, OAuth callback ownership, audit retention and
deletion policy are undecided. Namespace references by authenticated tenant
internally and never trust a caller-supplied tenant label.
