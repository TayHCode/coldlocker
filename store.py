import argparse
import getpass
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from portable import restrict


SECRET_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}\Z")
ROOT = Path(__file__).resolve().parent


def check_name(name):
    if not isinstance(name, str) or not SECRET_NAME.fullmatch(name):
        raise ValueError("invalid secret name")
    return name


def reference_name(ref):
    if not isinstance(ref, str) or not ref.startswith("secret://"):
        raise ValueError("invalid secret reference")
    return check_name(ref[9:])


def trusted_base(path):
    bases = [Path(os.path.abspath(base)) for base in (Path.home(), tempfile.gettempdir())]
    inside = [base for base in bases if path.is_relative_to(base)]
    return max(inside, key=lambda base: len(base.parts), default=Path(path.anchor))


def ordinary_path(path, base=None):
    path = Path(os.path.abspath(path))
    base = trusted_base(path) if base is None else Path(os.path.abspath(base))
    depth = len(path.relative_to(base).parts)
    for part in (path, *path.parents)[:max(depth, 1)]:
        if part.is_symlink():
            raise ValueError("store symlinks are forbidden")
    return path


class FileStore:
    def __init__(self, root=ROOT):
        root = Path(root).resolve()
        config = root / "config.json"
        ordinary_path(config)
        settings = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
        location = settings.get("store_path", ".hub-store")
        if not isinstance(location, str) or not location:
            raise ValueError("invalid store configuration")
        self.path = ordinary_path(Path(os.path.abspath(root / location)))
        self.secrets = self.path / "secrets"
        ordinary_path(self.secrets)

    def _directory(self):
        ordinary_path(self.secrets)
        for directory in (self.path, self.secrets):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.chmod(0o700)

    def _file(self, name):
        return ordinary_path(self.secrets / check_name(name))

    def put(self, name, value):
        destination = self._file(name)
        if not isinstance(value, str) or not value:
            raise ValueError("secret must be nonempty text")
        self._directory()
        descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=self.secrets)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                restrict(stream, temporary)
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def has(self, name):
        return self._file(name).is_file()

    def names(self):
        ordinary_path(self.secrets)
        if not self.secrets.exists():
            return []
        return sorted(p.name for p in self.secrets.iterdir()
                      if SECRET_NAME.fullmatch(p.name) and not p.is_symlink() and p.is_file())

    def delete(self, name):
        self._file(name).unlink(missing_ok=True)

    def resolve(self, ref):
        return self._file(reference_name(ref)).read_text(encoding="utf-8")

    def redact(self, text):
        values = sorted((self.resolve("secret://" + name) for name in self.names()),
                        key=len, reverse=True)
        for value in values:
            if value:
                text = text.replace(value, "[REDACTED]")
        return text


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "invalid command arguments\n")


def main():
    parser = QuietParser(description="Manage local secrets without printing values")
    parser.add_argument("command", choices=("set", "has", "list", "path", "delete"))
    parser.add_argument("name", nargs="?")
    args = parser.parse_args()
    try:
        needs_name = args.command in ("set", "has", "delete")
        if needs_name != (args.name is not None):
            raise ValueError("invalid arguments")
        store = FileStore()
        if args.command == "set":
            check_name(args.name)
            value = getpass.getpass("Secret: ") if sys.stdin.isatty() else sys.stdin.read().rstrip("\r\n")
            store.put(args.name, value)
        elif args.command == "has":
            present = store.has(args.name)
            print("yes" if present else "no")
            return 0 if present else 1
        elif args.command == "list":
            print("\n".join(store.names()))
        elif args.command == "path":
            print(store.path)
        else:
            store.delete(args.name)
        return 0
    except (OSError, ValueError, TypeError, AttributeError, EOFError, KeyboardInterrupt):
        print("store operation failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
