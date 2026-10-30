# Related projects

Licenses were checked through each repository's GitHub `license.spdx_id` on
2026-10-19. Recheck the exact revision and notices before copying code; agentlocker
references these projects without vendoring them, and referencing a project does
not relicense it. agentlocker itself is [MIT](../LICENSE).

| Project | License | Relationship |
| --- | --- | --- |
| [amtiYo/agents](https://github.com/amtiYo/agents) | Apache-2.0 | Live [agents sync connector](agents-sync.md); delegates config writing and skill bridges to its installed CLI. |
| [Composio](https://github.com/ComposioHQ/composio) | MIT SDK; hosted service terms | Reaches the remote locker through `connect-composio --catalog`; an optional [upstream connector](composio.md) keeps Composio toolkits usable through the gateway. |
| [MCPHub](https://github.com/samanhappy/mcphub) | Apache-2.0 | The default [gateway](gateway.md) backend. |
| [Docker MCP Gateway](https://github.com/docker/mcp-gateway) | MIT | Gateway candidate with a container runtime; compared in [mcp-gateway.md](mcp-gateway.md). |
| [Vercel skills CLI](https://github.com/vercel-labs/skills) | MIT | Skill discovery and installation reference; not a shared rules, routines and auth hub. |
| [Executor](https://github.com/UsefulSoftwareCo/executor) | MIT | [Live gateway backend](executor-connector.md) that reuses its sources, auth and policies; copying nonsecret sources is a fallback. |

Executor's [README](https://github.com/UsefulSoftwareCo/executor/blob/main/README.md)
documents an integration catalog, not a user skills library or routine contract; its
[vision](https://github.com/UsefulSoftwareCo/executor/blob/main/vision.md) proposes
shared skills and scheduled workflows, which are not shipped support. Recheck when
they ship.

Skill packages follow the [Agent Skills specification](https://agentskills.io/specification)
and entry instructions follow [AGENTS.md](https://agents.md/); neither guarantees
identical discovery or configuration behavior in every harness. For pstack-factory
reference patterns see [Factory overlap](factory-overlap.md).
