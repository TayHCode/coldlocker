#!/usr/bin/env python3
import argparse
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

SECRET_ENV = re.compile(r"(TOKEN|SECRET|PASSW|CREDENTIAL|API_?KEY|PRIVATE|AUTH|^GH_|^GITHUB_)", re.I)
KEPT_ENV = re.compile(r"^GIT_(AUTHOR|COMMITTER)_")
MARKER = re.compile(r"^FLEET_(TESTS|CHECKS)=(\d+)$")
VERIFICATION_PATH = re.compile(
    r"(^|/)(package\.json$|pytest\.ini$|pyproject\.toml$|setup\.cfg$|tox\.ini$|\.fleet/|\.coldlocker/|\.github/workflows/|tests?/|__tests__/|conftest\.py$|test_[^/]*$|[^/]*_test\.[^/]*$|[^/]*\.(test|spec)\.[^/]*$)")
SKIPPED = re.compile(r"(?m)^(?:OK|FAILED) \(.*?\bskipped=(\d+)")
TEST_COUNTS = (
    (re.compile(r"(?m)^Ran (\d+) tests?\b"), "sum"),
    (re.compile(r"(?m)^(?:=+ )?(?:.*, )?(\d+) passed\b"), "sum"),
    (re.compile(r"(?m)^# pass (\d+)\s*$"), "sum"),
    (re.compile(r"(?m)^Tests:\s+.*?\b(\d+) passed"), "sum"),
    (re.compile(r"(?m)^test result: ok\. (\d+) passed"), "sum"),
    (re.compile(r"(?m)^\s*--- PASS: "), "count"),
)
APP_API = "https://api.github.com"


def run(cmd, cwd=None, env=None, check=True):
    done = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and done.returncode:
        raise SystemExit(f"FAIL: {' '.join(cmd[:3])} exited {done.returncode}: {done.stderr.strip()[-300:]}")
    return done


def git(repo, *args, check=True):
    return run(["git", "-C", str(repo), *args], check=check).stdout.strip()


def scrub_env(extra=None):
    env = {k: v for k, v in os.environ.items() if KEPT_ENV.match(k) or not SECRET_ENV.search(k)}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.update(extra or {})
    return env


def public_url(raw):
    return re.sub(r"^(https?://)[^/@]+@", r"\1", raw.strip())


def owner_repo(url):
    found = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    return (found.group(1), found.group(2)) if found else None


def default_base(repo):
    head = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False)
    return head.split("/", 1)[1] if "/" in head else "main"


def pr_info(repo, pr):
    if not shutil.which("gh"):
        return {}
    args = ["gh", "pr", "view"] + ([str(pr)] if pr else []) + ["--json", "baseRefName,headRefOid,url"]
    done = run(args, cwd=repo, check=False)
    try:
        return json.loads(done.stdout) if done.returncode == 0 else {}
    except ValueError:
        return {}


def base_remote(repo, pr_url):
    if not pr_url:
        return "origin"
    wanted = owner_repo(re.sub(r"/pull/\d+.*$", "", pr_url))
    for name in git(repo, "remote").split():
        found = owner_repo(git(repo, "config", "--get", f"remote.{name}.url", check=False))
        if wanted and found and tuple(x.lower() for x in found) == tuple(x.lower() for x in wanted):
            return name
    raise SystemExit(f"FAIL: no remote points at {'/'.join(wanted) if wanted else pr_url}; add one so the pull request base can be fetched")


