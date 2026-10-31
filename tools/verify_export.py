"""Verify the documented public-export scope; private maintainer tests need config/."""
from pathlib import Path
import subprocess
import shutil
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    if shutil.which('git') is None:
        sys.exit('INCOMPLETE: install Git and put it on PATH; safety tests create temporary repositories.')
    try:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'target'
            target.touch()
            (Path(folder) / 'link').symlink_to(target)
    except OSError:
        sys.exit('INCOMPLETE: tests require symlink permission. On Windows enable Developer Mode or use an account with symbolic-link privilege.')
    commands = [[sys.executable, '-B', 'hub.py', 'doctor']]
    for pattern in ('test_safety.py', 'test_endpoint.py', 'test_catalog_search.py', 'test_runtime_proofs.py'):
        commands.append([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', pattern, '-v'])
    commands.append([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests',
                     '-p', 'test_public_export.py', '-k', 'test_export_contains_existing'])
    for command in commands:
        subprocess.run(command, cwd=ROOT, check=True)
    print('PASS: public export health, safety, MCP endpoint, catalog experiment and runtime proofs', flush=True)


if __name__ == '__main__':
    main()
