import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import agents_sync
import calllog
import connect
import gateway_render
import hub
import importers
import mcp_endpoint
import store


class SymlinkAncestorTests(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        real = Path(holder.name).resolve() / 'real'
        real.mkdir()
        self.link = Path(holder.name).resolve() / 'link'
        self.link.symlink_to(real)
        patcher = mock.patch.object(tempfile, 'tempdir', str(self.link))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.elsewhere = Path(holder.name).resolve() / 'elsewhere'
        self.elsewhere.mkdir()
        self.root = self.link / 'root'
        self.root.mkdir()

    def plant(self, location):
        location.symlink_to(self.elsewhere, target_is_directory=True)
        return location

    def test_store_path_below_symlinked_ancestor(self):
        path = self.root / '.hub-store' / 'secrets'
        self.assertEqual(store.ordinary_path(path), path)
        self.assertEqual(store.ordinary_path(path, self.root), path)

    def test_store_symlink_inside_or_as_target_refused(self):
        inner = self.plant(self.root / '.hub-store')
        for path in (inner, inner / 'secrets'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                store.ordinary_path(path)
            with self.subTest(path=path, base=True), self.assertRaises(ValueError):
                store.ordinary_path(path, self.root)

    def test_store_path_outside_explicit_base_refused(self):
        with self.assertRaises(ValueError):
            store.ordinary_path(self.elsewhere / 'x', self.root)

    def test_hub_ordinary_parents_below_symlinked_ancestor(self):
        parent = self.root / '.hub-store' / 'catalog' / 'skills'
        hub.ordinary_parents(parent)
        self.plant(self.root / '.hub-store')
        for path in (self.root / '.hub-store', parent):
            with self.subTest(path=path), self.assertRaises(ValueError):
                hub.ordinary_parents(path)

    def test_hub_ordinary_parents_refuses_non_directory(self):
        (self.root / 'file').write_text('x', encoding='utf-8')
        with self.assertRaises(ValueError):
            hub.ordinary_parents(self.root / 'file' / 'child')

    def test_calllog_directory_below_symlinked_ancestor(self):
        self.assertTrue(calllog.directory(self.root).is_dir())

    def test_importers_selected_path(self):
        (self.root / 'a').mkdir()
        self.assertEqual(importers.selected_path(self.root, 'a'), self.root / 'a')
        self.plant(self.root / 'b')
        with self.assertRaises(ValueError):
            importers.selected_path(self.root, 'b')

    def test_importers_selected_root_symlink_refused(self):
        root = self.plant(self.link / 'target')
        with self.assertRaises(ValueError):
            importers.selected_path(root)

    def test_agents_sync_ordinary(self):
        path = self.root / '.agents' / 'agents.json'
        self.assertEqual(agents_sync.ordinary(path), path)
        self.plant(self.root / '.agents')
        with self.assertRaises(ValueError):
            agents_sync.ordinary(path)

    def test_connect_ordinary_under_symlinked_home(self):
        home = self.link / 'home'
        path = home / '.claude' / 'settings.json'
        with mock.patch.dict(os.environ, {'HOME': str(home), 'USERPROFILE': str(home)}):
            connect.ordinary(path)
            home.mkdir()
            self.plant(home / '.claude')
            with self.assertRaises(ValueError):
                connect.ordinary(path)

    def test_connect_target_symlink_refused(self):
        home = self.link / 'home'
        home.mkdir()
        target = home / 'settings.json'
        target.symlink_to(self.elsewhere / 'settings.json')
        with mock.patch.dict(os.environ, {'HOME': str(home), 'USERPROFILE': str(home)}), self.assertRaises(ValueError):
            connect.ordinary(target)

    def test_gateway_render_output_path(self):
        target = self.link / 'out' / 'mcp.json'
        self.assertEqual(gateway_render.output_path(self.root, target), target)
        self.plant(self.link / 'out2')
        with self.assertRaises(ValueError):
            gateway_render.output_path(self.root, self.link / 'out2' / 'mcp.json')

    def test_repo_file(self):
        self.assertEqual(mcp_endpoint.repo_file(self.root, 'manifest.json'), self.root / 'manifest.json')
        self.plant(self.root / 'skills')
        with self.assertRaises(ValueError):
            mcp_endpoint.repo_file(self.root, 'skills/x/SKILL.md')


if __name__ == '__main__':
    unittest.main()
