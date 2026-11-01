import runpy
import subprocess
import sys

commands = []


def record(command, **_):
    commands.append(command)
    return subprocess.CompletedProcess(command, 0)


subprocess.run = record
runpy.run_path("tools/verify_export.py")["main"]()
scheduled = [c for c in commands if "test_e2e_dogfood.py" in c]
print(f"dogfood commands scheduled without .git: {len(scheduled)}")
sys.exit(1 if scheduled else 0)
