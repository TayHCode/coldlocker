# Export verification features

| Feature | User entrypoint | Proof |
| --- | --- | --- |
| Catalog health | `python3 hub.py doctor` | Schema, hashes, consistency and catalog privacy pass |
| Skill discovery and loading | MCP `tools/list`, `hub_list_skills`, `hub_get_item` | `test_endpoint.py` exercises JSON-RPC and rejects tampered content |
| Offline experiments | `python3 -B tools/verify_export.py` | Search regression cases and fresh-process checkpoint recovery pass |

Search is deliberately absent from the production tool list. The experimental ranker remains in `tools/catalog_search.py` for reproducibility.
