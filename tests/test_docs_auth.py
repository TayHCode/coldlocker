from pathlib import Path
import unittest

from mcp_endpoint import dispatch


ROOT = Path(__file__).resolve().parents[1]
STALE = (
    "future gateway",
    "future adapter",
    "future deployment",
    "design-stage",
    "historical",
    "not implemented",
    "not built",
)


class AuthDocumentTests(unittest.TestCase):
    def setUp(self):
        self.auth = (ROOT / "docs/mcp-auth.md").read_text(encoding="utf-8")

    def test_every_endpoint_tool_is_documented(self):
        tools = dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        self.assertIn("hub_get_item", [tool["name"] for tool in tools])
        missing = [tool["name"] for tool in tools if f"`{tool['name']}`" not in self.auth]
        self.assertEqual(missing, [])

    def test_auth_document_has_no_stale_unbuilt_claims(self):
        lowered = self.auth.lower()
        self.assertEqual([phrase for phrase in STALE if phrase in lowered], [])

    def test_auth_document_links_the_implemented_gateway_and_setup(self):
        self.assertIn("gateway.md", self.auth)
        self.assertIn("ui.md", self.auth)

    def test_cli_guide_says_imported_skills_link(self):
        cli = (ROOT / "docs/cli.md").read_text(encoding="utf-8")
        self.assertNotIn("Only the shipped manifest's skills are linked", cli)
        self.assertIn("imported", cli.split("## Link skills")[1].split("## Check the hub")[0])


if __name__ == "__main__":
    unittest.main()
