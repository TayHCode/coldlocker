import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'examples/test-servers'
sys.path.insert(0, str(SCRIPTS))
try:
    spec = importlib.util.spec_from_file_location('acceptance_runner', SCRIPTS / 'acceptance_proof.py')
    proof = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proof)
finally:
    sys.path.remove(str(SCRIPTS))


class AcceptanceProofTests(unittest.TestCase):
    def test_protected_tool_selection_cannot_fall_back_to_unprotected_add(self):
        tools = [{'name': 'example-server-add'}, {'name': 'oauth-demo-add'}]
        self.assertEqual(proof.select_tool(tools, 'oauth-demo-add'), 'oauth-demo-add')
        with self.assertRaises(ValueError):
            proof.select_tool(tools[:1], 'oauth-demo-add')

    def test_environment_isolates_product_homes_and_drops_credentials(self):
        with mock.patch.dict(os.environ, {'PATH': 'fixture-bin', 'CODEX_HOME': 'real-config',
                                         'API_TOKEN': 'fixture-credential', 'HOME': 'real-home'}, clear=True):
            env = proof.isolated_environment(Path('fixture-home'))
        self.assertEqual(env['PATH'], 'fixture-bin')
        for key in ('HOME', 'USERPROFILE'):
            self.assertEqual(env[key], 'fixture-home')
        self.assertNotIn('CODEX_HOME', env)
        self.assertNotIn('API_TOKEN', env)
        self.assertEqual(env['XDG_CONFIG_HOME'], str(Path('fixture-home/config')))

    def test_internal_fixture_mode_refuses_the_real_checkout(self):
        with self.assertRaisesRegex(proof.ProofFailure, 'disposable checkout guard'):
            proof.fixture_proof(ROOT)

    def test_parent_cleans_up_after_fixture_timeout_before_removing_checkout(self):
        cleanup_roots = []
        def execute(argv, **kwargs):
            if argv[-1] == 'install':
                return subprocess.CompletedProcess(argv, 0)
            raise subprocess.TimeoutExpired(argv, 240)
        def cleanup(root, env):
            self.assertTrue(root.is_dir())
            cleanup_roots.append(root)
        with mock.patch.object(proof, 'copy_source'), mock.patch.object(proof, 'captured', side_effect=execute), \
                mock.patch.object(proof, 'cleanup_fixture', side_effect=cleanup), mock.patch.object(proof.sys, 'platform', 'linux'):
            with self.assertRaises(subprocess.TimeoutExpired):
                proof.run(argparse.Namespace(fixture=False, install_runtime=True))
        self.assertEqual(len(cleanup_roots), 1)
        self.assertFalse(cleanup_roots[0].parent.exists())

    def test_failed_parent_stop_retains_fixture_for_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid = root / '.hub-store/gateway/pid.json'
            pid.parent.mkdir(parents=True)
            pid.write_text(json.dumps({'port': 45678}), encoding='utf-8')
            with mock.patch.object(proof.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)), \
                    mock.patch.object(proof, 'stop_fixture_processes'):
                with self.assertRaisesRegex(proof.ProofFailure, 'fixture retained'):
                    proof.cleanup_fixture(root, {})
            self.assertTrue(pid.exists())

    def test_parent_stop_waits_for_lifecycle_even_before_pid_is_written(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.hub-store/gateway').mkdir(parents=True)
            with mock.patch.object(proof.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run, \
                    mock.patch.object(proof, 'stop_fixture_processes'):
                proof.cleanup_fixture(root, {})
            self.assertEqual(run.call_args.args[0], [sys.executable, 'gateway.py', 'stop'])

    def test_process_cleanup_only_selects_exact_owned_fixture_commands(self):
        root = Path(tempfile.gettempdir()) / 'fixture with spaces'
        script = root / 'gateway.py'
        bootstrap = root / '.hub-store/gateway/bootstrap.mjs'
        unrelated = root.with_name(root.name + '-other') / '.hub-store/gateway/bootstrap.mjs'
        lines = [f'101 S {sys.executable} {script} _run --port 45678',
                 f'102 S node {bootstrap}',
                 f'103 S {sys.executable} -c print({script})',
                 f'104 S node {unrelated}',
                 f'105 Z node {bootstrap}']
        with mock.patch.object(proof.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '\n'.join(lines))), \
                mock.patch('gateway.identity', return_value='fixture-identity'):
            self.assertEqual(set(proof.fixture_processes(root)), {101, 102})


if __name__ == '__main__':
    unittest.main()
