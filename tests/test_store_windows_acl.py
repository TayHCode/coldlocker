import getpass
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from store import FileStore

OWNER_ONLY = {"nt authority\\system", "builtin\\administrators", "owner rights"}


def grants(path):
    output = subprocess.run(["icacls", str(path)], capture_output=True, text=True, check=True).stdout
    entries = []
    for line in output.replace(str(path), "").splitlines():
        if ":(" in line:
            grantee, _, flags = line.strip().partition(":(")
            entries.append((grantee.lower(), "(" + flags))
    return entries


def inherited(flags):
    return "(I)" in flags


def unexpected_grantees(entries):
    user = getpass.getuser().lower()
    return [name for name, _ in entries if name not in OWNER_ONLY and name.rpartition("\\")[2] != user]


@unittest.skipUnless(sys.platform == "win32", "icacls and Windows ACL inheritance exist only on Windows")
class WindowsStoreAclTests(unittest.TestCase):
    def test_store_is_owner_only_and_secret_inherits_it(self):
        with tempfile.TemporaryDirectory() as root:
            store = FileStore(Path(root))
            store.put("sample-token", "value")
            for directory in (store.path, store.secrets):
                entries = grants(directory)
                self.assertTrue(entries)
                self.assertFalse([e for e in entries if inherited(e[1])], entries)
                self.assertEqual(unexpected_grantees(entries), [])
            entries = grants(store.secrets / "sample-token")
            self.assertTrue(entries)
            self.assertTrue(all(inherited(flags) for _, flags in entries), entries)
            self.assertEqual(unexpected_grantees(entries), [])
