#!/usr/bin/env python3
"""Emit Coldfix ingest events for Codex review findings that a later head fixed, each with before/after replay proof."""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = "chatgpt-codex-connector[bot]"
MARKER = "<!-- codex-code-review-finding:v1 -->"
HEADLINE = re.compile(r"\*\*<sub><sub>!\[(P[12]) Badge\]\([^)]*\)</sub></sub>\s+(.+?)\*\*\s*\n(.*)", re.S)


def parse_finding(comment):
    body = comment.get("body", "")
    match = HEADLINE.match(body)
    if comment.get("user", {}).get("login") != BOT or MARKER not in body or not match:
        return None
    severity, title, rest = match.groups()
    detail = rest.split("Useful? React", 1)[0].strip()
    return dict(id=comment["id"], path=comment["path"], severity=severity, title=title, detail=detail,
                bad_head=comment["original_commit_id"], url=comment["html_url"], occurred=comment["created_at"])


def extract(repo_path, head, destination):
    archive = subprocess.run(["git", "-C", str(repo_path), "archive", head], capture_output=True, check=True)
    destination.mkdir()
    subprocess.run(["tar", "-x", "-C", str(destination)], input=archive.stdout, check=True)


def replay(repo_path, bad, fixed, case, overlay_root=ROOT):
    scratch = ROOT / ".scratch"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as work:
        results = {}
        for label, head in (("bad", bad), ("fixed", fixed)):
            tree = Path(work) / label
            extract(repo_path, head, tree)
            for name in case["overlay"]:
                (tree / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(Path(overlay_root) / name, tree / name)
            run = subprocess.run(case["command"], cwd=tree, capture_output=True, text=True,
                                 env={**os.environ, "GIT_CEILING_DIRECTORIES": str(scratch)})
            output = re.sub(r"\bin \d+\.\d+s\b", "in <time>", (run.stdout + run.stderr).replace(str(tree), "."))
            results[label] = dict(exit=run.returncode, output=output.strip())
    if results["bad"]["exit"] == 0 or results["fixed"]["exit"] != 0:
        sys.exit(f"replay does not separate bad from fixed for PR {case['pr']}: {json.dumps(results, indent=2)}")
    return dict(command=case["command"], overlay=case["overlay"], bad=results["bad"], fixed=results["fixed"])


def event(case, finding, fixed, proof):
    lesson = dict(type="codex-review-lesson", repo=case["repo"], pr=case["pr"], severity=finding["severity"],
                  title=finding["title"], path=finding["path"], finding=finding["detail"], comment_url=finding["url"],
                  bad_head=finding["bad_head"], fixed_head=fixed, fix_summary=case["fix_summary"],
                  covered_by=case["covered_by"], replay=proof)
    ref = f"codex-finding:{case['repo']}:{case['pr']}:{finding['id']}:{finding['bad_head'][:12]}:{fixed[:12]}"
    return dict(kind="review", repo=case["repo"], command="codex review finding fixed", agent="codex-review",
                error=json.dumps(lesson, indent=2, sort_keys=True), source_ref=ref,
                thread=f"https://github.com/{case['repo']}/pull/{case['pr']}", occurred=finding["occurred"])


def fetch_comment(repo, comment_id):
    run = subprocess.run(["gh", "api", f"repos/{repo}/pulls/comments/{comment_id}"], capture_output=True, text=True, check=True)
    return json.loads(run.stdout)


def resolve_commit(repo_path, rev):
    run = subprocess.run(["git", "-C", str(repo_path), "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
                         capture_output=True, text=True)
    if run.returncode != 0:
        sys.exit(f"commit {rev} is not in {repo_path}; pass --repo-path pointing at a checkout with the full "
                 "TayHCode/coldlocker history")
    return run.stdout.strip()


def build(case, repo_path, fetch=fetch_comment, overlay_root=ROOT):
    finding = parse_finding(fetch(case["repo"], case["comment_id"]))
    if finding is None:
        sys.exit(f"comment {case['comment_id']} is not a Codex P1/P2 finding")
    resolve_commit(repo_path, finding["bad_head"])
    fixed = resolve_commit(repo_path, case["fixed"])
    ancestor = subprocess.run(["git", "-C", str(repo_path), "merge-base", "--is-ancestor", finding["bad_head"], fixed])
    if ancestor.returncode != 0:
        sys.exit(f"{fixed} does not descend from reviewed head {finding['bad_head']}")
    return event(case, finding, fixed, replay(repo_path, finding["bad_head"], fixed, case, overlay_root))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(ROOT / "docs/codex-findings/cases.json"))
    parser.add_argument("--repo-path", required=True, help="checkout with the full TayHCode/coldlocker history")
    args = parser.parse_args(argv)
    for case in json.loads(Path(args.cases).read_text(encoding="utf-8")):
        print(json.dumps(build(case, args.repo_path)))


if __name__ == "__main__":
    main()
