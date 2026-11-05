import stat
import sys


ENFORCED = sys.platform != 'win32'
GATEWAY_PLATFORM = 'linux' if sys.platform == 'win32' else sys.platform


def check(case, path, expected):
    if ENFORCED:
        case.assertEqual(stat.S_IMODE(path.stat().st_mode), expected)
