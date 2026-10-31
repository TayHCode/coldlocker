from pathlib import Path, PurePosixPath
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PublicExportTests(unittest.TestCase):
    def test_export_contains_existing_source_and_no_private_or_unreviewed_paths(self):
        paths = [line.strip() for line in (ROOT / 'public-export.txt').read_text().splitlines()
                 if line.strip() and not line.lstrip().startswith('#')]
        self.assertEqual(paths, sorted(set(paths)))
        self.assertTrue({'hub.py', 'LICENSE', 'README.md', 'manifest.json',
                         'tests/test_scanner.py', 'ui/index.html'} <= set(paths))
        forbidden = {'.git', '.cursor', 'config', 'worktrees', '.worktrees',
                     'scratch', '.scratch', '.hub-store', '.fleet', 'audit'}
        for name in paths:
            with self.subTest(path=name):
                path = PurePosixPath(name)
                self.assertFalse(path.is_absolute())
                self.assertEqual(path.as_posix(), name)
                self.assertFalse(set(path.parts) & forbidden)
                self.assertTrue('.github' not in path.parts or name == '.github/ISSUE_TEMPLATE/bug_report.md')
                self.assertFalse(name != 'AGENTS.md' and any(
                    part.startswith(('STATE', 'AGENTS', 'VISION')) for part in path.parts))
                self.assertNotIn('..', path.parts)
                self.assertFalse(any(char in name for char in '\\*?[]:'))
                self.assertFalse(name.startswith('site/screens/'))
                self.assertNotEqual(name, 'docs/public-export.md')
                self.assertNotIn(name, ('config.json', 'config.local.json'))
                self.assertTrue((ROOT / name).is_file())
                for parent in (ROOT / name, *(ROOT / name).parents):
                    if parent == ROOT:
                        break
                    self.assertFalse(parent.is_symlink())

    def test_retained_license_documents_match_exact_review_digests(self):
        import hashlib
        import json

        review = json.loads((ROOT / 'config/public-export-review.json').read_text())
        entries = review['dependency_licenses']
        self.assertTrue(entries)
        for entry in entries.values():
            with self.subTest(path=entry['path']):
                self.assertEqual(hashlib.sha256((ROOT / entry['path']).read_bytes()).hexdigest(),
                                 entry['sha256'])

    def test_manifest_items_and_supporting_files_are_included(self):
        import json

        selected = set((ROOT / 'public-export.txt').read_text().splitlines())
        tracked = subprocess.check_output(
            ['git', '-C', str(ROOT), 'ls-files', '-z']).decode().split('\0')[:-1]
        manifest = json.loads((ROOT / 'manifest.json').read_text())
        for collection in ('skills', 'rules', 'mcp_servers', 'routines'):
            for entry in manifest[collection]:
                files = [name for name in tracked
                         if name == entry['path'] or name.startswith(entry['path'] + '/')]
                self.assertTrue(files)
                for name in files:
                    self.assertIn(name, selected)
