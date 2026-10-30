# Provenance

Maintainer of this catalog adaptation: `agentlocker`.

Source: the installed `~/.agents/skills/i-have-adhd` package, copied on
2026-10-29, including its `agents/` support files. The installed
`~/.agents/sources.json` records upstream https://github.com/ayghri/i-have-adhd
at commit `1ecea1993b7fce257ffbd686c070948b5e07eda6`.
This is the installed source revision record, not a claim that the installed
files are byte-identical to that upstream commit.

Installed SKILL.md SHA-256 before adaptation:
`88955c019bbb1419ff4b49dbb1f80a867467de6233d7865af2e06a7d7b76c7d3`.

The accompanying MIT license is copied unchanged from the installed
`~/.agents/licenses/i-have-adhd-LICENSE`; copyright remains with Ayoub Ghriss.

Local changes add the keep-going status reference, require evidence for time
estimates, and remove arbitrary presentation item limits. The self-contained
Gemini adapter receives the same guidance. The OpenAI adapter is unchanged.
The status reference describes `hooks/keep_going.py`; no hook is copied or
installed by this package.
