from pathlib import Path, PurePosixPath
import subprocess
import unittest

from checks import validators

ROOT = Path(__file__).resolve().parents[1]
PACK_MISSING = not (ROOT / validators.POLICY_PACK).is_file()


EXPORTED_PACK_LICENSES = (
    'skills/architect/LICENSE',
    'skills/arena/LICENSE',
    'skills/babysit-pr/LICENSE',
    'skills/benchmark-checklist/LICENSE',
    'skills/create-verification-skill/LICENSE',
    'skills/figure-it-out/LICENSE',
    'skills/file-pr/LICENSE',
    'skills/full-send/LICENSE',
    'skills/full-stop/LICENSE',
    'skills/how/LICENSE',
    'skills/interrogate/LICENSE',
    'skills/leaving-pr-comments/LICENSE',
    'skills/maintain-verification-skill/LICENSE',
    'skills/no-comments/LICENSE',
    'skills/p3-mode/LICENSE',
    'skills/poteto-mode/LICENSE',
    'skills/principle-attack-the-premise/LICENSE',
    'skills/principle-boundary-discipline/LICENSE',
    'skills/principle-build-the-lever/LICENSE',
    'skills/principle-encode-lessons-in-structure/LICENSE',
    'skills/principle-exhaust-the-design-space/LICENSE',
    'skills/principle-experience-first/LICENSE',
    'skills/principle-explain-the-number/LICENSE',
    'skills/principle-fix-root-causes/LICENSE',
    'skills/principle-foundational-thinking/LICENSE',
    'skills/principle-guard-the-context-window/LICENSE',
    'skills/principle-laziness-protocol/LICENSE',
    'skills/principle-make-operations-idempotent/LICENSE',
    'skills/principle-migrate-callers-then-delete-legacy-apis/LICENSE',
    'skills/principle-minimize-reader-load/LICENSE',
    'skills/principle-model-the-domain/LICENSE',
    'skills/principle-never-block-on-the-human/LICENSE',
    'skills/principle-outcome-oriented-execution/LICENSE',
    'skills/principle-prove-it-works/LICENSE',
    'skills/principle-redesign-from-first-principles/LICENSE',
    'skills/principle-separate-before-serializing-shared-state/LICENSE',
    'skills/principle-sequence-verifiable-units/LICENSE',
    'skills/principle-subtract-before-you-add/LICENSE',
    'skills/principle-type-system-discipline/LICENSE',
    'skills/reflect/LICENSE',
    'skills/setup-p3/LICENSE',
    'skills/show-me-your-work/LICENSE',
    'skills/sol-loop/LICENSE',
    'skills/swarm/LICENSE',
    'skills/tdd/LICENSE',
    'skills/technical-writing/LICENSE',
    'skills/typescript-best-practices/LICENSE',
    'skills/unslop/LICENSE',
    'skills/why/LICENSE',
)


def unexported_pack_paths(names):
    return [name for name in names if validators.policy_file(name) and name not in EXPORTED_PACK_LICENSES]


def pack_path_missing(name):
    return PACK_MISSING and validators.policy_file(name)


class PublicExportTests(unittest.TestCase):
    def test_export_contains_existing_source_and_no_private_or_unreviewed_paths(self):
        paths = [line.strip() for line in (ROOT / 'public-export.txt').read_text().splitlines()
                 if line.strip() and not line.lstrip().startswith('#')]
        self.assertEqual(paths, sorted(set(paths)))
        self.assertTrue({'hub.py', 'LICENSE', 'README.md', 'manifest.json',
                         'tests/test_scanner.py', 'ui/index.html'} <= set(paths))
        forbidden = {'.git', '.cursor', 'config', 'worktrees', '.worktrees',
                     'scratch', '.scratch', '.hub-store', '.fleet', '.coldlocker', 'audit'}
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
                self.assertTrue((ROOT / name).is_file() or pack_path_missing(name))
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

    def test_exported_files_alone_run_the_cli_with_packs(self):
        import shutil
        import sys
        import tempfile

        selected = [line.strip() for line in (ROOT / 'public-export.txt').read_text().splitlines()
                    if line.strip() and not line.lstrip().startswith('#')]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for name in selected:
                if pack_path_missing(name):
                    continue
                (target / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, target / name)
            env = {'HOME': directory, 'USERPROFILE': directory, 'PATH': '', 'PYTHONDONTWRITEBYTECODE': '1'}
            result = subprocess.run([sys.executable, '-B', 'hub.py', 'pack', 'list'], cwd=target, env=env,
                                    capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        self.assertIn('pstack  off  6 skills', result.stdout)

    def test_owner_policy_pack_is_never_exported(self):
        selected = [line.strip() for line in (ROOT / 'public-export.txt').read_text().splitlines() if line.strip()]
        self.assertEqual(unexported_pack_paths(selected), [])

    def test_only_the_49_listed_pack_licenses_pass_the_pack_guard(self):
        self.assertEqual(len(set(EXPORTED_PACK_LICENSES)), 49)
        for name in EXPORTED_PACK_LICENSES:
            self.assertTrue(validators.policy_file(name), name)
        refused = ['skills/hill-climb/LICENSE', 'skills/verify-loop/LICENSE', 'skills/tdd/LICENSE.md',
                   'skills/tdd/references/LICENSE', 'skills/tdd/SKILL.md', 'hooks/LICENSE', 'skills/p3-mode/NOTICE']
        for name in refused:
            self.assertTrue(validators.policy_file(name), name)
        self.assertEqual(unexported_pack_paths([*EXPORTED_PACK_LICENSES, *refused]), refused)
