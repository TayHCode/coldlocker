# Public site and self-hosted downloads

The site uses the existing v4 setup design and self-hosted fonts. Its screenshots
are explicitly labelled examples. It never reads or exposes the owner's catalog.
The working setup wizard remains in `ui/` and runs locally with `hub.py serve`.

Build into a new directory:

```sh
python3 tools/build_site.py --out remote/public --security-email '<verified-public-alias>'
```

The build makes ZIP and tar.gz source packages, per-file SHA-256 metadata, and
download checksums. It includes no Git history, ignored files, imported content,
maintainer audit records, CI configuration, or maintainer email allowlist.
`hub.py doctor` supports these source snapshots; `hub.py check` remains the
maintainer gate in the development repository.

Before publication, configure and verify the private security reporting route.
The build option sets only its public alias in the generated security page; the
private forwarding destination is never a build input. `SECURITY.md` links to
that page. Rehearse the downloaded package in a fresh
fixture home. Build outputs are generated and must not be committed. The builder
refuses to overwrite previous output. Keep old release evidence and artifacts.

On Cloudflare Workers, add `"assets": {"directory": "./public"}` to the private
Wrangler config. Static requests use Assets; unmatched MCP and OAuth requests
continue to the existing Worker. Do not configure SPA fallback: authorization
and missing API routes must retain their existing behavior. Preserve the
deployment's private bindings, existing catalog, and OAuth state.

Managed customer hosting and payment processing are not implemented by this site.
