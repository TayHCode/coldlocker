import contextlib
import io
import os
from pathlib import Path
from unittest.mock import patch


@contextlib.contextmanager
def windows_text_writes():
    write_text = Path.write_text
    fdopen = os.fdopen

    def path_write_text(self, data, encoding=None, errors=None, newline=None):
        return write_text(self, data, encoding=encoding, errors=errors, newline='\r\n' if newline is None else newline)

    def text_fdopen(descriptor, mode='r', buffering=-1, encoding=None, errors=None, newline=None, closefd=True):
        if 'b' in mode or 'w' not in mode and 'a' not in mode or newline is not None:
            return fdopen(descriptor, mode, buffering, encoding, errors, newline, closefd)
        stream = fdopen(descriptor, mode + 'b', buffering, closefd=closefd)
        return io.TextIOWrapper(stream, encoding=encoding, errors=errors, newline='\r\n')

    with patch.object(Path, 'write_text', path_write_text), patch('os.fdopen', text_fdopen):
        yield
