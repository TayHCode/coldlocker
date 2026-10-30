from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SignInViewTests(unittest.TestCase):
    def test_primary_action_follows_what_the_server_supports(self):
        result = subprocess.run(['node', 'tests/ui_signin_smoke.cjs'], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_credential_editors_are_forms_and_saved_copy_names_no_path(self):
        result = subprocess.run(['node', 'tests/ui_signin_form_smoke.cjs'], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_legacy_credential_row_is_a_visible_failure_counted_as_pending(self):
        result = subprocess.run(['node', 'tests/ui_signin_legacy_smoke.cjs'], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
