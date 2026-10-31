import json
from pathlib import Path
import shutil
import tempfile
import unittest

from mcp_endpoint import SEARCH_TOOL, dispatch, search_skills, terms

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests/fixtures/search_eval.json").read_text(encoding="utf-8"))


def call(root, name, arguments):
    return dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": name, "arguments": arguments}}, root)


def search(root, query):
    response = call(root, SEARCH_TOOL, {"query": query})
    return json.loads(response["result"]["content"][0]["text"])


class RankerTests(unittest.TestCase):
    def test_terms_drop_stopwords_and_stem(self):
        self.assertEqual(terms("The Robots are summarising releases"), {"robot", "summaris", "releas"})

    def test_name_match_outranks_description_match(self):
        catalog = [{"name": "alpha", "description": "release notes"}, {"name": "release-notes", "description": "x"}]
        found = search_skills_over(catalog, "release notes")
        self.assertEqual([e["name"] for e in found["results"]], ["release-notes", "alpha"])

    def test_below_threshold_and_stopword_queries_are_empty(self):
        catalog = [{"name": "alpha", "description": "release notes"}]
        self.assertEqual(search_skills_over(catalog, "release")["results"], [])
        self.assertEqual(search_skills_over(catalog, "what is the")["results"], [])

    def test_limit_and_remaining(self):
        catalog = [{"name": f"s{i}", "description": "alpha beta"} for i in range(5)]
        found = search_skills_over(catalog, "alpha beta")
        self.assertEqual(len(found["results"]), 3)
        self.assertEqual((found["searched"], found["remaining"]), (5, 2))


def search_skills_over(catalog, query):
    import mcp_endpoint
    original = mcp_endpoint.inventory
    mcp_endpoint.inventory = lambda root, kind: catalog
    try:
        return search_skills(None, query)
    finally:
        mcp_endpoint.inventory = original


class ShippedCatalogTests(unittest.TestCase):
    def test_dev_cases_hit_expected_skill_and_absence_is_empty(self):
        for case in FIXTURE["dev"]["cases"]:
            names = [e["name"] for e in search(ROOT, case["query"])["results"]]
            if case["kind"] == "present":
                self.assertTrue(set(names) & set(case["expected_any"]), (case["query"], names))
            else:
                self.assertEqual(names, [], case["query"])

    def test_search_result_loads_the_full_verified_package(self):
        found = search(ROOT, "make the robot remember its mistakes")["results"][0]
        item = call(ROOT, "hub_get_item", {"kind": "skill", "name": found["name"]})
        package = json.loads(item["result"]["content"][0]["text"])
        self.assertTrue(package["files"])
        self.assertFalse(package["truncated"])
        self.assertEqual(package["name"], found["name"])
        self.assertFalse(package["omitted"])

    def test_list_fallback_still_returns_every_skill(self):
        listed = json.loads(call(ROOT, "hub_list_skills", {})["result"]["content"][0]["text"])
        self.assertGreater(len(listed), 3)

    def test_bad_arguments_are_rejected(self):
        for arguments in ({}, {"query": ""}, {"query": 1}, {"query": "x" * 501}, {"query": "a", "x": 1}, ["a"]):
            response = call(ROOT, SEARCH_TOOL, arguments)
            self.assertEqual(response["error"]["code"], -32602, arguments)

    def test_fixture_is_split_and_fingerprinted(self):
        history = FIXTURE["history"]["cases"]
        self.assertGreaterEqual(len(history), 30)
        self.assertEqual(len(FIXTURE["dev"]["cases"]), 9)
        fingerprints = [case["fingerprint"] for case in history]
        self.assertEqual(len(set(fingerprints)), len(fingerprints))
        dev_queries = {case["query"] for case in FIXTURE["dev"]["cases"]}
        for case in history:
            self.assertEqual(len(case["fingerprint"]), 64)
            self.assertNotIn(case.get("excerpt"), dev_queries)


class IntegrityTests(unittest.TestCase):
    def test_tampered_skill_found_by_search_still_fails_get_item(self):
        scratch = ROOT / ".scratch/test-root"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temp:
            root = Path(temp)
            for name in ("manifest.json", "mcp_endpoint.py", "calllog.py", "store.py", "portable.py"):
                shutil.copyfile(ROOT / name, root / name)
            for name in ("skills", "rules", "checks"):
                shutil.copytree(ROOT / name, root / name)
            target = search(root, "make the robot remember its mistakes")["results"][0]
            skill = root / target["path"] / "SKILL.md"
            skill.write_text(skill.read_text(encoding="utf-8") + "\ntampered\n", encoding="utf-8")
            self.assertEqual(search(root, "make the robot remember its mistakes")["results"][0]["name"], target["name"])
            response = call(root, "hub_get_item", {"kind": "skill", "name": target["name"]})
            self.assertIn("error", response)


if __name__ == "__main__":
    unittest.main()
