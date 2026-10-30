# Provenance

Source: pstack 0.15.27, by Lauren Tan, from the installed cursor-plugins package.
Upstream repository: https://github.com/cursor/plugins, package `pstack`.
Coldlocker maintainer: `agentlocker`. License: MIT; the original notice is in `LICENSE`.
One version of this package is retained, including all supporting files.

## Original source SHA-256

- `SKILL.md`: `8eb22afe29058a689ab542dbd729b953cb1fe0d21f0214e54270477a1751b032`

## Coldlocker adaptations

Replaced the external harness-adapter link with Coldlocker policy and authorized-platform verification; explicitly excluded the retired independent Windows gate. Added a serial fallback when delegation is unavailable or unauthorized and replaced the fixed retry count with prerequisite-based blocking. Made project skill locations follow the target harness instead of requiring Cursor.
