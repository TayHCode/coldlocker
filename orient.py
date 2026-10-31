import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent


def run(*command):
    try:
        done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def open_prs():
    raw = run("gh", "pr", "list", "--state", "open", "--json", "number,title,headRefName,isDraft")
    return json.loads(raw) if raw else None


def next_step(branch, dirty, prs):
    mine = [pr for pr in prs or [] if pr["headRefName"] == branch]
    if branch == "?":
        return "not a git checkout; cd into the repository"
    if dirty:
        return "commit the working changes"
    if mine:
        return f"PR #{mine[0]['number']}: verify and review at the head, then merge (rules/merge-when-proven.md)"
    if branch != "main":
        return "open a pull request for this branch"
    return "start work in .worktrees/<branch>"


def brief():
    branch = run("git", "branch", "--show-current") or "?"
    head = run("git", "rev-parse", "--short", "HEAD") or "?"
    dirty = len((run("git", "status", "--short") or "").splitlines())
    prs = open_prs()
    lines = [f"repo: {ROOT.name}  branch: {branch}  head: {head}  dirty: {dirty}", "rules:"]
    for rule in sorted((ROOT / "rules").glob("*.md")):
        title = rule.read_text(encoding="utf-8").splitlines()[0].lstrip("# ")
        lines.append(f"  {rule.stem}: {title}")
    if prs is None:
        lines.append("open prs: unavailable (gh)")
    else:
        lines.append(f"open prs: {len(prs)}")
        lines += [f"  #{pr['number']} {pr['headRefName']}{' (draft)' if pr['isDraft'] else ''}: {pr['title']}" for pr in prs]
    lines.append("next: " + next_step(branch, dirty, prs))
    return "\n".join(lines)


def register(commands):
    commands.add_parser("orient", help="Print repo state, rules, open PRs and the next step in one compact brief.").set_defaults(
        action=lambda args: print(brief()) or 0)
