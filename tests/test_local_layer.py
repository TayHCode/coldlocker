import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from checks.validators import digest
from test_scanner import FIXTURES, ROOT


class LocalLayerTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.scratch/test-root'
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.hub = base / 'hub'
        self.hub.mkdir()
        files = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).split(b'\0')
        for filename in filter(None, files):
            relative = Path(os.fsdecode(filename))
            if (ROOT / relative).is_file():
                (self.hub / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, self.hub / relative)
        self.home = base / 'home'
        shutil.copytree(FIXTURES / 'linux', self.home)
        stub = base / 'bin'
        stub.mkdir()
        (stub / 'crontab').write_text('#!/bin/sh\necho "no crontab for user" >&2\nexit 1\n', encoding='utf-8')
        (stub / 'crontab').chmod(0o755)
        self.env = {**os.environ, 'PATH': f'{stub}{os.pathsep}{os.environ["PATH"]}', 'HOME': str(base / 'empty-home'),
                    'USERPROFILE': str(base / 'empty-home'), 'PYTHONDONTWRITEBYTECODE': '1'}
        (base / 'empty-home').mkdir()
        self.git('init', '-q')
        self.git('add', '--all')
        maintainer = (ROOT / 'config/commit-email-allowlist.txt').read_text(encoding='utf-8').split()[0]
        self.git('-c', 'user.name=t', '-c', f'user.email={maintainer}', 'commit', '-qm', 'base')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.hub, capture_output=True, text=True, check=True, encoding='utf-8').stdout

    def hub_cli(self, *args, code=0):
        result = subprocess.run([sys.executable, 'hub.py', *args], cwd=self.hub, env=self.env, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def endpoint(self, tool):
        requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': tool, 'arguments': {}}}]
        result = subprocess.run([sys.executable, 'mcp_endpoint.py'], cwd=self.hub, env=self.env, capture_output=True, text=True,
                                input=''.join(json.dumps(r) + '\n' for r in requests), encoding='utf-8')
        return json.loads(json.loads(result.stdout)['result']['content'][0]['text'])

    def imported(self):
        self.hub_cli('scan', '--home', self.home, '--import', '--yes')

    def test_scan_import_leaves_git_status_clean_and_endpoint_lists_items(self):
        shipped = {e['name'] for e in self.endpoint('hub_list_skills')}
        self.imported()
        self.assertEqual(self.git('status', '--short'), '')
        names = {e['name'] for e in self.endpoint('hub_list_skills')}
        self.assertGreater(len(names - shipped), 0)
        self.assertTrue(shipped <= names)
        local = json.loads((self.hub / '.hub-store/catalog/manifest.json').read_text(encoding='utf-8'))
        self.assertEqual({e['name'] for e in local['skills']}, names - shipped)
        shipped_manifest = json.loads((self.hub / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual({e['name'] for e in shipped_manifest['skills']}, shipped)

    def test_check_passes_and_does_not_flag_local_layer_as_private(self):
        self.imported()
        output = self.hub_cli('check')
        self.assertEqual(output.count('PASS'), 7, output)
        catalog = self.hub / '.hub-store/catalog'
        document = json.loads((catalog / 'manifest.json').read_text(encoding='utf-8'))
        entry = document['skills'][0]
        (catalog / entry['path'] / 'notes.md').write_text('see ' + os.sep + 'home' + os.sep + 'someone for details\n', encoding='utf-8')
        entry['sha256'] = digest(catalog / entry['path'])
        (catalog / 'manifest.json').write_text(json.dumps(document), encoding='utf-8')
        self.assertEqual(self.hub_cli('check').count('PASS'), 7)

    def test_link_includes_local_skills(self):
        self.imported()
        local = json.loads((self.hub / '.hub-store/catalog/manifest.json').read_text(encoding='utf-8'))['skills']
        target = self.hub.parent / 'target'
        target.mkdir()
        self.hub_cli('link', '--target', target)
        for entry in local:
            self.assertTrue((target / '.claude/skills' / entry['name'] / 'SKILL.md').is_file())

    def test_import_refuses_shipped_name(self):
        source = self.hub.parent / 'dup'
        shutil.copytree(self.hub / 'skills/hub-discover', source)
        self.hub_cli('import-skill', source, code=1)
        self.assertEqual(self.git('status', '--short'), '')
        self.assertFalse((self.hub / '.hub-store/catalog/manifest.json').exists())

    def test_local_layer_tamper_fails_check(self):
        self.imported()
        local = next((self.hub / '.hub-store/catalog/skills').iterdir())
        (local / 'SKILL.md').write_text((local / 'SKILL.md').read_text(encoding='utf-8') + '\nextra\n', encoding='utf-8')
        self.assertIn('FAIL', self.hub_cli('check', code=1))


if __name__ == '__main__':
    unittest.main()
