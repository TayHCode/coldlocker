import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import unittest

import hub
import test_ui


REPO = Path(__file__).resolve().parents[1]


class CountsTests(unittest.TestCase):
    stop = test_ui.UITests.stop
    request = test_ui.UITests.request
    post = test_ui.UITests.post
    setUp = test_ui.UITests.setUp

    def test_status_separates_built_in_examples_from_imports(self):
        shutil.copytree(REPO / 'skills/hub-discover', self.root / 'skills/hub-discover')
        manifest = json.loads((self.root / 'manifest.json').read_text(encoding='utf-8'))
        with contextlib.redirect_stdout(io.StringIO()):
            hub.record(manifest, 'skills', 'hub-discover', self.root / 'skills/hub-discover', 'agentlocker', 'authored', self.root)
        preview = self.post('skills/preview', {'root': str(self.fixtures)})
        self.post('import', {'ticket': preview['ticket'], 'selected': ['skills/sample-skill']})
        status = json.loads(self.request('/api/status')[1])
        self.assertEqual(status['counts']['skills'], 2)
        self.assertEqual(status['inventory']['skills'], {'imported': ['sample-skill'], 'examples': 1})
        for kind, count in status['counts'].items():
            self.assertEqual(count, len(status['inventory'][kind]['imported']) + status['inventory'][kind]['examples'], kind)

    def test_every_screen_uses_the_same_counts(self):
        if not shutil.which('node'):
            self.skipTest('Node is unavailable; JS smoke test skipped')
        result = subprocess.run(['node', str(REPO / 'tests/ui_counts_smoke.cjs')], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
