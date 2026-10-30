import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile


SPEC = importlib.util.spec_from_file_location('build_site', Path(__file__).resolve().parents[1] / 'tools/build_site.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class PublicPackageTests(unittest.TestCase):
    def fixture(self, root):
        (root / '.git').mkdir()
        names = ['site/' + name for name in builder.SITE_FILES]
        names += ['CONTRIBUTING.md', 'site/README.md', 'tools/build_site.py', 'tests/test_build_site.py', 'hub.py']
        names += ['site/screens/' + name for name in builder.SCREENS]
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('fixture: ' + name).encode())
        return names

    def test_private_state_and_maintainer_identity_never_enter_public_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = self.fixture(root)
            excluded = ['.hub-store/catalog/manifest.json', '.scratch/evidence.txt',
                        'remote/wrangler.local.jsonc', 'config.json', '.env',
                        'config/commit-email-allowlist.txt', 'audit/findings-v12.md', '.github/ci.py']
            for name in excluded:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture-private-content')
            with patch.object(builder.subprocess, 'check_output', return_value=('\0'.join(names + excluded) + '\0').encode()):
                exported = builder.source_files(root)
            self.assertEqual(set(exported), set(names) | {'config/README.md'})
            self.assertIn(b'python3 hub.py doctor', exported['config/README.md'])
            self.assertNotIn(b'fixture-private-content', b''.join(exported.values()))

    def test_source_rejects_symlinks_and_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = self.fixture(root)
            for bad in ('../outside', '/outside'):
                with self.subTest(path=bad), patch.object(builder.subprocess, 'check_output', return_value=('\0'.join(names + [bad]) + '\0').encode()):
                    with self.assertRaises(ValueError): builder.source_files(root)
            target = root / 'hub.py'
            with patch.object(Path, 'is_symlink', lambda path: path == target), patch.object(builder.subprocess, 'check_output', return_value=('\0'.join(names) + '\0').encode()):
                with self.assertRaises(ValueError): builder.source_files(root)
            target = root / 'site/screens'
            with patch.object(Path, 'is_symlink', lambda path: path == target), patch.object(builder.subprocess, 'check_output', return_value=('\0'.join(names) + '\0').encode()):
                with self.assertRaises(ValueError): builder.source_files(root)

    def test_snapshot_builder_reports_checkout_requirement_without_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(builder, 'ROOT', root), patch.object(builder.subprocess, 'check_output', side_effect=subprocess.CalledProcessError(128, 'git')):
                with self.assertRaisesRegex(ValueError, 'development Git checkout'):
                    builder.build(root / 'output')
            self.assertFalse((root / 'output').exists())

    def test_snapshot_does_not_reuse_an_enclosing_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            (root / '.git').rmdir()
            with patch.object(builder.subprocess, 'check_output') as git:
                with self.assertRaisesRegex(ValueError, 'development Git checkout'):
                    builder.source_files(root)
            git.assert_not_called()

    def test_zip_and_tar_retain_identical_source_bytes_and_are_reproducible(self):
        files = {'hub.py': b'print("fixture")\r\n', 'ui/icon.bin': b'\x00\xff\x01'}
        with tempfile.TemporaryDirectory() as directory:
            roots = [Path(directory) / name for name in ('first', 'second')]
            for root in roots:
                root.mkdir()
                with patch.object(builder.subprocess, 'check_output', side_effect=AssertionError('Archive assembly must not read Git')):
                    manifest = builder.archives(files, root, 'a' * 40)
                self.assertEqual(manifest['file_count'], 2)
                self.assertEqual(manifest['source_revision'], 'a' * 40)
                self.assertIn('base revision', manifest['source_revision_role'])
                self.assertTrue(manifest['working_tree_snapshot'])
                for name, data in files.items(): self.assertEqual(manifest['files'][name], hashlib.sha256(data).hexdigest())
                with tarfile.open(root / 'downloads/agentlocker.tar.gz') as archive:
                    self.assertEqual({m.name: archive.extractfile(m).read() for m in archive}, {'agentlocker/' + k: v for k, v in files.items()})
                with zipfile.ZipFile(root / 'downloads/agentlocker.zip') as archive:
                    self.assertEqual({name: archive.read(name) for name in archive.namelist()}, {'agentlocker/' + k: v for k, v in files.items()})
                self.assertEqual(json.loads((root / 'downloads/release.json').read_text())['files'], manifest['files'])
            for name in ('agentlocker.tar.gz', 'agentlocker.zip', 'release.json', 'SHA256SUMS.txt'):
                self.assertEqual((roots[0] / 'downloads' / name).read_bytes(), (roots[1] / 'downloads' / name).read_bytes())

    def test_build_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'sentinel').write_text('keep')
            with self.assertRaises(ValueError): builder.build(root)
            self.assertEqual((root / 'sentinel').read_text(), 'keep')

    def test_public_alias_is_validated_and_private_forwarding_configuration_is_not_read(self):
        placeholder = 'No private reporting inbox is configured for this build. Do not report credentials or private catalog content in public issues.'
        page = builder.security_page(placeholder, 'security@example.org')
        self.assertIn('href="mailto:security@example.org"', page)
        self.assertNotIn(placeholder, page)
        self.assertEqual(builder.security_page(placeholder, None), placeholder)
        for invalid in ['not-an-address', 'test@example.org\nBcc:other@example.org', '" onmouseover="alert(1)@example.org']:
            with self.subTest(contact=invalid), self.assertRaises(ValueError):
                builder.security_page(placeholder, invalid)

    def test_repository_link_is_optional_and_rejects_credentials_and_markup(self):
        template = (builder.ROOT / 'site/index.html').read_text(encoding='utf-8')
        self.assertIn(builder.DEFAULT_REPOSITORY, template)
        self.assertEqual(builder.repository_page(template, None), template)
        self.assertIn('Windows: 3.12.4+', template)
        fork = builder.repository_page(template, 'https://github.com/example/agentlocker')
        self.assertNotIn(builder.DEFAULT_REPOSITORY, fork)
        self.assertIn('git clone https://github.com/example/agentlocker\n', fork)
        for invalid in ('http://github.com/example/repo', 'https://user:password@example.org/example/repo',
                        'https://github.com/example/repo?token=value', 'https://github.com/example/repo"'):
            with self.subTest(url=invalid), self.assertRaises(ValueError):
                builder.repository_page(template, invalid)


if __name__ == '__main__': unittest.main()
