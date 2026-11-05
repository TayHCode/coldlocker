from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from consistency_check import scan, violations
from hooks import install
from test_hub import HubCase, REPO


class ConsistencyTests(unittest.TestCase):
    def test_models_and_efforts_are_routed(self):
        for text in ('Use Opus.', 'Use **Opus** for review.', 'Use _Opus_ for review.', 'Use Claude Sonnet for review.', 'Set model to Claude 3.5 Sonnet.',
                     'Set the reviewer to Opus.', 'Use the latest Opus.', 'Use\nOpus.', 'model: gpt-6.1-sol', '--model claude-sonnet-5-5',
                     'Haiku', 'Astra', 'Terra', 'Luna', 'Sol', 'Tell Sol: review the diff', 'verifier: "opus"', 'sonnet-5.5', 'effort: high',
                     'effort:\n  high',
                     'reasoning_effort="max"', 'model_reasoning_effort = "high"', 'Use medium reasoning effort.', '--effort low', 'Use high effort.',
                     'model:\n  haiku', 'reviewer:\n  terra', 'model: o3', 'model: grok-3',
                     'Use Gemini 2.5 Pro as the reviewer', 'model: Llama 3.3',
                     'The reviewer uses high effort.'):
            with self.subTest(text=text):
                self.assertEqual(list(violations(text))[0][1], 'model-routing')

    def test_data_names_and_program_states_are_not_model_or_completion_instructions(self):
        self.assertEqual(list(violations('{"font": "Luna"}', data=True)), [])
        self.assertTrue(list(violations('{"model": "Luna"}', data=True)))
        self.assertTrue(list(violations('{"prompt": "Use Luna"}', data=True)))
        self.assertTrue(list(violations('{"choice": "gpt-6-luna"}', data=True)))
        for model in ('deepseek-r1', 'gemini-pro', 'devstral:latest'):
            self.assertTrue(list(violations('{"choice": "' + model + '"}', data=True)))
        self.assertTrue(list(violations('The OpenAI API response contains "model": "gpt-4o".')))
        self.assertEqual(list(violations('The API response contains "model": "fixture-model".')), [])
        self.assertEqual(list(violations('status: "failed"')), [])
        for text in ('Status: pending', 'Status: WAITING', 'Status: PARTIAL'):
            self.assertEqual(list(violations(text)), [])
        self.assertEqual(list(violations('Terminal<"COMPLETE">; stop, or `COMPLETE`')), [])
        self.assertEqual(list(violations('STATUS = re.compile(pattern)\nFIELDS = ("Goal:", "Remaining:")')), [])
        self.assertEqual(list(violations('{"provider": "fonts",\n"designer": [\n"Sol Matas"]}', data=True)), [])
        self.assertEqual(list(violations('claude-plugins-official and Claude-interaction')), [])
        self.assertEqual(list(violations('{"runner": "grok-bot"}', data=True)), [])

    def test_ordinary_family_words_are_not_model_choices(self):
        for text in ('const title = "Write a sonnet";', 'Write a haiku about spring.',
                     'Use a sonnet as the title.', 'Use a haiku for the review.', 'Use haiku as the title.', 'Run the model and include a sonnet in the result.', 'Use the agent to write a haiku.', 'The reviewer uses model routing. Write a haiku about spring.', 'Choose the model through routing.\nWrite a haiku about spring.', 'This task requires high effort from the team.', '{"prompt": "Write a sonnet"}'):
            with self.subTest(text=text):
                self.assertEqual(list(violations(text)), [])
        self.assertEqual(list(violations('Never use Opus; select the reviewer through model routing.'))[0][1],
                         'model-routing')

    def test_reference_exception_is_model_only(self):
        self.assertEqual(list(violations('Use Opus with effort: high', model_reference=True)), [])
        self.assertEqual(list(violations('Final status: PARTIAL', model_reference=True))[0][1], 'terminal-status')

    def test_diagnostic_line_tracks_wrapped_sentences(self):
        found = list(violations("Safe first line.\nAnother safe line.\nReport DONE before running tests."))
        self.assertEqual(found[0][:2], (3, "verification-order"))

    def test_known_conflicts(self):
        for text, code in (
            ('Final status: PARTIAL', 'terminal-status'),
            ('Final status: **PARTIAL**.', 'terminal-status'),
            ('Final status: _PARTIAL_.', 'terminal-status'),
            ('The final task status is INCOMPLETE', 'terminal-status'),
            ('Finish with status: INCOMPLETE', 'terminal-status'),
            ('Final status: waiting', 'terminal-status'),
            ('Final status: DONE/PARTIAL/BLOCKED', 'terminal-status'),
            ('Finish with Status: DONE or Status: PARTIAL', 'terminal-status'),
            ('Final status: DONE, PARTIAL, BLOCKED', 'terminal-status'),
            ('Final state: SUCCESS', 'terminal-status'),
            ('End the turn with INCOMPLETE', 'terminal-status'),
            ('End the turn with incomplete', 'terminal-status'),
            ('Run cold gate authorize before merge.', 'retired-gate'),
            ('Run cold gate authorize if the check is not green.', 'retired-gate'),
            ('Report DONE before running tests if no checks ran.', 'verification-order'),
            ('Goal: finish the change\nRemaining: none\nStatus: WAITING', 'terminal-status'),
            ('Never skip verification; run cold gate authorize before merge.', 'retired-gate'),
            ('Do not skip tests; report DONE before running tests.', 'verification-order'),
            ('Do not skip tests but report DONE before running tests.', 'verification-order'),
            ('Local checks supplement this Windows gate.', 'retired-gate'),
            ('Report DONE before running tests.', 'verification-order'),
            ('Declare complete then verify.', 'verification-order'),
            ('Report DONE before\nrunning tests.', 'verification-order'),
            ('Never skip tests. Run cold gate scan.', 'retired-gate'),
            ('Keep going; skip verification.', 'verification-order'),
            ('Do not skip verification and skip verification', 'verification-order'),
            ('Do not report DONE before tests and report DONE before tests', 'verification-order'),
        ):
            with self.subTest(text=text):
                self.assertIn(code, [item[1] for item in violations(text)])

    def test_verdicts_negations_and_workflow_names_are_not_conflicts(self):
        text = '''Run the sol-loop skill through model routing.
Use claude-code as the routine runner.
Status: DONE
Status: BLOCKED
Finish with Status: DONE or Status: BLOCKED.
Final status: DONE, then explain the result.
Final status: DONE. HTTP status: failed.
Final status: DONE
Status: pending
Never report Status: PARTIAL.
The retired cold gate run command must not execute.
Do not skip verification.
Do not **skip verification**.
Do not run cold gate authorize.
This package does not activate the retired independent Windows gate.
Explicitly excluded the retired independent Windows gate.
Skip when: Never skip the verification itself.
The independent Windows gate is retired.
Final status: PARTIAL is never allowed.
Check verdicts are PASS, FAIL or INCOMPLETE.
Verify before reporting done. Keep going until the goal is met.
'''
        self.assertEqual(list(violations(text)), [])

    def test_scans_nested_support_files_and_only_one_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('skills/a/references/note.txt', 'rules/other.md',
                         'hooks/a.py', 'routines/a/prompt.md', 'rules/model-routing.md',
                         '.hub-store/catalog/skills/local/SKILL.md'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('Use haiku', encoding='utf-8')
            errors = scan(root)
            self.assertEqual(len(errors), 5)
            self.assertTrue(any('skills/a/references/note.txt:1:' in error for error in errors))
            self.assertFalse(any('Use haiku' in error for error in errors))

    def test_unreadable_instruction_and_symlink_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'skills').mkdir()
            (root / 'skills/bad.md').write_bytes(b'\xff')
            (root / 'skills/link').symlink_to(root / 'skills/bad.md')
            self.assertEqual(len(scan(root)), 2)

    def test_binary_support_assets_do_not_block_valid_instructions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / 'skills/example'
            skill.mkdir(parents=True)
            (skill / 'SKILL.md').write_text('Use model routing.', encoding='utf-8')
            (skill / 'image.png').write_bytes(b'\x89PNG\r\n\x1a\nhaiku')
            (skill / 'font.woff').write_bytes(b'wOFF\0haiku')
            self.assertEqual(scan(root), [])
            (skill / 'SKILL.md').write_text('Use haiku.', encoding='utf-8')
            self.assertEqual(len(scan(root)), 1)

    def test_custom_hook_is_checked_before_settings_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hook = root / "custom.py"
            hook.write_text('model = "haiku"', encoding="utf-8")
            settings = root / "settings.json"
            settings.write_text('{}', encoding="utf-8")
            for write in (False, True):
                with self.assertRaisesRegex(ValueError, "instruction consistency"):
                    install.install([(settings, False)], sys.executable, write, hook=hook)
                self.assertEqual(settings.read_text(), '{}')
                self.assertEqual(sorted(p.name for p in root.iterdir()), ['custom.py', 'settings.json'])
            hook.write_text('print("ready")', encoding="utf-8")
            install.install([(settings, False)], sys.executable, True, hook=hook)
            self.assertIn('custom.py', settings.read_text())

    def test_hook_install_refuses_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'hooks').mkdir()
            hook = root / 'hooks/keep_going.py'
            hook.write_text('model = "haiku"', encoding='utf-8')
            settings = root / 'settings.json'
            settings.write_text('{}', encoding='utf-8')
            with patch.object(install, 'HOOK', hook):
                with self.assertRaisesRegex(ValueError, 'model-routing'):
                    install.install([(settings, False)], sys.executable, True)
            self.assertEqual(settings.read_text(), '{}')
            self.assertEqual(list(root.glob('*.bak*')), [])

    def test_cli_fails_for_fixture_and_passes_after_fix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'rules').mkdir()
            rule = root / 'rules/flow.md'
            rule.write_text('Final status: PARTIAL', encoding='utf-8')
            def run():
                return subprocess.run([sys.executable, str(REPO / 'consistency_check.py'), str(root)],
                                      capture_output=True, text=True)
            self.assertEqual(run().returncode, 1)
            rule.write_text('Final status: BLOCKED', encoding='utf-8')
            result = run()
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('PASS:', result.stdout)


