import json
from pathlib import Path
import shutil
import tempfile
import unittest

import connect
import scanner
from store import FileStore
from test_scanner import ROOT

SERVER = {'command': 'uvx', 'args': ['tool==1.0.0']}


class OwnConnectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.home = base / 'home'
        self.root = base / 'locker'
        self.root.mkdir()
        for name in ('manifest.json', 'LICENSE', '.gitignore'):
            shutil.copyfile(ROOT / name, self.root / name)
        for folder in ('skills', 'rules', 'routines', 'mcp'):
            shutil.copytree(ROOT / folder, self.root / folder)
        own = connect.entries(self.root, 'claude')
        own['agentlocker-gateway'] = {'type': 'http', 'url': 'http://127.0.0.1:43871/mcp'}
        self.home.mkdir()
        (self.home / '.claude.json').write_text(json.dumps({'mcpServers': {**own, 'my-server': SERVER}}))

    def inventory(self):
        return scanner.Inventory(self.home, (), 'linux', {}, lambda command: '', self.root)

    def test_scan_does_not_offer_the_connection_connect_wrote(self):
        report = self.inventory().discover()
        names = {i['name'] for i in report['items'] if i['kind'] in ('mcp_server', 'connection')}
        self.assertEqual(names, {'my-server'})

    def test_import_after_connect_leaves_the_hub_out_of_its_own_catalog(self):
        report = self.inventory().discover()
        selected = [i['id'] for i in report['items'] if i['importable']]
        result = scanner.apply(self.root, report, selected, FileStore(self.root), self.home, platform='linux', env={}, runner=lambda command: '')
        self.assertEqual(result['imported'], [i['id'] for i in report['items'] if i['name'] == 'my-server' and i['importable']])
        stored = {p.stem for p in (self.root / '.hub-store/catalog').rglob('*.json') if p.stem.startswith('agentlocker')}
        self.assertEqual(stored, set())


if __name__ == '__main__':
    unittest.main()
