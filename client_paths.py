import os
import shutil
import sys
from pathlib import Path


def snap_codex_home(home, env):
    executable = sys.platform == 'linux' and shutil.which('codex', path=env.get('PATH'))
    if not executable or os.path.basename(os.path.realpath(executable)) != 'snap':
        return None
    base = Path(home) / 'snap/codex'
    current = base / 'current'
    if not current.is_symlink() or not current.is_dir():
        return None
    return base / os.readlink(current)


def codex_home(home, env=None):
    env = os.environ if env is None else env
    value = env.get('CODEX_HOME')
    path = Path(value) if value else snap_codex_home(home, env) or Path(home) / '.codex'
    return Path(os.path.abspath(path))
