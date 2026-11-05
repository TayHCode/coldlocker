import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import modes
import gateway


REPOSITORY = Path(__file__).resolve().parent.parent


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root_patch = patch.object(gateway, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        platform = patch('sys.platform', modes.GATEWAY_PLATFORM)
        platform.start()
        self.addCleanup(platform.stop)
        (self.root / 'gateway').mkdir()
        for name in ('package.json', 'package-lock.json'):
            shutil.copy(REPOSITORY / 'gateway' / name, self.root / 'gateway' / name)

    def package(self, version=gateway.VERSION, complete=True, client_version='2.3.0', hono_version='4.13.11', node_server_version='2.1.3', prefix=None):
        package = gateway.runtime_package(prefix)
        package.mkdir(parents=True)
        (package / 'package.json').write_text(json.dumps({'version': version}), encoding='utf-8')
        if complete:
            (package / 'dist').mkdir()
            (package / 'dist/index.js').touch()
        prefix = self.root / '.scratch/mcphub-runtime' if prefix is None else prefix
        for name, version in {'@modelcontextprotocol/client': client_version, 'hono': hono_version, '@hono/node-server': node_server_version}.items():
            if version is not None:
                dependency = prefix / 'node_modules' / name
                dependency.mkdir(parents=True)
                (dependency / 'package.json').write_text(json.dumps({'version': version}), encoding='utf-8')


    @patch('gateway.shutil.which', return_value='/bin/tool')
    @patch('gateway.subprocess.run')
    def test_install_pinned_private_runtime_and_restores_umask(self, run, which):
        previous = os.umask(0o022)
        self.addCleanup(os.umask, previous)
        def execute(argv, **kwargs):
            if argv == ['node', '--version']:
                return subprocess.CompletedProcess(argv, 0, 'v20.0.0\n')
            staged = Path(argv[-1])
            self.assertEqual(argv[:-1], ['npm', 'ci', '--ignore-scripts', '--prefix'])
            self.assertEqual(staged.parent, self.root / '.scratch')
            self.assertTrue(staged.name.startswith('mcphub-install-'))
            for name in ('package.json', 'package-lock.json'):
                self.assertEqual((staged / name).read_bytes(),
                                 (self.root / 'gateway' / name).read_bytes())
            self.assertTrue(kwargs['check'])
            for directory in (self.root / '.scratch', staged, staged / 'home', self.root / '.scratch/npm-cache'):
                modes.check(self, directory, 0o700)
            self.assertEqual(kwargs['env']['HOME'], str(staged / 'home'))
            if modes.ENFORCED:
                self.assertEqual(os.umask(0o077), 0o077)
            self.package(prefix=staged)
            return subprocess.CompletedProcess(argv, 0)
        run.side_effect = execute
        gateway.install()
        if modes.ENFORCED:
            self.assertEqual(os.umask(0o022), 0o022)
        self.assertEqual(run.call_count, 2)

    def test_tracked_lockfile_pins_every_package_to_the_registry(self):
        manifest = json.loads((REPOSITORY / 'gateway/package.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['dependencies'], {'@samanhappy/mcphub': gateway.VERSION})
        self.assertEqual(manifest['overrides'], gateway.RUNTIME_PINS)
        self.assertEqual(gateway.RUNTIME_PINS, {'@modelcontextprotocol/client': '2.3.0', 'hono': '4.13.11', '@hono/node-server': '2.1.3'})
        lock = json.loads((REPOSITORY / 'gateway/package-lock.json').read_text(encoding='utf-8'))
        self.assertEqual(lock['lockfileVersion'], 3)
        packages = {name: value for name, value in lock['packages'].items() if name}
        self.assertEqual(packages['node_modules/@samanhappy/mcphub']['version'], gateway.VERSION)
        for name, version in gateway.RUNTIME_PINS.items():
            self.assertEqual(packages['node_modules/' + name]['version'], version)
        self.assertGreater(len(packages), 1)
        for name, value in packages.items():
            with self.subTest(package=name):
                self.assertTrue(value['resolved'].startswith('https://registry.npmjs.org/'))
                self.assertTrue(value['integrity'].startswith('sha512-'))

    def test_missing_node_and_npm_are_actionable(self):
        for missing in ('node', 'npm'):
            with self.subTest(missing=missing), patch('gateway.shutil.which', side_effect=lambda n: None if n == missing else '/bin/tool'), patch('gateway.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'v20.0.0')):
                with self.assertRaisesRegex(gateway.SetupError, missing + ' is required'):
                    gateway.install()

    @patch('gateway.shutil.which', return_value='/bin/tool')
    @patch('gateway.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'v18.0.0'))
    def test_old_node_refused_before_npm(self, run, which):
        with self.assertRaisesRegex(gateway.SetupError, 'Node 20'):
            gateway.install()
        self.assertEqual(run.call_count, 1)

    @patch('gateway.check_node')
    @patch('gateway.prerequisite')
    def test_existing_version_refused_without_install(self, prerequisite, node):
        self.package('0.0.0')
        with patch('gateway.subprocess.run') as run:
            with self.assertRaisesRegex(gateway.SetupError, 'version refused'):
                gateway.install()
            run.assert_not_called()

    @patch('gateway.check_node')
    @patch('gateway.prerequisite')
    def test_existing_pinned_runtime_is_noop(self, prerequisite, node):
        self.package()
        with patch('gateway.subprocess.run') as run:
            gateway.install()
            run.assert_not_called()

    def test_vulnerable_client_cannot_launch_with_matching_mcphub(self):
        self.package(client_version='2.2.0')
        settings = self.root / '.hub-store/gateway'
        settings.mkdir(parents=True)
        path = settings / 'mcp_settings.json'
        path.write_text('preserve settings', encoding='utf-8')
        with patch('gateway.check_node'), patch('gateway.prerequisite'), patch('gateway.state', return_value=None), \
                patch('gateway.subprocess.Popen') as launch:
            with self.assertRaisesRegex(gateway.SetupError, 'package refused.*client 2.3.0.*gateway.py install'):
                gateway.start(settings, gateway.PORT)
            launch.assert_not_called()
        self.assertEqual(path.read_text(encoding='utf-8'), 'preserve settings')
        self.assertFalse(gateway.startable())

    def test_missing_or_malformed_client_is_refused(self):
        self.package(client_version=None)
        with self.assertRaisesRegex(gateway.SetupError, 'package missing'):
            gateway.check_runtime()
        client = self.root / '.scratch/mcphub-runtime/node_modules/@modelcontextprotocol/client'
        client.mkdir(parents=True)
        for value in ('{', '[]'):
            (client / 'package.json').write_text(value, encoding='utf-8')
            with self.subTest(value=value), self.assertRaisesRegex(gateway.SetupError, 'package missing or unreadable'):
                gateway.check_runtime()

    def test_nested_vulnerable_dependency_cannot_shadow_fixed_root_package(self):
        self.package()
        for name, version in {'@modelcontextprotocol/client': '2.2.0', 'hono': '4.13.7', '@hono/node-server': '1.19.17'}.items():
            nested = gateway.runtime_package() / 'node_modules' / name
            nested.mkdir(parents=True)
            (nested / 'package.json').write_text(json.dumps({'version': version}), encoding='utf-8')
            with self.subTest(name=name), self.assertRaisesRegex(gateway.SetupError, 'package refused'):
                gateway.check_runtime()
            (nested / 'package.json').write_text(json.dumps({'version': gateway.RUNTIME_PINS[name]}), encoding='utf-8')

    @patch('gateway.check_node')
    @patch('gateway.prerequisite')
    def test_fixed_client_with_stale_hono_is_refused_and_upgraded(self, prerequisite, node):
        self.package(hono_version='4.13.7')
        with self.assertRaisesRegex(gateway.SetupError, 'hono 4.13.11.*gateway.py install'):
            gateway.check_runtime()
        self.assertFalse(gateway.startable())
        def execute(argv, **kwargs):
            self.package(prefix=Path(argv[-1]))
            return subprocess.CompletedProcess(argv, 0)
        with patch('gateway.subprocess.run', side_effect=execute) as run:
            gateway.install()
            run.assert_called_once()
        gateway.check_runtime()
        backup, = (self.root / '.scratch').glob('mcphub-runtime.backup-*')
        self.assertEqual(gateway.runtime_package_versions(backup), {
            '@modelcontextprotocol/client': ['2.3.0'], 'hono': ['4.13.7'], '@hono/node-server': ['2.1.3']})
        self.assertEqual(gateway.runtime_package_versions(), {
            '@modelcontextprotocol/client': ['2.3.0'], 'hono': ['4.13.11'], '@hono/node-server': ['2.1.3']})

    def test_missing_hono_is_refused_even_with_fixed_client(self):
        self.package(hono_version=None)
        with self.assertRaisesRegex(gateway.SetupError, 'package missing or unreadable: hono'):
            gateway.check_runtime()

    @patch('gateway.check_node')
    @patch('gateway.prerequisite')
    def test_failed_legacy_install_is_retried_without_losing_partial_files(self, prerequisite, node):
        prefix = self.root / '.scratch/mcphub-runtime'
        (prefix / 'home').mkdir(parents=True)
        (prefix / 'home/unfamiliar').write_bytes(b'preserve partial install')
        def execute(argv, **kwargs):
            self.package(prefix=Path(argv[-1]))
            return subprocess.CompletedProcess(argv, 0)
        with patch('gateway.subprocess.run', side_effect=execute) as run:
            gateway.install()
            run.assert_called_once()
        gateway.check_runtime()
        backup, = prefix.parent.glob('mcphub-runtime.backup-*')
        self.assertEqual((backup / 'home/unfamiliar').read_bytes(), b'preserve partial install')

    @patch('gateway.check_node')
    @patch('gateway.prerequisite')
    def test_upgrade_preserves_original_runtime_and_private_configuration(self, prerequisite, node):
        self.package(client_version='2.2.0', hono_version='4.13.7')
        prefix = self.root / '.scratch/mcphub-runtime'
        (prefix / 'unfamiliar.bin').write_bytes(b'preserve runtime contents\x00')
        snapshots = {}
        for relative in ('config.json', '.hub-store/secrets/fixture-token',
                         '.hub-store/gateway/mcp_settings.json', '.codex/config.toml'):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'{}' if relative == 'config.json' else b'fixture-private-content')
            snapshots[path] = path.read_bytes(), path.stat().st_mode

        def execute(argv, **kwargs):
            self.assertEqual(gateway.runtime_package_versions()['@modelcontextprotocol/client'], ['2.2.0'])
            staged = Path(argv[-1])
            self.assertNotEqual(staged, prefix)
            for name in ('package.json', 'package-lock.json'):
                self.assertEqual((staged / name).read_bytes(), (self.root / 'gateway' / name).read_bytes())
            self.package(prefix=staged)
            return subprocess.CompletedProcess(argv, 0)

        with patch('gateway.subprocess.run', side_effect=execute) as run:
            gateway.install()
            run.assert_called_once()
        gateway.check_runtime()
        self.assertEqual(gateway.runtime_package_versions()['@modelcontextprotocol/client'], ['2.3.0'])
        backups = list(prefix.parent.glob('mcphub-runtime.backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(gateway.runtime_package_versions(backups[0])['@modelcontextprotocol/client'], ['2.2.0'])
        self.assertEqual(gateway.runtime_package_versions(backups[0])['hono'], ['4.13.7'])
        self.assertEqual((backups[0] / 'unfamiliar.bin').read_bytes(), b'preserve runtime contents\x00')
        for path, expected in snapshots.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mode), expected)

    @patch('gateway.check_node')
    @patch('gateway.prerequisite')
    def test_failed_upgrade_keeps_original_and_failed_staging_for_inspection(self, prerequisite, node):
        self.package(client_version='2.2.0')
        prefix = self.root / '.scratch/mcphub-runtime'
        (prefix / 'unfamiliar').write_bytes(b'preserve')
        with patch('gateway.subprocess.run', side_effect=subprocess.CalledProcessError(1, ['npm'])):
            with self.assertRaises(subprocess.CalledProcessError):
                gateway.install()
        self.assertEqual(gateway.runtime_package_versions()['@modelcontextprotocol/client'], ['2.2.0'])
        self.assertEqual((prefix / 'unfamiliar').read_bytes(), b'preserve')
        self.assertEqual(list(prefix.parent.glob('mcphub-runtime.backup-*')), [])
        staged = list(prefix.parent.glob('mcphub-install-*'))
        self.assertEqual(len(staged), 1)
        modes.check(self, staged[0], 0o700)

    @patch('gateway.check_node')
    @patch('gateway.prerequisite')
    def test_invalid_staged_runtime_never_replaces_original(self, prerequisite, node):
        self.package(client_version='2.2.0')
        def execute(argv, **kwargs):
            self.package(client_version='2.2.0', prefix=Path(argv[-1]))
            return subprocess.CompletedProcess(argv, 0)
        with patch('gateway.subprocess.run', side_effect=execute):
            with self.assertRaisesRegex(gateway.SetupError, 'package refused'):
                gateway.install()
        self.assertEqual(gateway.runtime_package_versions()['@modelcontextprotocol/client'], ['2.2.0'])
        self.assertEqual(list((self.root / '.scratch').glob('mcphub-runtime.backup-*')), [])

    @patch('gateway.check_node')
    @patch('gateway.prerequisite')
    def test_failed_swap_restores_original_runtime(self, prerequisite, node):
        self.package(client_version='2.2.0')
        prefix = self.root / '.scratch/mcphub-runtime'
        original_rename = Path.rename
        def rename(path, target):
            if path.name.startswith('mcphub-install-'):
                raise OSError('fixture swap failure')
            return original_rename(path, target)
        def execute(argv, **kwargs):
            self.package(prefix=Path(argv[-1]))
            return subprocess.CompletedProcess(argv, 0)
        with patch('gateway.subprocess.run', side_effect=execute), patch.object(Path, 'rename', rename):
            with self.assertRaisesRegex(OSError, 'swap failure'):
                gateway.install()
        self.assertEqual(gateway.runtime_package_versions()['@modelcontextprotocol/client'], ['2.2.0'])
        self.assertTrue(prefix.is_dir())

    @patch('gateway.check_node')
    @patch('gateway.prerequisite')
    def test_unfamiliar_client_is_not_overwritten(self, prerequisite, node):
        self.package(client_version='2.9.0')
        with patch('gateway.subprocess.run') as run:
            with self.assertRaisesRegex(gateway.SetupError, 'unfamiliar runtime'):
                gateway.install()
            run.assert_not_called()
        self.assertEqual(gateway.runtime_package_versions()['@modelcontextprotocol/client'], ['2.9.0'])

    @patch('gateway.check_node')
    @patch('gateway.prerequisite')
    def test_upgrade_never_stops_a_running_gateway(self, prerequisite, node):
        self.package(client_version='2.2.0')
        with patch('gateway.state', return_value={'pid': 42}), patch('gateway.stop') as stop, \
                patch('gateway.subprocess.run') as run:
            with self.assertRaisesRegex(gateway.SetupError, 'stop the gateway'):
                gateway.install()
            stop.assert_not_called()
            run.assert_not_called()

    def test_active_runtime_lock_blocks_install_without_touching_store(self):
        with gateway.installation_lock(exclusive=False), patch('gateway.subprocess.run') as run:
            with self.assertRaisesRegex(gateway.SetupError, 'runtime is busy'):
                gateway.install()
            run.assert_not_called()
        self.assertFalse((self.root / '.hub-store').exists())

    @unittest.skipUnless(modes.ENFORCED, 'Windows does not keep a POSIX umask')
    @patch('gateway.check_node')
    @patch('gateway.prerequisite')
    def test_failed_npm_restores_umask(self, prerequisite, node):
        previous = os.umask(0o022)
        self.addCleanup(os.umask, previous)
        with patch('gateway.subprocess.run', side_effect=subprocess.CalledProcessError(1, ['npm'])):
            with self.assertRaises(subprocess.CalledProcessError):
                gateway.install()
        self.assertEqual(os.umask(0o022), 0o022)

    def test_missing_and_incomplete_runtime(self):
        with self.assertRaisesRegex(gateway.SetupError, 'gateway.py install'):
            gateway.check_runtime()
        self.package(complete=False)
        with self.assertRaisesRegex(gateway.SetupError, 'runtime incomplete'):
            gateway.check_runtime()

    @patch('gateway.check_node')
    @patch('gateway.prerequisite')
    @patch('gateway.check_runtime')
    def test_port_conflict_never_launches(self, runtime, prerequisite, node):
        (self.root / 'mcp_settings.json').touch()
        with socket.socket() as listener:
            listener.bind((gateway.LOOPBACK, 0))
            listener.listen()
            with patch('gateway.state', return_value=None), patch('gateway.subprocess.Popen') as launch:
                with self.assertRaisesRegex(gateway.SetupError, 'port unavailable'):
                    gateway.start(self.root, listener.getsockname()[1])
                launch.assert_not_called()

    @patch('gateway.check_node')
    @patch('gateway.prerequisite')
    @patch('gateway.check_runtime')
    def test_port_probe_allows_time_wait(self, runtime, prerequisite, node):
        (self.root / 'mcp_settings.json').touch()
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((gateway.LOOPBACK, 0))
            listener.listen()
            port = listener.getsockname()[1]
            with socket.socket() as reservation:
                reservation.bind((gateway.LOOPBACK, 0))
                client_port = reservation.getsockname()[1]
            with socket.socket() as client:
                client.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                client.bind((gateway.LOOPBACK, client_port))
                client.connect((gateway.LOOPBACK, port))
                accepted, _ = listener.accept()
                client.shutdown(socket.SHUT_WR)
                self.assertEqual(accepted.recv(1), b'')
                accepted.close()
                client.close()
        if os.name == 'nt':
            self.skipTest('TIME_WAIT port reuse semantics are POSIX-specific')
        with socket.socket() as unavailable:
            with self.assertRaises(OSError):
                unavailable.bind((gateway.LOOPBACK, client_port))
        with patch('gateway.state', return_value=None), patch(
                'gateway.subprocess.Popen', side_effect=RuntimeError('probe passed')) as launch:
            with self.assertRaisesRegex(RuntimeError, 'probe passed'):
                gateway.start(self.root, client_port)
            launch.assert_called_once()

    def test_cli_prints_only_safe_setup_message(self):
        with patch('sys.argv', ['gateway.py', 'install']), patch('gateway.check_node', side_effect=gateway.SetupError('node is required')), contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(gateway.main(), 1)
        self.assertEqual(output.getvalue(), 'node is required\n')

    def test_gateway_doc_states_node_minimum_and_harmless_pgvector_warning(self):
        lock = json.loads((REPOSITORY / 'gateway/package-lock.json').read_text(encoding='utf-8'))['packages']
        self.assertEqual(lock['node_modules/pgvector']['engines']['node'], '>=22')
        self.assertEqual(lock['node_modules/@samanhappy/mcphub']['engines']['node'], '>=20.0.0')
        document = (REPOSITORY / 'docs/gateway.md').read_text(encoding='utf-8')
        self.assertRegex(document, r'Node 20\.19 or newer')
        self.assertRegex(document, r'EBADENGINE[^\n]*\n?[^\n]*pgvector|pgvector[^\n]*\n?[^\n]*EBADENGINE')
        self.assertIn('harmless', document)
        self.assertIn('Node 24 is recommended', document)
        self.assertIn('That result is historical', document)
