import errno
import os
import sys
import time

try:
    import fcntl
except ImportError:
    fcntl = None


def product_env(name, default=None, env=None):
    env = os.environ if env is None else env
    legacy = name.replace('COLDLOCKER_', 'AGENTLOCKER_', 1)
    return env.get(name, env.get(legacy, default))


def lock(stream, exclusive=True, wait=True):
    if fcntl:
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(stream, operation if wait else operation | fcntl.LOCK_NB)
        return
    import msvcrt
    stream.seek(0)
    while True:
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EDEADLK):
                raise
            if not wait:
                raise BlockingIOError(error.errno, error.strerror) from None
            time.sleep(0.05)


def restrict(stream, path):
    if hasattr(os, 'fchmod'):
        os.fchmod(stream.fileno(), 0o600)
    else:
        os.chmod(path, 0o600)


def python_command():
    if sys.platform.startswith('win'):
        return f'"{sys.executable}"' if ' ' in sys.executable else sys.executable
    return 'python3'
