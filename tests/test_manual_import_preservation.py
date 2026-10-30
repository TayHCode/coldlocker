import contextlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import hub
import gateway_adapter
import gateway_render
from checks import validators
from store import FileStore
from test_hub import HubCase


class ManualImportPreservationTests(HubCase):
    kinds = ('skill', 'rule', 'mcp', 'routine', 'plugin', 'connection')

    def item(self, kind):
        if kind == 'skill':
            return self.source, 'skills/sample', 'skills'
        if kind == 'rule':
            source = self.base / 'notes.md'
            source.write_bytes(b'Incoming notes\n')
            return source, 'rules/notes.md', 'rules'
        content = {
            'mcp': {'name': 'sample', 'description': 'Fixture', 'transport': 'stdio', 'command': 'example', 'args': []},
            'routine': {'id': 'sample', 'description': 'Fixture', 'owner': 'maintainer', 'runner': 't3-scheduler',
                        'schedule': {'type': 'interval', 'every_seconds': 60}, 'timezone': 'UTC',
                        'prompt': 'prompt.md', 'overlap': 'skip', 'timeout_seconds': 60, 'enabled': False},
            'plugin': {'name': 'sample', 'agent': 'claude', 'source': 'fixture-package', 'version': '1',
                       'enabled': False, 'install_hint': 'Fixture package'},
            'connection': {'name': 'sample', 'provider': 'fixture', 'endpoint': 'https://example.invalid/mcp',
                           'auth_status': 'unknown'},
        }[kind]
        source = self.base / (kind + '-input') / 'input.json'
        source.parent.mkdir(exist_ok=True)
        hub.write_json(source, content)
        if kind == 'routine':
            (source.parent / 'prompt.md').write_bytes(b'Fixture prompt\n')
        relative, collection = {
            'mcp': ('mcp/servers/sample.json', 'mcp_servers'),
            'routine': ('routines/sample/routine.json', 'routines'),
            'plugin': ('plugins/sample.json', 'plugins'),
            'connection': ('connections/sample.json', 'connections'),
        }[kind]
        return source, relative, collection

    def args(self, kind, source, **options):
        return SimpleNamespace(command='import-' + kind, path=str(source), owner='maintainer',
                               origin='local-import', name=None, approve_risky=False, **options)

    def run_import(self, kind, source, **options):
        with contextlib.redirect_stdout(io.StringIO()):
            return hub.import_item(self.args(kind, source, **options), self.root)

    def tree(self):
        parent = self.root / '.hub-store'
        return {p.relative_to(parent).as_posix(): ('link', os.readlink(p)) if p.is_symlink()
                else p.read_bytes() if p.is_file() else None
                for p in parent.rglob('*')} if parent.exists() else {}

    def change_source(self, kind, source):
        if kind == 'skill':
            (source / 'nested/data.txt').write_bytes(b'Updated package\n')
        elif kind == 'rule':
            source.write_bytes(b'Updated notes\n')
        else:
            value = hub.read_json(source)
            field = {'mcp': 'description', 'routine': 'description', 'plugin': 'version', 'connection': 'auth_status'}[kind]
            value[field] = 'connected' if kind == 'connection' else 'Updated'
            hub.write_json(source, value)

    def test_unindexed_destination_preserves_original_bytes_and_manifest_for_every_kind(self):
        for kind in self.kinds:
            with self.subTest(kind=kind):
                source, relative, _ = self.item(kind)
                destination = self.local / relative
                if kind == 'skill':
                    destination.mkdir(parents=True)
                    (destination / 'original.md').write_bytes(b'Unfamiliar package\n')
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(b'Unfamiliar original\n')
                before = self.tree()
                self.cli('import-' + kind, source, '--owner', 'maintainer', code=1)
                self.assertEqual(self.tree(), before)

    def test_owner_change_preserves_content_and_manifest_for_every_kind(self):
        for kind in self.kinds:
            with self.subTest(kind=kind):
                source, _, _ = self.item(kind)
                self.run_import(kind, source)
                self.change_source(kind, source)
                if kind == 'routine':
                    value = hub.read_json(source)
                    value['owner'] = 'other-owner'
                    hub.write_json(source, value)
                before = self.tree()
                output = self.cli('import-' + kind, source, '--owner', 'other-owner', code=1)
                self.assertIn('owner', output)
                self.assertEqual(self.tree(), before)

    def test_origin_change_preserves_content_and_manifest_for_every_kind(self):
        for kind in self.kinds:
            with self.subTest(kind=kind):
                source, _, _ = self.item(kind)
                self.run_import(kind, source)
                self.change_source(kind, source)
                before = self.tree()
                output = self.cli('import-' + kind, source, '--owner', 'maintainer', '--origin', 'upstream', code=1)
                self.assertIn('provenance', output)
                self.assertEqual(self.tree(), before)

    def test_modified_destination_is_not_overwritten_for_every_kind(self):
        for kind in self.kinds:
            with self.subTest(kind=kind):
                source, relative, _ = self.item(kind)
                self.run_import(kind, source)
                destination = self.local / relative
                target = destination / 'nested/data.txt' if kind == 'skill' else destination
                target.write_bytes(b'Local changes must survive\n')
                before = self.tree()
                self.cli('import-' + kind, source, '--owner', 'maintainer', code=1)
                self.assertEqual(self.tree(), before)

    def test_same_owner_origin_updates_and_idempotent_reimports_for_every_kind(self):
        for kind in self.kinds:
            with self.subTest(kind=kind):
                source, relative, collection = self.item(kind)
                self.run_import(kind, source)
                before = self.tree()
                self.run_import(kind, source)
                self.assertEqual(self.tree(), before)
                old_hash = self.manifest()[collection][0]['sha256']
                self.change_source(kind, source)
                self.run_import(kind, source)
                entry = self.manifest()[collection][0]
                self.assertNotEqual(entry['sha256'], old_hash)
                self.assertEqual(entry['sha256'], validators.digest(self.local / relative))
                self.assertEqual(entry['owner'], 'maintainer')

    def test_nonordinary_manifest_refused_before_writing_destination(self):
        for shape in ('directory', 'symlink'):
            with self.subTest(shape=shape):
                source, _, _ = self.item('rule')
                manifest = self.local / 'manifest.json'
                manifest.parent.mkdir(parents=True, exist_ok=True)
                if shape == 'directory':
                    manifest.mkdir()
                else:
                    manifest.symlink_to(self.root / 'manifest.json')
                before = self.tree()
                self.cli('import-rule', source, code=1)
                self.assertEqual(self.tree(), before)
                if manifest.is_symlink():
                    manifest.unlink()
                else:
                    manifest.rmdir()

    def test_routine_unindexed_prompt_collision_preserved(self):
        source, relative, _ = self.item('routine')
        destination = self.local / relative
        destination.parent.mkdir(parents=True)
        (destination.parent / 'prompt.md').write_bytes(b'Unfamiliar prompt\n')
        before = self.tree()
        self.cli('import-routine', source, code=1)
        self.assertEqual(self.tree(), before)

    def test_routine_new_prompt_name_cannot_overwrite_unrelated_sibling(self):
        source, relative, _ = self.item('routine')
        self.run_import('routine', source)
        (self.local / relative).parent.joinpath('other.md').write_bytes(b'Unfamiliar sibling\n')
        (source.parent / 'other.md').write_bytes(b'Incoming replacement\n')
        value = hub.read_json(source)
        value['prompt'] = 'other.md'
        hub.write_json(source, value)
        before = self.tree()
        self.cli('import-routine', source, '--owner', 'maintainer', code=1)
        self.assertEqual(self.tree(), before)

    def test_invalid_mcp_path_binding_preserves_definition_manifest_and_store(self):
        source, _, _ = self.item('mcp')
        self.run_import('mcp', source)
        self.change_source('mcp', source)
        before = self.tree()
        with self.assertRaises(ValueError):
            self.run_import('mcp', source, path_bindings={'command': {'portable': 'example', 'resolved': 'relative'}})
        self.assertEqual(self.tree(), before)

    def test_invalid_private_store_configuration_preserves_existing_mcp(self):
        source, _, _ = self.item('mcp')
        self.run_import('mcp', source)
        self.change_source('mcp', source)
        hub.write_json(self.root / 'config.json', {'store_path': None})
        before = self.tree()
        self.cli('import-mcp', source, '--owner', 'maintainer', code=1)
        self.assertEqual(self.tree(), before)

    def test_manifest_publication_failure_restores_every_existing_kind(self):
        original = Path.replace

        def fail_manifest(path, target):
            if path.name == 'pending' and Path(target) == self.local / 'manifest.json':
                original(path, target)
                raise OSError('injected manifest publication failure after replacement')
            return original(path, target)

        for kind in self.kinds:
            with self.subTest(kind=kind):
                source, _, _ = self.item(kind)
                self.run_import(kind, source)
                self.change_source(kind, source)
                if kind == 'routine':
                    (source.parent / 'prompt.md').write_bytes(b'Updated prompt\n')
                before = self.tree()
                with patch.object(Path, 'replace', fail_manifest), self.assertRaises(OSError):
                    self.run_import(kind, source)
                self.assertEqual(self.tree(), before)

    def test_new_import_publication_failure_removes_partial_content_and_created_directories(self):
        source, _, _ = self.item('routine')
        original = Path.replace

        def fail_prompt(path, target):
            if path.name == 'pending' and Path(target).name == 'prompt.md':
                raise OSError('injected prompt publication failure')
            return original(path, target)

        before = self.tree()
        with patch.object(Path, 'replace', fail_prompt), self.assertRaises(OSError):
            self.run_import('routine', source)
        self.assertEqual(self.tree(), before)
        self.assertFalse((self.root / '.hub-store').exists())

    def test_staging_manifest_write_failure_preserves_existing_content(self):
        source, _, _ = self.item('rule')
        self.run_import('rule', source)
        self.change_source('rule', source)
        before = self.tree()
        original = hub.write_json

        def fail_manifest(path, value):
            if path.name == 'manifest.json':
                path.write_bytes(b'Partial staged manifest')
                raise OSError('injected manifest write failure')
            return original(path, value)

        with patch.object(hub, 'write_json', fail_manifest), self.assertRaises(OSError):
            self.run_import('rule', source)
        self.assertEqual(self.tree(), before)

    def test_mcp_binding_and_store_failures_restore_existing_bytes_and_references(self):
        source, _, _ = self.item('mcp')
        private = self.base / 'private-store'
        hub.write_json(self.root / 'config.json', {'store_path': str(private)})
        self.run_import('mcp', source, path_bindings={
            'command': {'portable': 'example', 'resolved': str(self.base / 'original')}})
        store = FileStore(self.root)
        store.put('fixture-unrelated', 'Fixture credential')
        self.change_source('mcp', source)
        original = Path.replace

        def private_snapshot():
            return {p.relative_to(private).as_posix(): p.read_bytes() if p.is_file() else None
                    for p in private.rglob('*')}

        for failure in ('binding-stage', 'binding-publish', 'store-publish', 'manifest-publish'):
            with self.subTest(failure=failure):
                before, private_before = self.tree(), private_snapshot()

                def fail_publish(path, target):
                    target = Path(target)
                    selected = ((failure == 'binding-publish' and target.name == 'source-path-bindings.json') or
                                (failure == 'store-publish' and target.parent == store.secrets) or
                                (failure == 'manifest-publish' and target == self.local / 'manifest.json'))
                    if path.name == 'pending' and selected:
                        original(path, target)
                        raise OSError('injected publication failure')
                    return original(path, target)

                def fail_stage(*args):
                    gateway_render.store_source_path_bindings(*args)
                    raise OSError('injected binding write failure')

                with patch.object(Path, 'replace', fail_publish), \
                        patch.object(hub, 'store_source_path_bindings',
                                     fail_stage if failure == 'binding-stage' else gateway_render.store_source_path_bindings), \
                        self.assertRaises(OSError):
                    self.run_import('mcp', source, path_bindings={
                        'command': {'portable': 'example', 'resolved': str(self.base / 'updated')}})
                self.assertEqual(self.tree(), before)
                self.assertEqual(private_snapshot(), private_before)

    def test_concurrent_destination_or_manifest_change_during_preparation_is_preserved(self):
        source, relative, _ = self.item('rule')
        self.run_import('rule', source)
        self.change_source('rule', source)
        original = hub.publish_import
        for changed in (self.local / relative, self.local / 'manifest.json'):
            with self.subTest(changed=changed.name):
                previous = changed.read_bytes()
                replacement = b'Concurrent notes\n' if changed.suffix == '.md' else previous + b'\n'
                expected = {}

                def change_then_publish(operations, snapshots):
                    changed.write_bytes(replacement)
                    expected.update(self.tree())
                    return original(operations, snapshots)

                try:
                    with patch.object(hub, 'publish_import', change_then_publish), \
                            self.assertRaisesRegex(ValueError, 'changed during preparation'):
                        self.run_import('rule', source)
                    self.assertEqual(self.tree(), expected)
                    self.assertEqual(changed.read_bytes(), replacement)
                finally:
                    changed.write_bytes(previous)

    def test_mcp_reimport_without_explicit_bindings_keeps_existing_path_references(self):
        source, _, _ = self.item('mcp')
        self.run_import('mcp', source, path_bindings={
            'command': {'portable': 'example', 'resolved': str(self.base / 'original')}})
        before = self.tree()
        self.run_import('mcp', source)
        self.assertEqual(self.tree(), before)

    def test_mcp_reimport_with_changed_bound_command_or_argument_removes_stale_bindings(self):
        source, _, _ = self.item('mcp')
        for field in ('command', 'args'):
            with self.subTest(field=field):
                value = hub.read_json(source)
                value.update(command='example', args=['original'])
                hub.write_json(source, value)
                bindings = {'command': {'portable': 'example', 'resolved': str(self.base / 'example')}} if field == 'command' else {
                    'args': {0: {'portable': 'original', 'resolved': str(self.base / 'original')}}}
                self.run_import('mcp', source, path_bindings=bindings)
                old_references = set(FileStore(self.root).names())
                value[field] = 'other' if field == 'command' else ['other']
                hub.write_json(source, value)
                self.run_import('mcp', source)
                self.assertEqual(gateway_render.source_path_bindings(self.root, 'sample'), {})
                self.assertTrue(old_references.isdisjoint(FileStore(self.root).names()))
                settings = {'mcpServers': {'sample': {'command': value['command'], 'args': value['args']}}}
                self.assertEqual(gateway_adapter.launch_settings(settings, self.root), settings)

    def test_missing_or_wrong_type_indexed_destination_refused_without_manifest_changes(self):
        source, relative, _ = self.item('rule')
        self.run_import('rule', source)
        destination = self.local / relative
        destination.unlink()
        for shape in ('missing', 'directory'):
            with self.subTest(shape=shape):
                if shape == 'directory':
                    destination.mkdir()
                before = self.tree()
                self.cli('import-rule', source, '--owner', 'maintainer', code=1)
                self.assertEqual(self.tree(), before)

    def test_inconsistent_indexed_path_or_provenance_refused_without_writes(self):
        source, relative, _ = self.item('rule')
        self.run_import('rule', source)
        manifest = self.local / 'manifest.json'
        original = manifest.read_bytes()
        for field in ('path', 'source_id'):
            with self.subTest(field=field):
                value = json.loads(original)
                entry = value['rules'][0]
                if field == 'path':
                    entry['path'] = validators.LOCAL + '/' + relative
                else:
                    entry['provenance']['source_id'] = 'src-' + '0' * 12
                hub.write_json(manifest, value)
                before = self.tree()
                self.cli('import-rule', source, '--owner', 'maintainer', code=1)
                self.assertEqual(self.tree(), before)

    def test_skill_update_preserves_unindexed_git_bytes_by_refusing(self):
        self.run_import('skill', self.source)
        metadata = self.local / 'skills/sample/.git/HEAD'
        metadata.parent.mkdir()
        metadata.write_bytes(b'Unindexed original metadata\n')
        self.change_source('skill', self.source)
        before = self.tree()
        self.cli('import-skill', self.source, '--owner', 'maintainer', code=1)
        self.assertEqual(self.tree(), before)

    def test_skill_git_metadata_is_not_copied_and_reimports_remain_idempotent(self):
        metadata = self.source / '.git/HEAD'
        metadata.parent.mkdir()
        metadata.write_bytes(b'ref: refs/heads/main\n')
        nested = self.source / 'nested/.git/config'
        nested.parent.mkdir()
        nested.write_bytes(b'Fixture metadata\n')
        self.run_import('skill', self.source)
        installed = self.local / 'skills/sample'
        self.assertFalse((installed / '.git').exists())
        self.assertFalse((installed / 'nested/.git').exists())
        before = self.tree()
        self.run_import('skill', self.source)
        self.run_import('skill', installed)
        self.assertEqual(self.tree(), before)
        self.assertEqual(metadata.read_bytes(), b'ref: refs/heads/main\n')
        self.assertEqual(nested.read_bytes(), b'Fixture metadata\n')

    def test_routine_same_owner_prompt_update_follows_definition_hash_contract(self):
        source, relative, _ = self.item('routine')
        self.run_import('routine', source)
        prompt = (self.local / relative).parent / 'prompt.md'
        prompt.write_bytes(b'Local prompt edit\n')
        (source.parent / 'prompt.md').write_bytes(b'Updated upstream prompt\n')
        old_hash = self.manifest()['routines'][0]['sha256']
        self.run_import('routine', source)
        self.assertEqual(prompt.read_bytes(), b'Updated upstream prompt\n')
        self.assertEqual(self.manifest()['routines'][0]['sha256'], old_hash)

    def test_rollback_failure_retains_recoverable_original_bytes(self):
        source, relative, _ = self.item('rule')
        self.run_import('rule', source)
        destination = self.local / relative
        original_bytes = destination.read_bytes()
        self.change_source('rule', source)
        original = Path.replace

        def fail_publication_and_restore(path, target):
            if ((path.name == 'pending' and Path(target) == self.local / 'manifest.json') or
                    (path.name == 'previous' and Path(target) == destination)):
                raise OSError('injected publication or restoration failure')
            return original(path, target)

        with patch.object(Path, 'replace', fail_publication_and_restore), \
                self.assertRaisesRegex(hub.Refused, 'prior state remains in .import-'):
            self.run_import('rule', source)
        copies = list(destination.parent.glob('.import-*/previous'))
        self.assertEqual([path.read_bytes() for path in copies], [original_bytes])


if __name__ == '__main__':
    unittest.main()
