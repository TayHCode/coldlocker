import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('local_catalog_proof', ROOT / 'examples/test-servers/local_catalog_proof.py')
proof = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proof)


@unittest.skipIf(sys.version_info < (3, 11), 'CLI/Codex fixture requires Python 3.11+')
class LocalCatalogProofTests(unittest.TestCase):
    def fixture(self):
        fixture = proof.Fixture()
        fixture.__enter__()
        self.addCleanup(fixture.__exit__, None, None, None)
        return fixture

    def snapshot(self, root):
        return {path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob('*') if path.is_file() and not path.is_symlink()}

    def test_complete_cli_stdio_journey_has_bounded_evidence_and_no_machine_paths(self):
        fixture = self.fixture()
        summary = fixture.prove()
        self.assertEqual(summary['status'], 'passed')
        self.assertRegex(summary['source']['sha256'], r'^[0-9a-f]{64}$')
        for key in ('product_agent_proof', 'real_provider_proof', 'gateway_proof', 'remote_proof', 'scheduler_proof'):
            self.assertIs(summary[key], False)
        encoded = json.dumps(summary)
        self.assertNotIn(str(fixture.base), encoded)
        self.assertNotIn(str(ROOT), encoded)
        self.assertFalse((fixture.root / 'config.json').exists())
        self.assertFalse((fixture.root / '.git').exists())

    @unittest.skipIf(sys.platform == 'win32',
                     'Windows aliased-temp history fixture unresolved; see docs/release.md')
    def test_symlinked_tmpdir_accepts_canonical_fixture_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target, alias = base / 'real tmp', base / 'linked tmp'
            target.mkdir()
            alias.symlink_to(target, target_is_directory=True)
            with mock.patch.dict(os.environ, {'TMPDIR': str(alias), 'TMP': str(alias), 'TEMP': str(alias)}), \
                    mock.patch.object(tempfile, 'tempdir', None):
                with proof.Fixture() as fixture:
                    self.assertTrue(fixture.base.is_relative_to(alias))
                    self.assertNotEqual(fixture.root, fixture.root.resolve())
                    self.assertEqual(fixture.prove()['status'], 'passed')
                    entry = json.loads(fixture.claude.read_bytes())['mcpServers']['agentlocker']
                    self.assertNotEqual(entry['args'], [str(fixture.root / 'mcp_endpoint.py')])
                    self.assertTrue(Path(entry['args'][0]).samefile(fixture.root / 'mcp_endpoint.py'))

    def test_cli_launched_by_python_alias_accepts_same_interpreter(self):
        fixture = self.fixture()
        alias = fixture.base / ('fixture-python.exe' if os.name == 'nt' else 'fixture-python')
        alias.symlink_to(sys.executable)
        run = proof.run

        def launch(argv, *args, **kwargs):
            if argv[1:2] == ['hub.py']:
                argv = [str(alias), *argv[1:]]
            return run(argv, *args, **kwargs)

        with mock.patch.object(proof, 'run', side_effect=launch):
            self.assertEqual(fixture.prove()['status'], 'passed')
        entry = json.loads(fixture.claude.read_bytes())['mcpServers']['agentlocker']
        self.assertTrue(Path(entry['command']).samefile(sys.executable))

    def test_crlf_history_keeps_actual_original_updated_and_prompt_bytes(self):
        write_text = Path.write_text

        def crlf(path, data, *args, **kwargs):
            if path.name in ('guide.md', 'prompt.md'):
                kwargs['newline'] = '\r\n'
            return write_text(path, data, *args, **kwargs)

        with mock.patch.object(Path, 'write_text', autospec=True, side_effect=crlf):
            fixture = self.fixture()
            self.assertEqual(fixture.guide.read_bytes(), b'Fixture guide version one.\r\n')
            self.assertEqual(fixture.prove()['status'], 'passed')
        history = fixture.root / '.hub-store/history'
        self.assertEqual({path.read_bytes() for path in history.rglob('guide.md')},
                         {b'Fixture guide version one.\r\n', b'Fixture guide version two.\r\n'})
        self.assertEqual([path.read_bytes() for path in history.rglob('prompt.md')],
                         [b'Read the fixture guide without changes.\r\n'])

    def test_configured_endpoint_rejects_wrong_files_interpreters_and_extra_args(self):
        fixture = self.fixture()
        endpoint = fixture.root / 'mcp_endpoint.py'
        outside = fixture.base / 'outside.py'
        outside.write_bytes(endpoint.read_bytes())
        hardlink = fixture.base / 'outside-hardlink.py'
        os.link(endpoint, hardlink)
        escape = fixture.root / 'escape.py'
        escape.symlink_to(outside)
        arbitrary = fixture.base / 'arbitrary-python'
        arbitrary.write_bytes(b'fixture non-interpreter')
        arbitrary.chmod(0o700)
        for command, args in ((sys.executable, [str(outside)]),
                              (sys.executable, [str(hardlink)]),
                              (sys.executable, [str(escape)]),
                              (sys.executable, [str(fixture.root / 'hub.py')]),
                              (sys.executable, [str(endpoint), 'extra']),
                              (sys.executable, ['mcp_endpoint.py']),
                              (str(arbitrary), [str(endpoint)]),
                              ('python3', [str(endpoint)])):
            with self.subTest(command=command, args=args):
                with self.assertRaisesRegex(proof.ProofFailure, '^fixture-only configured endpoint$'):
                    fixture.configured_endpoint({'command': command, 'args': args})
        endpoint.unlink()
        endpoint.symlink_to(outside)
        with self.assertRaisesRegex(proof.ProofFailure, '^fixture-only configured endpoint$'):
            fixture.configured_endpoint({'command': sys.executable, 'args': [str(endpoint)]})

    def test_configured_endpoint_rejects_forged_python_path_candidate_without_running_it(self):
        fixture = self.fixture()
        candidate = fixture.base / ('python3.exe' if os.name == 'nt' else 'python3')
        candidate.write_bytes(b'fixture non-interpreter')
        candidate.chmod(0o700)
        fixture.env['PATH'] = str(fixture.base)
        run = proof.run
        executed = []

        def forge(argv, *args, **kwargs):
            if argv[0] == str(candidate):
                executed.append(argv)
                return subprocess.CompletedProcess(argv, 0, 'fixture-python-ok\n', '')
            return run(argv, *args, **kwargs)

        with mock.patch.object(proof, 'run', side_effect=forge):
            with self.assertRaisesRegex(proof.ProofFailure, '^fixture-only configured endpoint$'):
                fixture.configured_endpoint({'command': str(candidate),
                                             'args': [str(fixture.root / 'mcp_endpoint.py')]})
        self.assertEqual(executed, [])

    def test_history_rejects_newline_changes_and_content_corruption(self):
        cases = [('import-skill', 'guide.md', b'Fixture guide version one.\n',
                  'upgrade retained previous support file'),
                 ('remove', 'guide.md', b'prefix Fixture guide version two.\n suffix',
                  'removal retains upgraded support file'),
                 ('remove', 'prompt.md', b'Read the fixture guide without changes.\n',
                  'removal retains routine prompt')]
        for command, filename, corrupted, stage in cases:
            with self.subTest(stage=stage):
                fixture = self.fixture()
                fixture.guide.write_bytes(b'Fixture guide version one.\r\n')
                (fixture.routine.parent / 'prompt.md').write_bytes(b'Read the fixture guide without changes.\r\n')
                cli = fixture.cli

                def corrupt(*args, **kwargs):
                    result = cli(*args, **kwargs)
                    if args[0] == command:
                        for path in (fixture.root / '.hub-store/history').rglob(filename):
                            path.write_bytes(corrupted)
                    return result

                with mock.patch.object(fixture, 'cli', side_effect=corrupt):
                    with self.assertRaisesRegex(proof.ProofFailure, '^' + stage + '$'):
                        fixture.prove()

    def test_refused_owner_update_and_removal_preserve_import_bytes(self):
        fixture = self.fixture()
        fixture.import_items()
        catalog = fixture.root / '.hub-store/catalog'
        before = self.snapshot(catalog)
        fixture.guide.write_text('A different reviewed fixture guide.\n', encoding='utf-8')
        fixture.cli('import-skill', fixture.skill, '--owner', 'other-fixture', expected=1)
        self.assertEqual(self.snapshot(catalog), before)
        fixture.cli('remove', 'skill', 'catalog-sample', '--owner', 'other-fixture', '--yes', expected=1)
        self.assertEqual(self.snapshot(catalog), before)

    def test_local_edits_refuse_upgrade_and_removal_without_losing_content(self):
        fixture = self.fixture()
        fixture.import_items()
        catalog = fixture.root / '.hub-store/catalog'
        guide = catalog / 'skills/catalog-sample/references/guide.md'
        guide.write_text('Uncommitted fixture edit must survive.\n', encoding='utf-8')
        before = self.snapshot(catalog)
        fixture.cli('import-skill', fixture.skill, '--owner', 'fixture', expected=1)
        fixture.cli('remove', 'skill', 'catalog-sample', '--owner', 'fixture', '--yes', expected=1)
        self.assertEqual(self.snapshot(catalog), before)
        self.assertFalse((fixture.root / '.hub-store/history').exists())

    def test_malformed_config_and_unconfirmed_write_preserve_bytes(self):
        fixture = self.fixture()
        malformed = b'{fixture malformed JSON\n'
        fixture.claude.write_bytes(malformed)
        fixture.cli('connect', '--agent', 'claude', '--write', '--yes', expected=1)
        self.assertEqual(fixture.claude.read_bytes(), malformed)
        fixture.cli('connect', '--agent', 'codex', '--write', expected=1)
        self.assertEqual(fixture.codex.read_bytes(), fixture.original[fixture.codex])
        self.assertFalse(list(fixture.home.rglob('*.bak')))

    def test_symlinked_config_does_not_change_target_or_unrelated_files(self):
        fixture = self.fixture()
        target = fixture.home / 'preserved-config.json'
        original = fixture.claude.read_bytes()
        target.write_bytes(original)
        fixture.claude.unlink()
        fixture.claude.symlink_to(target)
        fixture.cli('connect', '--agent', 'claude', '--write', '--yes', expected=1)
        self.assertTrue(fixture.claude.is_symlink())
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(fixture.codex.read_bytes(), fixture.original[fixture.codex])
        self.assertFalse(list(fixture.home.rglob('*.bak')))

    def test_offline_guard_denies_sockets_and_child_processes_before_effects(self):
        fixture = self.fixture()
        marker = fixture.home / 'unexpected-child'
        code = ('import socket, subprocess, sys\n'
                'for action in (lambda: socket.socket(), lambda: subprocess.run([sys.executable, "-c", '
                + repr('from pathlib import Path; Path(' + repr(str(marker)) + ').touch()') + '])):\n'
                '    try: action()\n'
                '    except OSError: pass\n'
                '    else: raise AssertionError("boundary escaped")\n')
        proof.run([sys.executable, '-c', code], fixture.root, fixture.env)
        self.assertFalse(marker.exists())

    def test_corrupt_endpoint_output_fails_closed(self):
        fixture = self.fixture()
        (fixture.root / 'mcp_endpoint.py').write_text('print("not a protocol response")\n', encoding='utf-8')
        with self.assertRaisesRegex(proof.ProofFailure, '^invalid RPC response$'):
            fixture.catalog(present=False)

    def endpoint(self, fixture, body):
        (fixture.root / 'mcp_endpoint.py').write_text(textwrap.dedent(body), encoding='utf-8')

    def test_handshake_waits_for_response_before_initialized_and_tools(self):
        fixture = self.fixture()
        self.endpoint(fixture, '''
            import json, queue, sys, threading
            from pathlib import Path
            messages = queue.Queue()
            def read():
                for line in sys.stdin:
                    messages.put(json.loads(line))
                messages.put(None)
            threading.Thread(target=read, daemon=True).start()
            first = messages.get(timeout=3)
            Path('traffic.json').write_text(json.dumps([first]))
            assert first['method'] == 'initialize'
            try:
                premature = messages.get(timeout=0.25)
            except queue.Empty:
                pass
            else:
                Path('traffic.json').write_text(json.dumps([first, premature]))
                raise SystemExit(7)
            print(json.dumps({'jsonrpc': '2.0', 'id': first['id'], 'result': {
                'protocolVersion': '2024-11-05', 'serverInfo': {'name': 'agentlocker', 'version': 'fixture'},
                'capabilities': {'tools': {}}}}), flush=True)
            initialized = messages.get(timeout=3)
            tools = messages.get(timeout=3)
            Path('traffic.json').write_text(json.dumps([first, initialized, tools]))
            assert initialized == {'jsonrpc': '2.0', 'method': 'notifications/initialized'}
            assert tools['method'] == 'tools/list'
            print(json.dumps({'jsonrpc': '2.0', 'id': tools['id'], 'result': {'tools': []}}), flush=True)
            assert messages.get(timeout=3) is None
            Path('closed-input').touch()
        ''')
        results = fixture.rpc([fixture.initialize(), {'method': 'notifications/initialized'},
                               {'id': 2, 'method': 'tools/list'}])
        self.assertEqual(results[1], {'tools': []})
        traffic = json.loads((fixture.root / 'traffic.json').read_text())
        self.assertEqual([item['method'] for item in traffic],
                         ['initialize', 'notifications/initialized', 'tools/list'])
        self.assertTrue((fixture.root / 'closed-input').exists())

    def test_invalid_initialization_stops_traffic_and_reaps_child(self):
        fixture = self.fixture()
        valid = {'jsonrpc': '2.0', 'id': 1, 'result': {
            'protocolVersion': '2024-11-05', 'serverInfo': {'name': 'agentlocker', 'version': 'fixture'},
            'capabilities': {'tools': {}}}}
        variants = [('protocol', {'result': {**valid['result'], 'protocolVersion': 'unsupported'}}),
                    ('id', {'id': 99}), ('boolean-id', {'id': True}), ('jsonrpc', {'jsonrpc': '1.0'}),
                    ('identity', {'result': {**valid['result'], 'serverInfo': {'name': 'other'}}}),
                    ('capabilities', {'result': {**valid['result'], 'capabilities': {}}})]
        spawn = proof.asyncio.create_subprocess_exec
        children = []

        async def record(*args, **kwargs):
            child = await spawn(*args, **kwargs)
            children.append(child)
            return child

        for label, changes in variants:
            with self.subTest(label=label):
                response = {**valid, **changes}
                self.endpoint(fixture, '''
                    import json, sys
                    from pathlib import Path
                    first = sys.stdin.readline()
                    Path('traffic.json').write_text(first)
                    print(RESPONSE, flush=True)
                    for line in sys.stdin:
                        with Path('traffic.json').open('a') as stream:
                            stream.write(line)
                '''.replace('RESPONSE', repr(json.dumps(response))))
                with mock.patch.object(proof.asyncio, 'create_subprocess_exec', side_effect=record):
                    with self.assertRaises(proof.ProofFailure):
                        fixture.rpc([fixture.initialize(), {'method': 'notifications/initialized'},
                                     {'id': 2, 'method': 'tools/list'}])
                traffic = (fixture.root / 'traffic.json').read_text().splitlines()
                self.assertEqual(len(traffic), 1)
                self.assertEqual(json.loads(traffic[0])['method'], 'initialize')
                self.assertIsNotNone(children[-1].returncode)

    def test_unresponsive_endpoint_times_out_reaps_child_and_redacts_stderr(self):
        fixture = self.fixture()
        self.endpoint(fixture, '''
            import signal, sys, time
            from pathlib import Path
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            Path('traffic.json').write_text(sys.stdin.readline())
            sys.stderr.write('private fixture diagnostic' * 10000)
            sys.stderr.flush()
            time.sleep(30)
        ''')
        spawn = proof.asyncio.create_subprocess_exec
        children = []

        async def record(*args, **kwargs):
            child = await spawn(*args, **kwargs)
            children.append(child)
            return child

        started = time.monotonic()
        with mock.patch.object(proof, 'RPC_TIMEOUT', 2), \
                mock.patch.object(proof.asyncio, 'create_subprocess_exec', side_effect=record):
            with self.assertRaisesRegex(proof.ProofFailure, '^RPC unavailable or timed out$'):
                fixture.rpc([fixture.initialize(), {'method': 'notifications/initialized'}])
        self.assertLess(time.monotonic() - started, 10)
        self.assertIsNotNone(children[0].returncode)
        traffic = (fixture.root / 'traffic.json').read_text().splitlines()
        self.assertEqual(len(traffic), 1)
        self.assertEqual(json.loads(traffic[0])['method'], 'initialize')

    def test_timeout_and_main_failure_suppress_raw_diagnostics(self):
        sensitive = 'fixture diagnostic that must remain private'
        with mock.patch.object(proof.subprocess, 'run', side_effect=subprocess.TimeoutExpired(
                ['fixture'], 1, output=sensitive, stderr=sensitive)):
            with self.assertRaises(proof.ProofFailure) as failure:
                proof.run(['fixture'], ROOT, {})
        self.assertNotIn(sensitive, str(failure.exception))
        output = io.StringIO()
        with mock.patch.object(proof, 'Fixture', side_effect=ValueError(sensitive)), contextlib.redirect_stdout(output):
            self.assertEqual(proof.main(), 1)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary['status'], 'failed')
        self.assertNotIn(sensitive, output.getvalue())


