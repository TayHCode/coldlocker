import os
from unittest import mock

import scanner
from test_hub import HubCase


class SkillSizeTests(HubCase):
    def package(self, name, sizes):
        path = self.base / 'home' / '.claude' / 'skills' / name
        path.mkdir(parents=True)
        (path / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: Fixture skill\n---\nBody\n', encoding='utf-8')
        for index, size in enumerate(sizes):
            (path / f'data{index}.txt').write_text('x' * size, encoding='utf-8')
        return path

    def scanned(self, name):
        report = scanner.Inventory(self.base / 'home', (), 'linux', {}, lambda command: '', self.root).discover()
        return next(item for item in report['items'] if item['kind'] == 'skill' and item['name'] == name)

    def test_package_larger_than_limit_in_total_is_handled_alike(self):
        path = self.package('wide', [400000] * 3)
        self.assertGreater(sum(item.stat().st_size for item in path.iterdir()), scanner.LIMIT)
        self.assertNotEqual(self.scanned('wide')['status'], 'skipped')
        self.cli('import-skill', path)
        self.assertTrue((self.local / 'skills/wide/data0.txt').is_file())

    def test_file_over_limit_is_refused_by_both(self):
        path = self.package('huge', [scanner.LIMIT + 1])
        item = self.scanned('huge')
        self.assertEqual(item['status'], 'skipped')
        self.assertIn('package too large', item['reason'])
        self.cli('import-skill', path, code=1)
        self.assertFalse((self.local / 'skills/huge').exists())

    def test_too_many_files_is_refused_by_both(self):
        path = self.package('many', [1] * 1000)
        self.assertEqual(self.scanned('many')['status'], 'skipped')
        self.cli('import-skill', path, code=1)

    def test_too_many_files_is_skipped_whatever_the_listing_order(self):
        self.package('many', [1] * 1000)
        real = os.scandir

        class Reversed:
            def __init__(self, path):
                with real(path) as stream:
                    self.entries = iter(sorted(stream, key=lambda e: e.name == 'SKILL.md'))

            def __iter__(self):
                return self

            def __next__(self):
                return next(self.entries)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return None

        with mock.patch.object(scanner.os, 'scandir', Reversed):
            item = self.scanned('many')
        self.assertEqual(item['status'], 'skipped')
        self.assertIn('package too large', item['reason'])
