import os
from pathlib import Path


def codex_home(home, env=None):
    env = os.environ if env is None else env
    value = env.get('CODEX_HOME')
    path = Path(value) if value else Path(home) / '.codex'
    return Path(os.path.abspath(path))