class LocalCatalogCompatibilityTests(unittest.TestCase):
    def test_missing_tomllib_allows_discovery_and_deliberate_redacted_cli_failure(self):
        code = '''
import json, runpy, sys, unittest
sys.modules['tomllib'] = None
loader = unittest.TestLoader()
suite = loader.discover('tests', pattern='test_local_catalog_proof.py')
assert not loader.errors, loader.errors
assert suite.countTestCases() > 0
runpy.run_path('examples/test-servers/local_catalog_proof.py', run_name='__main__')
'''
        with tempfile.TemporaryDirectory() as directory:
            env = proof.environment(Path(directory))
            result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, '')
        summary = json.loads(result.stdout)
        self.assertEqual(summary['status'], 'failed')
        self.assertEqual(summary['diagnostic'], 'Python 3.11+ with tomllib required; proof not run')
        self.assertNotIn(str(ROOT), result.stdout)


class ReviewedCopyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.env = proof.environment(self.base)
        self.source = self.base / 'source'
        proof.run(['git', 'clone', '--shared', '--no-checkout', str(ROOT), str(self.source)], ROOT, self.env)
        proof.run(['git', 'checkout', 'HEAD', '--', 'hub.py', 'mcp_endpoint.py', 'manifest.json',
                   'checks/checker.py', '.gitignore'], self.source, self.env)
        script = self.source / proof.PROOF
        script.parent.mkdir(parents=True)
        script.write_bytes((ROOT / proof.PROOF).read_bytes())

    def copy(self, name):
        target = self.base / name
        target.mkdir()
        return target, proof.reviewed_copy(self.source, target, self.env)

    def test_working_bytes_digest_determinism_changes_and_ignored_private_exclusion(self):
        runtime = self.source / 'hub.py'
        changed = runtime.read_bytes() + b'\n# fixture working edit\n'
        runtime.write_bytes(changed)
        private = {'config.json': b'fixture private config',
                   '.hub-store/auth.json': b'fixture private store'}
        for name, data in private.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            proof.run(['git', 'check-ignore', '--no-index', '-q', '--', name], self.source, self.env)
        target, first = self.copy('first')
        self.assertEqual((target / 'hub.py').read_bytes(), changed)
        self.assertEqual(set(path.relative_to(target).as_posix() for path in target.rglob('*') if path.is_file()),
                         {'hub.py', 'mcp_endpoint.py', 'manifest.json', 'checks/checker.py', proof.PROOF})
        for name in (*private, '.git'):
            self.assertFalse((target / name).exists())
        _, second = self.copy('second')
        self.assertEqual(first, second)
        runtime.write_bytes(changed + b'\n')
        third_target, third = self.copy('third')
        self.assertEqual((third_target / 'hub.py').read_bytes(), changed + b'\n')
        self.assertNotEqual(first['sha256'], third['sha256'])
        self.assertEqual(first['revision'], third['revision'])

    def test_selected_symlink_file_refused(self):
        runtime = self.source / 'hub.py'
        preserved = self.source / 'preserved.py'
        runtime.rename(preserved)
        original = preserved.read_bytes()
        runtime.symlink_to(preserved)
        with self.assertRaisesRegex(proof.ProofFailure, '^symlinked source refused$'):
            self.copy('file-link')
        self.assertEqual(preserved.read_bytes(), original)

    def test_selected_symlink_ancestor_refused(self):
        checks = self.source / 'checks'
        preserved = self.source / 'preserved-checks'
        checks.rename(preserved)
        original = (preserved / 'checker.py').read_bytes()
        checks.symlink_to(preserved, target_is_directory=True)
        with self.assertRaisesRegex(proof.ProofFailure, '^symlinked source refused$'):
            self.copy('ancestor-link')
        self.assertEqual((preserved / 'checker.py').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
