import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch


if sys.argv[1] == 'api':
    if os.environ.get('FAKE_GH') == 'fail':
        sys.exit(1)
    sha = sys.argv[2].split('/commits/')[1].split('/')[0]
    print(json.dumps({'check_runs': json.loads(os.environ.get('FAKE_GH_RUNS', '{}')).get(sha, [])}))
elif sys.argv[1:3] == ['pr', 'list']:
    if os.environ.get('FAKE_GH') == 'fail':
        sys.exit(1)
    prs = json.loads(os.environ.get('FAKE_GH_PRS', '[]'))
    print(json.dumps(prs[:int(sys.argv[sys.argv.index('--limit') + 1])]))
elif sys.argv[1] == 'pr':
    if os.environ.get('FAKE_GH') == 'fail':
        sys.exit(1)
    head = os.environ.get('FAKE_GH_HEAD') or subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], text=True).strip()
    print(json.dumps({'baseRefName': os.environ.get('FAKE_GH_BASE', 'main'),
                      'headRefOid': head, 'url': os.environ.get('FAKE_GH_URL', '')}))
else:
    runner = sys.argv.pop(1)
    spec = importlib.util.spec_from_file_location('verify_loop', runner)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    real_run, real_which = subprocess.run, shutil.which

    def run(command, *args, **kwargs):
        if command[0] == 'gh':
            command = [sys.executable, '-B', str(Path(__file__).resolve()), *command[1:]]
        return real_run(command, *args, **kwargs)

    with patch('subprocess.run', side_effect=run), patch('shutil.which', side_effect=lambda name: __file__ if name == 'gh' else real_which(name)):
        sys.exit(module.main())
