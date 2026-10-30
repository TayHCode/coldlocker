import json
from pathlib import Path
import subprocess
import sys
import unittest

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "auto_pick.py"


def ask(*questions):
    return {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
            "tool_input": {"questions": list(questions)}}


def question(text, *labels):
    return {"question": text, "header": "Choice", "multiSelect": False,
            "options": [{"label": label, "description": ""} for label in labels]}


def run_hook(payload):
    result = subprocess.run([sys.executable, "-I", str(HOOK)], input=json.dumps(payload),
                            text=True, capture_output=True, encoding="utf-8", check=True)
    return json.loads(result.stdout)


class AutoPickHookTests(unittest.TestCase):
    def test_denies_question_with_recommended_option_and_names_it(self):
        out = run_hook(ask(question("Which test runner?", "unittest (Recommended)", "pytest")))
        hook = out["hookSpecificOutput"]
        self.assertEqual(hook["hookEventName"], "PreToolUse")
        self.assertEqual(hook["permissionDecision"], "deny")
        self.assertIn("Which test runner? -> unittest (Recommended)", hook["permissionDecisionReason"])
        self.assertIn("spends money, publishes, deletes or messages people", hook["permissionDecisionReason"])

    def test_denies_only_when_every_question_has_a_recommendation(self):
        both = run_hook(ask(question("Runner?", "a (Recommended)", "b"), question("Format?", "json", "toml (recommended)")))
        self.assertIn("Format? -> toml (recommended)", both["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual(run_hook(ask(question("Runner?", "a (Recommended)", "b"), question("Format?", "json", "toml"))), {})

    def test_auto_picks_ordinary_format_choice(self):
        out = run_hook(ask(question("Output format?", "JSON (Recommended)", "Plain text")))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_allows_question_without_single_recommendation(self):
        self.assertEqual(run_hook(ask(question("Which runner?", "unittest", "pytest"))), {})
        self.assertEqual(run_hook(ask(question("Which runner?", "a (Recommended)", "b (Recommended)"))), {})

    def test_still_asks_about_money_publishing_deleting_and_messaging(self):
        for text in ("Buy the paid plan?", "Publish the release?", "Delete the old branch?", "Send the email to the team?",
                     "Should I erase the production database?", "Purge the cache bucket?", "Upgrade to the Pro subscription?",
                     "Make the repo visible to everyone?", "Tweet the announcement?", "Invite the team to the doc?",
                     "Renew the $99 annual plan?"):
            with self.subTest(text=text):
                self.assertEqual(run_hook(ask(question(text, "Yes (Recommended)", "No"))), {})

    def test_ignores_malformed_input(self):
        for payload in ({}, {"tool_input": "x"}, {"tool_input": {"questions": ["x"]}}, []):
            with self.subTest(payload=payload):
                self.assertEqual(run_hook(payload), {})
        result = subprocess.run([sys.executable, "-I", str(HOOK)], input="not json", text=True,
                                capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), {})


if __name__ == "__main__":
    unittest.main()
