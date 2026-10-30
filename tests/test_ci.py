import importlib.util
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ci_gate', ROOT / '.github/ci.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class CIGateTests(unittest.TestCase):
    def test_hosted_workflows_are_absent(self):
        directory = ROOT / '.github/workflows'
        self.assertEqual(sorted(directory.glob('*.yml')) + sorted(directory.glob('*.yaml')), [])





    def test_runtime_rejects_wrong_node_major(self):
        with patch.object(ci.sys, 'version_info', (3, 12, 10)), \
                patch.object(ci.shutil, 'which', return_value='node'), \
                patch.object(ci.subprocess, 'check_output', return_value='v22.0.0'):
            with self.assertRaisesRegex(RuntimeError, 'Node 24'):
                ci.runtime()

    def test_windows_runtime_selects_git_bash_and_exports_it_for_later_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            git = Path(directory) / 'Git' / 'cmd' / 'git.exe'
            bash = git.parent.parent / 'bin' / 'bash.exe'
            bash.parent.mkdir(parents=True)
            bash.touch()
            exported = Path(directory) / 'path'
            versions = {'node': 'v24.0.0', 'npm': '11.0.0', str(git): 'git version 2',
                        str(bash): 'GNU bash, version 5'}
            with patch.object(ci.sys, 'platform', 'win32'), \
                    patch.object(ci.sys, 'version_info', (3, 13, 0)), \
                    patch.dict(os.environ, {'PATH': 'original-path', 'GITHUB_PATH': str(exported)}), \
                    patch.object(ci.shutil, 'which', side_effect=lambda tool: str(git) if tool == 'git' else tool), \
                    patch.object(ci.subprocess, 'check_output', side_effect=lambda args, **kw: versions[args[0]]):
                ci.runtime()
                self.assertEqual(os.environ['PATH'].split(os.pathsep)[0], str(bash.parent))
                self.assertEqual(exported.read_text(encoding='utf-8'), str(bash.parent) + '\n')

    def test_windows_runtime_refuses_missing_git_bash(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(ci.sys, 'platform', 'win32'), \
                patch.object(ci.sys, 'version_info', (3, 13, 0)), \
                patch.object(ci.shutil, 'which', return_value=str(Path(directory) / 'cmd' / 'git.exe')), \
                patch.object(ci.subprocess, 'check_output', return_value='v24.0.0'):
            with self.assertRaisesRegex(RuntimeError, 'Git Bash'):
                ci.runtime()


    def test_missing_runtime_skip_is_rejected_by_live_gate(self):
        test = SimpleNamespace(id=lambda: next(iter(ci.GATEWAY_TESTS)))
        result = SimpleNamespace(skipped=[(test, 'runtime absent')])
        self.assertEqual(ci.skipped_unexpectedly(result), [])
        self.assertEqual(len(ci.skipped_unexpectedly(result, gateway=True)), 1)

    def test_windows_acl_and_missing_node_cannot_silently_skip(self):
        acl = SimpleNamespace(id=lambda: ci.WINDOWS_ACL)
        node = SimpleNamespace(id=lambda: 'test_ui_scan.ScanUITests.test_static_xss_safe_and_node_smoke')
        result = SimpleNamespace(skipped=[(acl, 'no ACL'), (node, 'no Node')])
        self.assertEqual(len(ci.skipped_unexpectedly(result, platform='win32')), 2)
        self.assertEqual(ci.skipped_unexpectedly(result, platform='linux'), [(node.id(), 'no Node')])

    def test_windows_platform_skips_do_not_hide_linux_regressions(self):
        test = SimpleNamespace(id=lambda: next(iter(ci.WINDOWS_SKIPS)))
        result = SimpleNamespace(skipped=[(test, 'POSIX only')])
        self.assertEqual(ci.skipped_unexpectedly(result, platform='win32'), [])
        self.assertEqual(len(ci.skipped_unexpectedly(result, platform='linux')), 1)

    def test_empty_or_renamed_live_suite_fails_closed(self):
        with patch.object(ci.sys, 'version_info', (3, 12, 10)), patch.object(ci.shutil, 'which', return_value='node'), \
                patch.object(ci.unittest.defaultTestLoader, 'discover', return_value=unittest.TestSuite()):
            with self.assertRaisesRegex(RuntimeError, 'not discovered'):
                ci.run_tests(gateway=True)

    def test_setup_only_emits_paths_inside_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            envfile, pathfile = root / 'env', root / 'path'
            with patch.object(ci, 'ROOT', root), patch.dict(os.environ, {
                    'GITHUB_ENV': str(envfile), 'GITHUB_PATH': str(pathfile),
                    'RUNNER_TEMP': str(root / 'runner-temp')}):
                ci.setup()
            values = dict(line.split('=', 1) for line in envfile.read_text(encoding='utf-8').splitlines())
            for key in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'XDG_CONFIG_HOME', 'TMPDIR', 'TEMP', 'TMP'):
                self.assertTrue(Path(values[key]).is_relative_to(root))
            self.assertEqual(values['CODEX_HOME'], '')
            self.assertEqual(values['CLAUDE_CONFIG_DIR'], '')
            self.assertEqual(Path(pathfile.read_text(encoding='utf-8').strip()), root / '.scratch/ci/bin')

    def test_windows_setup_uses_short_runner_temp_and_keeps_home_in_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout, temporary = root / 'long' / 'checkout', root / 'runner-temp'
            envfile, pathfile = root / 'env', root / 'path'
            with patch.object(ci, 'ROOT', checkout), patch.object(ci.sys, 'platform', 'win32'), \
                    patch.dict(os.environ, {'GITHUB_ENV': str(envfile), 'GITHUB_PATH': str(pathfile),
                                           'RUNNER_TEMP': str(temporary)}):
                ci.setup()
                direct, _ = ci.environment(checkout)
            values = dict(line.split('=', 1) for line in envfile.read_text(encoding='utf-8').splitlines())
            for key in ('TMPDIR', 'TEMP', 'TMP'):
                self.assertEqual(Path(values[key]), temporary / 'agentlocker-ci')
                self.assertEqual(direct[key], values[key])
                self.assertTrue(Path(values[key]).is_dir())
            self.assertEqual(Path(values['HOME']), checkout / '.scratch/ci/home')

    def test_windows_environment_uses_the_existing_job_temp_outside_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(ci.sys, 'platform', 'win32'), \
                    patch.dict(os.environ, {'RUNNER_TEMP': '', 'TEMP': str(root / 'job-temp')}):
                values, _ = ci.environment(root / 'checkout')
            self.assertEqual(Path(values['TEMP']), root / 'job-temp/agentlocker-ci')
            self.assertTrue(Path(values['TEMP']).is_dir())

    def test_skip_allowlist_names_exist_in_discovered_suite(self):
        suite = unittest.TestLoader().discover(str(ROOT / 'tests'))
        names = {test.id() for test in ci.cases(suite)}
        self.assertEqual((ci.GATEWAY_TESTS | ci.WINDOWS_SKIPS | {ci.WINDOWS_ACL}) - names, set())

    def test_routine_native_skips_are_allowed_only_on_windows(self):
        names = {name for name in ci.WINDOWS_SKIPS if name.startswith('test_routine_enable.')}
        self.assertTrue(names)
        result = SimpleNamespace(skipped=[(SimpleNamespace(id=lambda name=name: name), 'POSIX fixture') for name in names])
        self.assertEqual(ci.skipped_unexpectedly(result, platform='win32'), [])
        self.assertEqual(len(ci.skipped_unexpectedly(result, platform='linux')), len(names))
