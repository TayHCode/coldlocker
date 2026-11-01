import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import codex_findings

QUOTED = "'" + "@AGENTS" + ".md" + "'"
PR3_COMMENT = {
    "id": 4432314750,
    "user": {"login": "chatgpt-codex-connector[bot]"},
    "path": "tests/test_model_policy.py",
    "original_commit_id": "b697063ecb78ed8da970460b0878b3a8aafb4ea7",
    "html_url": "https://github.com/TayHCode/coldlocker/pull/3#discussion_r4432314750",
    "created_at": "2026-10-31T15:52:16Z",
    "body": "**<sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub>  "
            "Avoid triggering the email scanner in the policy assertion**\n\n"
            f"The single-quoted `{QUOTED}` literal is interpreted by `checks/scan.py` as an email address "
            "because its email pattern accepts the opening apostrophe as a local part. Running `python3 hub.py check` "
            "reports `tests/test_model_policy.py:12: email-address`. Use a double-quoted literal here.\n\n"
            "Useful? React with 👍 / 👎.\n\n<!-- codex-code-review-finding:v1 -->",
}
BAD_LINE = f"        self.assertIn({QUOTED}, text)\n"
FIXED_LINE = '        self.assertIn("@AGENTS.md", text)\n'


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


class CodexFindingsTests(unittest.TestCase):
    def test_parses_a_real_codex_finding(self):
        finding = codex_findings.parse_finding(PR3_COMMENT)
        self.assertEqual((finding["severity"], finding["id"], finding["path"]),
                         ("P2", 4432314750, "tests/test_model_policy.py"))
        self.assertEqual(finding["title"], "Avoid triggering the email scanner in the policy assertion")
        self.assertTrue(finding["detail"].startswith(f"The single-quoted `{QUOTED}`"))
        self.assertNotIn("Useful?", finding["detail"])
        self.assertNotIn("codex-code-review-finding", finding["detail"])

    def test_ignores_comments_that_are_not_codex_findings(self):
        human = {**PR3_COMMENT, "user": {"login": "someone"}}
        unmarked = {**PR3_COMMENT, "body": PR3_COMMENT["body"].replace("<!-- codex-code-review-finding:v1 -->", "")}
        self.assertIsNone(codex_findings.parse_finding(human))
        self.assertIsNone(codex_findings.parse_finding(unmarked))

    def make_repo(self, folder, fixed_line):
        repo = Path(folder) / "repo"
        (repo / "checks").mkdir(parents=True)
        (repo / "tests").mkdir()
        (repo / "checks/scan.py").write_text((ROOT / "checks/scan.py").read_text(encoding="utf-8"), encoding="utf-8")
        (repo / "tests/test_model_policy.py").write_text(BAD_LINE, encoding="utf-8")
        git(repo.parent, "init", "-q", str(repo))
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "bad")
        bad = git(repo, "rev-parse", "HEAD")
        (repo / "tests/test_model_policy.py").write_text(fixed_line, encoding="utf-8")
        git(repo, "commit", "-q", "-am", "fixed")
        return repo, bad

    def case(self):
        return dict(repo="TayHCode/coldlocker", pr=3, comment_id=4432314750, fixed="HEAD",
                    fix_summary="double-quote the literal", covered_by="scan_file replay",
                    command=["python3", "tests/replays/scan_file.py", "tests/test_model_policy.py"],
                    overlay=["tests/replays/scan_file.py"])

    def test_builds_an_ingest_event_with_failing_before_and_passing_after_proof(self):
        with tempfile.TemporaryDirectory() as folder:
            repo, bad = self.make_repo(folder, FIXED_LINE)
            comment = {**PR3_COMMENT, "original_commit_id": bad}
            event = codex_findings.build(self.case(), repo, fetch=lambda *_: comment)
        lesson = json.loads(event["error"])
        self.assertEqual((event["kind"], event["agent"], event["repo"]), ("review", "codex-review", "TayHCode/coldlocker"))
        self.assertEqual(event["source_ref"], f"codex-finding:TayHCode/coldlocker:3:4432314750:{bad[:12]}:{lesson['fixed_head'][:12]}")
        self.assertEqual((lesson["severity"], lesson["bad_head"], lesson["path"]), ("P2", bad, "tests/test_model_policy.py"))
        self.assertEqual(lesson["replay"]["bad"]["exit"], 1)
        self.assertIn("tests/test_model_policy.py:1: email-address", lesson["replay"]["bad"]["output"])
        self.assertEqual(lesson["replay"]["fixed"]["exit"], 0)

    def test_rejects_a_fix_that_the_replay_cannot_separate_from_the_bug(self):
        with tempfile.TemporaryDirectory() as folder:
            repo, bad = self.make_repo(folder, BAD_LINE + "# unrelated\n")
            comment = {**PR3_COMMENT, "original_commit_id": bad}
            with self.assertRaises(SystemExit) as raised:
                codex_findings.build(self.case(), repo, fetch=lambda *_: comment)
        self.assertIn("does not separate bad from fixed", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
