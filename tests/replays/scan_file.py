import pathlib
import sys

sys.path.insert(0, "checks")
import scan

path = sys.argv[1]
found = scan.findings(pathlib.Path(path).read_text(encoding="utf-8"), path)
for number, rule in found:
    print(f"{path}:{number}: {rule}")
sys.exit(1 if found else 0)
