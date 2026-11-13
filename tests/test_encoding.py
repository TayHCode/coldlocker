import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TEXT = 'Café — 日本語 ✓ Łódź'
SKILL = '---\nname: unicode-sample\ndescription: ' + TEXT + '\n---\n\n# ' + TEXT + '\n'
NOTES = 'notes: ' + TEXT + '\n'
IGNORED = ('.git', '.scratch', '.hub-store', '.worktrees', '__pycache__', 'config.json', 'tests')


def legacy_codec_env(home):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('LC_', 'PYTHON')) and key not in ('LANG', 'LANGUAGE')}
    env.update(LC_ALL='C', PYTHONUTF8='0', PYTHONCOERCECLOCALE='0', HOME=str(home),
               XDG_CONFIG_HOME=str(home / '.config'), XDG_DATA_HOME=str(home / '.local/share'))
    return env


class LegacyCodecTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=self.scratch())
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.home = base / 'home'
        self.checkout = base / 'checkout'
        skill = self.home / '.claude/skills/unicode-sample'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_bytes(SKILL.encode('utf-8'))
        (skill / 'notes.md').write_bytes(NOTES.encode('utf-8'))
        shutil.copytree(ROOT, self.checkout, ignore=shutil.ignore_patterns(*IGNORED))
        subprocess.run(['git', 'init', '-q', str(self.checkout)], check=True)
        self.env = legacy_codec_env(self.home)

    @staticmethod
    def scratch():
        path = ROOT / '.scratch/test-root'
        path.mkdir(parents=True, exist_ok=True)
        return path

    def run_python(self, *args, data=b''):
        result = subprocess.run([sys.executable, *args], cwd=self.checkout, env=self.env,
                                input=data, capture_output=True, timeout=60)
        return result.returncode, result.stdout.decode('utf-8'), result.stderr.decode('utf-8', 'replace')

    def test_interpreter_uses_a_non_utf8_default_encoding(self):
        code, output, _ = self.run_python('-c', 'import locale; print(locale.getpreferredencoding(False).lower())')
        self.assertEqual(code, 0)
        self.assertNotIn('utf', output)

    def test_scan_packages_non_ascii_skill_and_import_keeps_it_intact(self):
        code, output, error = self.run_python('hub.py', 'scan', '--home', str(self.home), '--json')
        self.assertEqual(code, 0, error)
        items = [i for i in json.loads(output)['items'] if i['kind'] == 'skill' and i['name'] == 'unicode-sample']
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]['importable'], items[0])
        code, output, error = self.run_python('hub.py', 'scan', '--home', str(self.home), '--import', '--yes')
        self.assertEqual(code, 0, output + error)
        imported = self.checkout / '.hub-store/catalog/skills/unicode-sample'
        self.assertEqual((imported / 'SKILL.md').read_bytes(), SKILL.encode('utf-8'))

    def test_endpoint_returns_imported_non_ascii_skill_intact(self):
        code, output, error = self.run_python('hub.py', 'scan', '--home', str(self.home), '--import', '--yes')
        self.assertEqual(code, 0, output + error)
        requests = [{'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {'clientInfo': {'name': 'Caf\u00e9'}}},
                    {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                     'params': {'name': 'hub_get_item', 'arguments': {'kind': 'skill', 'name': 'unicode-sample'}}}]
        data = ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in requests).encode('utf-8')
        code, output, error = self.run_python('mcp_endpoint.py', data=data)
        self.assertEqual(code, 0, error)
        responses = [json.loads(line) for line in output.splitlines()]
        fetched = json.loads(responses[1]['result']['content'][0]['text'])
        self.assertEqual(fetched['files'], {'.hub-store/catalog/skills/unicode-sample/SKILL.md': SKILL,
                                            '.hub-store/catalog/skills/unicode-sample/notes.md': NOTES})
        self.assertEqual(fetched['omitted'], [])
        first = json.loads((self.checkout / '.hub-store/calls-first.json').read_text(encoding='utf-8'))
        self.assertIn('Caf\u00e9', first)


if __name__ == '__main__':
    unittest.main()