class ConsistencyIntegrationTests(HubCase):
    def bad_rule(self):
        path = self.root / 'rules/conflict.md'
        path.parent.mkdir()
        path.write_text('Use haiku', encoding='utf-8')

    def test_hub_check_reports_inconsistency(self):
        self.bad_rule()
        self.git('init', '-q')
        self.git('add', '.')
        self.assertIn('FAIL: instruction consistency', self.cli('check', code=1))

    def test_doctor_reports_inconsistency(self):
        self.bad_rule()
        self.assertIn('FAIL: instruction consistency', self.cli('doctor', code=1))

    def test_link_accepts_imported_skill_with_binary_support(self):
        (self.source / 'preview.png').write_bytes(b'\x89PNG\r\n\x1a\n')
        (self.source / 'job.txt').write_text('Status: pending\nStatus: WAITING', encoding='utf-8')
        self.import_skill()
        target = self.base / 'project'
        target.mkdir()
        self.cli('link', '--target', target)
        self.assertTrue((target / '.agents/skills/sample').is_symlink())
        self.assertEqual((target / '.agents/skills/sample/preview.png').read_bytes(),
                         (self.source / 'preview.png').read_bytes())

    def test_link_refuses_inconsistent_catalog_without_creating_links(self):
        self.bad_rule()
        target = self.base / 'project'
        target.mkdir()
        self.cli('link', '--target', target, code=1)
        self.assertFalse((target / '.agents').exists())
        self.assertFalse((target / '.claude').exists())


if __name__ == '__main__':
    unittest.main()
