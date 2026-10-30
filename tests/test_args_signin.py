import contextlib
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from checks import validators
from hub import record, write_json
import hub_ui
from store import FileStore
from ui_auth import Authentication

ROOT = Path(__file__).resolve().parents[1]
FLAGGED = 'scan-' + 'a' * 32
INLINE = 'scan-' + 'b' * 32
BARE = 'scan-' + 'c' * 32
VALUE = 'typed-fixture-value'


class ArgsSignInTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'locker'
        self.root.mkdir()
        self.local = self.root / '.hub-store/catalog'
        for name in ('manifest.json', 'LICENSE', '.gitignore'):
            shutil.copyfile(ROOT / name, self.root / name)
        for folder in ('skills', 'rules', 'routines', 'mcp', 'config'):
            shutil.copytree(ROOT / folder, self.root / folder)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.store = FileStore(self.root)
        self.authentication = Authentication(self.root, self.store)
        servers = {'flagsrv': ['-y', 'some-mcp@1.2.3', '--api-key', 'secret://' + FLAGGED],
                   'inlinesrv': ['-y', 'some-mcp@1.2.3', '--token=secret://' + INLINE],
                   'baresrv': ['serve', 'secret://' + BARE]}
        manifest = validators.empty_manifest()
        for name, args in servers.items():
            destination = self.local / 'mcp/servers' / (name + '.json')
            destination.parent.mkdir(parents=True, exist_ok=True)
            write_json(destination, {'name': name, 'description': name, 'transport': 'stdio', 'command': 'npx', 'args': args})
            with contextlib.redirect_stdout(io.StringIO()):
                record(manifest, 'mcp_servers', name, destination, 'agentlocker', 'scan-claude', self.local)

    def row(self, name):
        return next(row for row in hub_ui.auth_status(self.root, self.store) if row['name'] == name)

    def test_references_are_named_by_flag_or_position(self):
        refs = lambda name: self.authentication.stdio_references(self.authentication.definition(name))
        self.assertEqual(refs('flagsrv'), {'--api-key': FLAGGED})
        self.assertEqual(refs('inlinesrv'), {'--token': INLINE})
        self.assertEqual(refs('baresrv'), {'arg 1': BARE})

    def test_missing_reference_needs_sign_in_and_stored_one_is_satisfied(self):
        for name, key, ref in (('flagsrv', '--api-key', FLAGGED), ('inlinesrv', '--token', INLINE), ('baresrv', 'arg 1', BARE)):
            row = self.row(name)
            self.assertEqual((row['state'], row['secrets']), ('needs_signin', [key]))
            self.store.put(ref, VALUE)
            row = self.row(name)
            self.assertEqual((row['state'], row['secrets']), ('saved', [key]))
            self.assertNotIn(VALUE, str(row))

    def test_saving_writes_the_referenced_key_without_returning_the_value(self):
        for name, key, ref in (('flagsrv', '--api-key', FLAGGED), ('inlinesrv', '--token', INLINE), ('baresrv', 'arg 1', BARE)):
            result = self.authentication.secret(name, key, VALUE)
            self.assertEqual(result, {'status': 'saved'})
            self.assertEqual(self.store.resolve('secret://' + ref), VALUE)
            self.assertEqual(self.row(name)['state'], 'saved')

    def test_unknown_argument_name_is_refused(self):
        with self.assertRaises(ValueError):
            self.authentication.secret('flagsrv', '--other', VALUE)


if __name__ == '__main__':
    unittest.main()