def snapshot(repo, work, head, dirty_overlay):
    run(["git", "clone", "--quiet", "--no-checkout", str(repo), str(work)])
    git(work, "checkout", "--quiet", "--detach", head)
    if dirty_overlay:
        names = run(["git", "-C", str(repo), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]).stdout
        staged = run(["git", "-C", str(repo), "ls-files", "-s", "-z"]).stdout
        gitlinks = {entry.split("\t", 1)[1] for entry in staged.split("\0") if entry.startswith("160000 ")}
        removed = run(["git", "-C", str(repo), "diff", "--cached", "--no-renames", "--diff-filter=D", "--name-only", "-z", head]).stdout
        for name in filter(None, removed.split("\0")):
            if Path(work, name).is_file() or Path(work, name).is_symlink():
                Path(work, name).unlink()
        for name in filter(None, names.split("\0")):
            src, dest = Path(repo, name), Path(work, name)
            if name in gitlinks:
                if git(repo, "status", "--porcelain", "--", name):
                    raise SystemExit(f"FAIL: submodule {name} has uncommitted or unrecorded changes; commit them in the submodule and the parent first")
                continue
            if not src.is_symlink() and not src.is_file():
                dest.unlink(missing_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.unlink(missing_ok=True)
            if src.is_symlink():
                os.symlink(os.readlink(src), dest)
            else:
                shutil.copy2(src, dest)
        git(work, "add", "-A")
        ident = ["-c", "user.name=verify-loop", "-c", "user.email=verify-loop@localhost"]
        if git(work, "status", "--porcelain"):
            git(work, *ident, "commit", "--quiet", "-m", "verify-loop snapshot")


def merge_base(work, repo, base, remote="origin"):
    tracking = f"refs/remotes/{remote}/{base}"
    fetched = run(["git", "-C", str(repo), "fetch", "--quiet", remote, f"+refs/heads/{base}:{tracking}"], check=False)
    if fetched.returncode:
        raise SystemExit(f"FAIL: could not fetch {remote}/{base}; fix the connection and rerun")
    tip = git(repo, "rev-parse", "--verify", "--quiet", tracking, check=False)
    if not tip:
        raise SystemExit(f"FAIL: {remote}/{base} not found; fetch it or pass --base")
    pinned = f"refs/verify-loop/{uuid.uuid4().hex}"
    git(repo, "update-ref", pinned, tip)
    try:
        git(work, "fetch", "--quiet", "origin", f"{pinned}:refs/remotes/upstream/{base}")
    finally:
        git(repo, "update-ref", "-d", pinned, check=False)
    ident = ["-c", "user.name=verify-loop", "-c", "user.email=verify-loop@localhost"]
    head = git(work, "rev-parse", "HEAD")
    git(work, "checkout", "--quiet", "--detach", f"refs/remotes/upstream/{base}")
    merged = run(["git", "-C", str(work), *ident, "merge", "--no-edit", "--quiet", head], check=False)
    if merged.returncode:
        raise SystemExit(f"FAIL: merge conflict with {base}; rebase on {base} and rerun")
    return tip


def init_submodules(work):
    if (Path(work) / ".gitmodules").is_file():
        submodules = run(["git", "-C", str(work), "submodule", "update", "--init", "--recursive", "--quiet"], check=False)
        if submodules.returncode:
            raise SystemExit("FAIL: could not initialize submodules: " + submodules.stderr.strip()[-300:])


def changed_verification(work, base_sha):
    status = git(work, "diff", "--name-status", f"{base_sha}...HEAD", check=False)
    hits = []
    for line in status.splitlines():
        flag, *paths = line.split("\t")
        for path in paths:
            if VERIFICATION_PATH.search(path):
                hits.append(("deleted " if flag.startswith("D") else "") + path)
    return hits


PYTEST_STYLE = re.compile(r"(?m)^(?:async )?def test_|^(?:import pytest|from pytest\b)|^class Test\w*\s*(?:\((?![^)]*TestCase)[^)]*\))?\s*:")


SKIPPED_DIRS = {"node_modules", "venv", "env", "site-packages", "__pycache__", "build", "dist"}


def python_files(work, pattern):
    found = []
    for directory, names, files in os.walk(work):
        names[:] = [n for n in names if not n.startswith(".") and n not in SKIPPED_DIRS]
        found += [Path(directory, f) for f in files if fnmatch.fnmatch(f, pattern)]
    return sorted(found)


def uses_pytest(work, tests):
    if (work / "pytest.ini").is_file() or python_files(work, "conftest.py"):
        return True
    for path in tests:
        if PYTEST_STYLE.search(path.read_text(encoding="utf-8", errors="replace")):
            return True
    for name, marker in (("pyproject.toml", "[tool.pytest"), ("setup.cfg", "[tool:pytest]"), ("tox.ini", "[pytest]")):
        path = work / name
        if path.is_file() and marker in path.read_text(encoding="utf-8", errors="replace"):
            return True
    return False


def unittest_steps(work, tests):
    discover = [sys.executable, "-m", "unittest", "discover", "-p", "test_*.py"]
    roots = []

    def covered(root, directory):
        if root != directory and root not in directory.parents:
            return False
        parts = directory.relative_to(root).parts
        return all((root.joinpath(*parts[:i + 1]) / "__init__.py").is_file() for i in range(len(parts)))

    for directory in sorted({path.parent for path in tests}):
        if not any(covered(root, directory) for root in roots):
            roots.append(directory)
    return [discover if root == work else [*discover, "-s", root.relative_to(work).as_posix()] for root in roots]


def detect_command(work):
    work = Path(work)
    on_windows = os.name == "nt"
    if on_windows and (work / ".fleet/verify.ps1").is_file():
        shell = shutil.which("powershell") or "powershell"
        return "recipe", [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ".fleet/verify.ps1"]
    if (work / ".fleet/verify.sh").is_file():
        bash = shutil.which("bash") or "bash"
        git_exe = shutil.which("git")
        if on_windows and git_exe:
            candidate = Path(git_exe).resolve().parents[1] / "bin" / "bash.exe"
            bash = str(candidate) if candidate.is_file() else bash
        return "recipe", [bash, ".fleet/verify.sh"]
    package = work / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {})
        except ValueError:
            scripts = {}
        if scripts.get("test"):
            lock = (work / "package-lock.json").is_file()
            npm = shutil.which("npm") or "npm"
            return "node", [[npm, "ci" if lock else "install"], [npm, "test"]]
    if (work / "go.mod").is_file():
        return "go", [["go", "test", "-v", "./..."]]
    if (work / "Cargo.toml").is_file():
        return "rust", [["cargo", "test"]]
    tests = python_files(work, "test_*.py")
    if tests:
        if uses_pytest(work, tests):
            return "python", [[sys.executable, "-m", "pytest", "-q"]]
        return "python", unittest_steps(work, tests)
    return None, None


def count_tests(output):
    lines = output.strip().splitlines()
    marker = MARKER.match(lines[-1].strip()) if lines else None
    if marker:
        return int(marker.group(2)), marker.group(1)
    total = 0
    for pattern, mode in TEST_COUNTS:
        found = pattern.findall(output)
        total += len(found) if mode == "count" else sum(int(n) for n in found)
        if pattern is TEST_COUNTS[0][0]:
            total -= sum(int(n) for n in SKIPPED.findall(output))
        if total:
            break
    return total, "TESTS"


def count_run(kind, texts):
    if kind == "python":
        return sum(count_tests(text)[0] for text in texts), "TESTS"
    return count_tests(texts[-1] if texts else "")


def execute(work, command, env, log):
    steps = command if command and isinstance(command[0], list) else [command]
    code = 0
    output = []
    texts = []
    for step in steps:
        header = f"$ {' '.join(step)}\n"
        log.write(header)
        output.append(header)
        try:
            done = subprocess.run(step, cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8", errors="replace")
            text = done.stdout
            code = done.returncode
        except OSError as error:
            text, code = f"{error}\n", 127
        log.write(text)
        output.append(text)
        texts.append(text)
        if code:
            break
    return code, "".join(output), texts


def unique_json(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate lesson field")
    return value


def lesson_check(text):
    def require(condition, message):
        if not condition:
            raise ValueError(message)
    require("\n## Verify\n" in text and "\n## Evidence\n" in text, "lesson sections missing")
    verification = text.rsplit("\n## Verify\n", 1)[1].split("\n## Evidence\n", 1)[0]
    metadata = re.split(r"^`{3,}sh\n", verification, maxsplit=1, flags=re.M)[0]
    fields = {}
    for field in ("Status", "Verification", "Scope"):
        values = re.findall(r"^" + field + r": (.+)$", metadata, re.M)
        require(len(values) == 1, "invalid lesson " + field)
        fields[field] = values[0]
    if fields["Status"] == "retired":
        return None
    require(fields["Status"] in ("active", "enforced") and fields["Verification"] == "proven", "lesson is not proven and active")
    require(fields["Scope"] in ("global", "repository-specific"), "invalid lesson scope")
    blocks = re.findall(r"^(`{3,})sh\n(.*?)^\1$", verification, re.M | re.S)
    require(len(blocks) == 1 and blocks[0][1].strip(), "lesson requires one shell check")
    command = blocks[0][1]
    records = re.findall(r"^## Lesson record\n\n(`{3,})json\n(.*?)^\1$", text, re.M | re.S)
    require(len(records) == 1, "lesson requires one four-part proof record")
    record = json.loads(records[0][1], object_pairs_hook=unique_json)
    require(isinstance(record, dict) and set(record) == {"schema_version", "lesson", "source_evidence", "what_went_wrong", "fix", "rule", "replay", "gates"}, "invalid lesson record schema")
    require(type(record["schema_version"]) is int and record["schema_version"] == 1, "invalid lesson record version")
    for key in ("what_went_wrong", "fix", "rule"):
        require(isinstance(record[key], str) and record[key].strip(), "missing lesson " + key)
    require("\n\n" + record["rule"] + "\n\n" in text, "lesson rule differs from record")
    require(re.findall(r"^Lesson: (coldfix:lesson:[^\s:]+:[^\s:]+)$", text, re.M) == [record["lesson"]], "lesson reference mismatch")
    evidence = record["source_evidence"]
    require(isinstance(evidence, list) and evidence and all(isinstance(ref, str) and re.fullmatch(r"coldfix:failure:[0-9a-f]{32}", ref) for ref in evidence), "missing source evidence")
    require(sorted(evidence) == sorted(re.findall(r"^- (coldfix:failure:[0-9a-f]{32})$", text, re.M)), "source evidence mismatch")
    replay = record["replay"]
    require(isinstance(replay, dict) and set(replay) == {"receipt", "expected_failure", "check_sha256", "before", "after"}, "invalid replay proof")
    require(isinstance(replay["expected_failure"], str) and replay["expected_failure"].strip(), "missing expected failure")
    require(re.findall(r"^Replay: (coldfix:replay:[^\s]+)$", text, re.M) == [replay["receipt"]], "replay reference mismatch")
    require(replay["check_sha256"] == hashlib.sha256(command.encode("utf-8")).hexdigest(), "replay check binding mismatch")
    for side, status, exit_code in (("before", "FAIL", 1), ("after", "PASS", 0)):
        row = replay[side]
        require(isinstance(row, dict) and set(row) == {"commit", "tree", "status", "exit"}, "incomplete replay side")
        require(row["status"] == status and type(row["exit"]) is int and row["exit"] == exit_code, "replay must fail before and pass after")
        for field in ("commit", "tree"):
            require(isinstance(row[field], str) and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", row[field]), "missing replay revision")
    require(replay["before"]["commit"] != replay["after"]["commit"] and replay["before"]["tree"] != replay["after"]["tree"], "replay must identify a changed fix")
    require(record["gates"] == {"privacy": "PASS", "release_check": "PASS"}, "privacy and release-check must pass")
    return ["sh", "-c", command]


def lesson_commands(work):
    if (work / ".coldlocker/pending").exists():
        raise ValueError("rule delivery is incomplete; retry delivery")
    directory = Path(work) / ".coldlocker" / "rules"
    if directory.is_symlink() or directory.parent.is_symlink():
        raise ValueError("lesson directory must not be symlinked")
    registry_path = directory.parent / "rules.json"
    if registry_path.is_symlink():
        raise ValueError("rule registry must not be symlinked")
    registry = json.loads(registry_path.read_text(encoding="utf-8"), object_pairs_hook=unique_json) if registry_path.exists() else {}
    if not isinstance(registry, dict):
        raise ValueError("invalid rule registry")
    for name, entry in registry.items():
        if (not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", name) or not isinstance(entry, dict)
                or set(entry) != {"hashes", "lesson", "pending"} or type(entry["lesson"]) is not bool
                or type(entry["pending"]) is not bool
                or not isinstance(entry["hashes"], list) or not entry["hashes"]
                or not all(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) for digest in entry["hashes"])):
            raise ValueError("invalid rule registry entry")
        if entry["pending"]:
            raise ValueError("rule delivery is incomplete; retry delivery")
        if entry["lesson"] and not (directory / (name + ".md")).is_file():
            raise ValueError("delivered lesson is missing")
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise ValueError("lesson directory must be a directory")
    commands = []
    for path in sorted(directory.glob("*.md")):
        if path.is_symlink() or not path.is_file():
            raise ValueError("lesson must be an ordinary file")
        text = path.read_text(encoding="utf-8")
        entry = registry.get(path.stem)
        marked = text.startswith("<!-- coldlocker:lesson -->\n")
        exported = entry is None and re.search(r"^(?:Lesson: coldfix:lesson:|Replay: coldfix:replay:)", text, re.M)
        if marked or exported or entry and entry["lesson"]:
            if not marked:
                raise ValueError("lesson delivery marker is missing")
            if not entry or not entry["lesson"] or hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest() not in entry["hashes"]:
                raise ValueError("delivered lesson differs from its owned copy")
            command = lesson_check(text)
            if command:
                commands.append(command)
    return commands


def lesson_passed(output):
    def unique(pairs):
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError("duplicate check result field")
        return value
    try:
        return json.loads(output, object_pairs_hook=unique) == {"status": "PASS"}
    except (ValueError, TypeError, RecursionError):
        return False


def api(base_url, token, method, path, body=None):
    request = urllib.request.Request(
        base_url + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "coldlocker-verify-loop"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read() or b"null")


def mint_token(token_cmd):
    done = run(shlex.split(token_cmd), check=False)
    token = done.stdout.strip()
    if done.returncode or not token:
        raise SystemExit("FAIL: could not mint a check-posting token with " + shlex.split(token_cmd)[0])
    return token


def required_context(base_url, token, owner, repo, base, app_id):
    quoted = urllib.parse.quote(base, safe="")

    def fetch(path):
        try:
            return api(base_url, token, "GET", f"/repos/{owner}/{repo}/{path}")
        except (urllib.error.URLError, ValueError):
            return None

    contexts = [(c.get("context"), c.get("integration_id")) for r in fetch(f"rules/branches/{quoted}") or []
                if r.get("type") == "required_status_checks"
                for c in r.get("parameters", {}).get("required_status_checks", [])]
    classic = fetch(f"branches/{quoted}/protection/required_status_checks") or {}
    contexts += [(c.get("context"), c.get("app_id")) for c in classic.get("checks", [])]
    ours = [context for context, owner_id in contexts if app_id and context and owner_id == app_id]
    return (ours or ["verify"])[0]


def build_summary(info):
    lines = [f"host: {info['host']}", f"command: {info['command']}",
             f"base: {info['base']} @ {info['base_sha']}", f"head: {info['head']}",
             f"duration: {info['duration']:.1f}s", f"log sha256: {info['log_sha']}",
             f"{info['kind'].lower()}: {info['tests']}", f"log: {info['log_path']}"]
    if info["changed"]:
        lines.append("verification changed: " + ", ".join(info["changed"][:20]))
    lines.append("")
    lines.append("\n".join(info["tail"]))
    return "\n".join(lines)[:60000]


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Run a repo's tests on its committed HEAD merged with the base branch.")
    parser.add_argument("--repo", default=".", help="repository or worktree to verify")
    parser.add_argument("--post", action="store_true", help="post the result as a check run on the head SHA")
    parser.add_argument("--pr", help="pull request number, to read its base branch and head")
    parser.add_argument("--base", help="base branch (default: the pull request's, else the remote default)")
    parser.add_argument("--cmd", help="override the discovered verification command")
    parser.add_argument("--slug", help="owner/repo to post to when origin is not a github.com URL")
    parser.add_argument("--coverage", type=int, metavar="N",
                        help="read-only: list whether the last N merged pull requests have the check on their head")
    parser.add_argument("--check-name", help="check run name (default: the required context, else verify)")
    parser.add_argument("--token-cmd", default=os.environ.get("VERIFY_LOOP_TOKEN_CMD", "cold gh-token"),
                        help="command that prints a check-writing token")
    parser.add_argument("--api-url", default=os.environ.get("VERIFY_LOOP_API_URL", APP_API))
    parser.add_argument("--app-id", type=int, default=int(os.environ.get("VERIFY_LOOP_APP_ID", "0")),
                        help="App integration id used to pick the required context")
    return parser.parse_args(argv)


def gh_json(*args):
    done = run(["gh", *args], check=False)
    if done.returncode:
        raise ValueError(f"gh {args[0]} exited {done.returncode}: {done.stderr.strip()[-300:]}")
    return json.loads(done.stdout)


def coverage(args):
    try:
        if args.coverage < 1:
            raise ValueError("--coverage needs a positive number")
        if not shutil.which("gh"):
            raise ValueError("gh is required to read pull requests")
        slug = tuple(args.slug.split("/", 1)) if args.slug else owner_repo(git(args.repo, "remote", "get-url", "origin", check=False))
        if not slug:
            raise ValueError("origin is not a GitHub remote; pass --slug owner/repo")
        name = args.check_name or "verify"
        prs = gh_json("pr", "list", "--repo", "/".join(slug), "--state", "merged",
                      "--search", "sort:updated-desc", "--limit", str(max(100, 2 * args.coverage)),
                      "--json", "number,headRefOid,mergedAt")
        prs = sorted(prs, key=lambda p: p["mergedAt"], reverse=True)[:args.coverage]
        posted = 0
        for pr in prs:
            runs = gh_json("api", f"repos/{slug[0]}/{slug[1]}/commits/{pr['headRefOid']}/check-runs?check_name={urllib.parse.quote(name)}")
            latest = max(runs["check_runs"], key=lambda r: r["id"], default=None)
            state = "missing" if not latest else "posted " + (latest["conclusion"] if latest["status"] == "completed" else latest["status"])
            posted += bool(latest)
            print(f"#{pr['number']} {pr['headRefOid']} {state}")
        print(f"posted {posted} of {len(prs)}")
        return 0 if posted == len(prs) else 1
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"ERROR: {error}")
        return 2


def main(argv=None):
    try:
        args = parse_args(argv)
        return coverage(args) if args.coverage is not None else run_loop(args)
    except SystemExit as stop:
        if isinstance(stop.code, str):
            print(stop.code)
            return 1
        raise


def run_loop(args):
    repo = Path(git(args.repo, "rev-parse", "--show-toplevel")).resolve()
    if (repo / ".coldlocker/pending").exists() or (repo / ".coldlocker/pending").is_symlink():
        print("FAIL: rule delivery is incomplete; retry delivery")
        return 1
    head = git(repo, "rev-parse", "HEAD")
    dirty = bool(git(repo, "status", "--porcelain"))
    if (args.post or args.pr) and dirty:
        print("FAIL: worktree has uncommitted changes; commit or discard them before --post or --pr")
        return 1
    info = pr_info(repo, args.pr) if (args.pr or args.post) else {}
    if (args.post or args.pr) and not info.get("headRefOid"):
        print("FAIL: could not read the pull request with gh; its head and base are needed")
        return 1
    if args.post or args.pr:
        if info["headRefOid"] != head:
            print(f"FAIL: worktree HEAD {head[:12]} is not the pull request head {info['headRefOid'][:12]}; "
                  "check out the PR head (e.g. its worktree) or pass --repo <worktree>")
            return 1
    if args.post:
        if args.base and args.base != info.get("baseRefName"):
            print(f"FAIL: --base {args.base} is not the pull request base {info.get('baseRefName')}")
            return 1
    base = args.base or info.get("baseRefName") or default_base(repo)
    origin = git(repo, "remote", "get-url", "origin", check=False)
    remote = base_remote(repo, info.get("url"))
    pr_slug = owner_repo(re.sub(r"/pull/\d+.*$", "", info["url"])) if info.get("url") else None
    slug = tuple(args.slug.split("/", 1)) if args.slug else pr_slug or owner_repo(origin)
    check = None
    token = None
    started = time.time()
    scratch = Path(tempfile.mkdtemp(prefix="verify-loop-"))
    log_dir = Path(repo, git(repo, "rev-parse", "--git-common-dir")).resolve() / "verify-loop"
    log_dir.mkdir(parents=True, exist_ok=True)
    work = scratch / "work"
    try:
        snapshot(repo, work, head, dirty_overlay=not args.post)
        base_sha = merge_base(work, repo, base, remote)
        base_url = git(repo, "config", "--get", f"remote.{remote}.url", check=False)
        if base_url:
            git(work, "remote", "set-url", "origin", public_url(base_url))
        init_submodules(work)
        changed = changed_verification(work, base_sha)
        log_path = log_dir / f"{head[:12]}-{base_sha[:12]}-{uuid.uuid4().hex[:8]}.log"
        kind_name, command = detect_command(work)
        if args.cmd:
            kind_name, command = "custom", shlex.split(args.cmd)
        name = args.check_name
        if args.post:
            if not slug:
                print("FAIL: origin is not a GitHub remote; cannot post a check run")
                return 1
            token = mint_token(args.token_cmd)
            name = name or required_context(args.api_url, token, *slug, base, args.app_id)
            check = api(args.api_url, token, "POST", f"/repos/{slug[0]}/{slug[1]}/check-runs",
                        {"name": name, "head_sha": head, "status": "in_progress", "external_id": base_sha})
        env = scrub_env({"FLEET_VERIFY_BASE": base_sha})
        return finish(args, head, base, base_sha, work, kind_name, command, env, log_path, changed, check, token, name, slug, started)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def host_name():
    return os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", "host")


def command_text(command):
    steps = command if command and isinstance(command[0], list) else [command or ["none"]]
    return " && ".join(" ".join(step) for step in steps)


def complete_check(args, token, slug, check, conclusion, title, summary, base_sha):
    api(args.api_url, token, "PATCH", f"/repos/{slug[0]}/{slug[1]}/check-runs/{check['id']}",
        {"status": "completed", "conclusion": conclusion, "external_id": base_sha,
         "output": {"title": title, "summary": summary}})


def finish(args, head, base, base_sha, work, kind_name, command, env, log_path, changed, check, token, name, slug, started):
    try:
        lesson_error = None
        try:
            lessons = lesson_commands(work)
        except (OSError, ValueError) as error:
            lessons, lesson_error = [], "invalid exported lesson: " + str(error) + "\n"
        if lesson_error:
            code, output, kind, tests = 1, lesson_error, "TESTS", 0
            log_path.write_text(output, encoding="utf-8")
        elif command is None:
            code, output, kind, tests = 1, "no test command found; add .fleet/verify.sh or pass --cmd\n", "TESTS", 0
            log_path.write_text(output, encoding="utf-8")
        else:
            code, output, texts = 0, "", []
            with open(log_path, "w", encoding="utf-8") as log:
                try:
                    for lesson in lessons:
                        done = run(lesson, cwd=work, env=env, check=False)
                        lesson_output = "$ " + command_text(lesson) + "\n" + done.stdout + done.stderr
                        log.write(lesson_output)
                        output += lesson_output
                        if done.returncode or not lesson_passed(done.stdout):
                            code = 1
                            message = "lesson check did not return exit 0 and {\"status\":\"PASS\"}\n"
                            output += message
                            log.write(message)
                            break
                except (OSError, ValueError) as error:
                    code = 1
                    message = "invalid exported lesson: " + str(error) + "\n"
                    output += message
                    log.write(message)
                if code == 0:
                    code, project_output, texts = execute(work, command, env, log)
                    output += project_output
            tests, kind = count_run(kind_name, texts)
        reason = ""
        if code == 0 and tests == 0:
            code = 1
            reason = "0 tests ran (exit 0 with no FLEET_TESTS/FLEET_CHECKS marker or recognised test count)\n"
            with open(log_path, "a", encoding="utf-8") as log:
                log.write(reason)
        tail = (output + reason).strip().splitlines()[-50:]
        summary = build_summary({
            "host": host_name(), "command": command_text(command), "base": base, "base_sha": base_sha,
            "head": head, "duration": time.time() - started, "kind": kind, "tests": tests,
            "log_sha": hashlib.sha256(log_path.read_bytes()).hexdigest(), "log_path": str(log_path),
            "changed": changed, "tail": tail})
        passed = code == 0
        conclusion = "success" if passed else "failure"
        if check:
            runs = api(args.api_url, token, "GET",
                       f"/repos/{slug[0]}/{slug[1]}/commits/{head}/check-runs?check_name={urllib.parse.quote(name)}")
            app = (check.get("app") or {}).get("id")
            newer = [r["id"] for r in (runs or {}).get("check_runs", [])
                     if r["id"] > check["id"] and (r.get("app") or {}).get("id") == app]
            if newer and passed:
                conclusion, summary, passed = "neutral", f"superseded by {max(newer)}\n" + summary, False
        label = "SUPERSEDED" if conclusion == "neutral" else "PASS" if passed else "FAIL"
        pr_head = f" pr_head={head[:12]}" if args.pr else ""
        print(f"{label}: {kind.lower()}={tests} head={head[:12]}{pr_head} base={base}@{base_sha[:12]}")
        print(f"log: {log_path}")
        if changed:
            print("verification changed: " + ", ".join(changed[:20]))
        if conclusion == "neutral":
            print(f"a newer run of {name} exists on this commit; assess its result instead of this one")
        elif not passed:
            print("\n".join(tail[-40:]))
        if check:
            complete_check(args, token, slug, check, conclusion, f"{conclusion}: {kind.lower()}={tests}", summary, base_sha)
            print(f"posted: {name} = {conclusion} on {head[:12]}")
        return 0 if passed else 1
    except BaseException as error:
        if check:
            complete_check(args, token, slug, check, "failure", "verify-loop error", f"{type(error).__name__}: {error}", base_sha)
        raise


if __name__ == "__main__":
    sys.exit(main())
