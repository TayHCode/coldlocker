import argparse
from pathlib import Path
import re
import subprocess

from checks.scan import ENCODING_LABEL, FIXTURE_HOSTS, HOST, PACKAGE_VERSION_LABEL, email_matches, findings, local_patterns


AUDIT_ID = re.compile(r"(?i)v[0-9]+-[0-9]+\Z")
COAUTHOR_TRAILER = re.compile(r"(?i)co-authored-by:\s+[^<>]+<([^<>]+)>\s*\Z")
MODEL_TRAILER = re.compile(r"(?i)co-authored-by:\s+GPT-[0-9]+(?:[.][0-9]+)*(?:-[a-z]+)*(?: [a-z]+)*\s+<noreply[@]openai[.]com>\s*\Z")


def text_findings(text, extra=(), allowed_emails=()):
    result = set(findings(text))
    allowed = {address.lower() for address in allowed_emails}
    for number, line in enumerate(text.splitlines(), 1):
        trailer = COAUTHOR_TRAILER.fullmatch(line)
        if (trailer and (trailer.group(1).lower() in allowed
                         or re.fullmatch(r"(?i)noreply[@](?:anthropic|openai)[.]com", trailer.group(1)))
                and all(match.group().lower() == trailer.group(1).lower() for match in email_matches(line))):
            result.discard((number, "email-address"))
        for match in HOST.finditer(line):
            if MODEL_TRAILER.fullmatch(line):
                continue
            label = match.group().lower()
            if (label not in FIXTURE_HOSTS and not ENCODING_LABEL.fullmatch(label)
                    and not PACKAGE_VERSION_LABEL.fullmatch(label) and not AUDIT_ID.fullmatch(label)):
                result.add((number, "private-device-label"))
        if any(pattern.search(line) for pattern in extra):
            result.add((number, "private-pattern"))
    return sorted(result)


def git(root, *args):
    result = subprocess.run(["git", "--no-replace-objects", *args], cwd=root,
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise ValueError("cannot read commit range; provide existing commits and full local history")
    return result.stdout.strip()


def scan_range(root, base, head="HEAD"):
    root = Path(git(root, "rev-parse", "--show-toplevel"))
    if git(root, "rev-parse", "--is-shallow-repository") != "false":
        raise ValueError("full local history is required")
    base_id = git(root, "rev-parse", "--verify", "--end-of-options", base + "^{commit}")
    head_id = git(root, "rev-parse", "--verify", "--end-of-options", head + "^{commit}")
    git(root, "merge-base", base_id, head_id)
    commits = git(root, "rev-list", base_id + ".." + head_id).splitlines()
    extra, errors = local_patterns(root)
    allowlist = root / 'config/commit-email-allowlist.txt'
    allowed = allowlist.read_text(encoding='utf-8').split() if allowlist.is_file() else ()
    for commit in commits:
        text = git(root, "show", "--no-patch", "--no-show-signature", "--format=%B", "--encoding=UTF-8", commit)
        errors.extend(f"commit {commit}:{line}: {rule}" for line, rule in text_findings(text, extra, allowed))
    return errors, len(commits)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Scan new commit subjects and bodies in an explicit trusted base..head range.")
    parser.add_argument("--base", required=True, help="trusted existing commit excluded from the range")
    parser.add_argument("--head", default="HEAD", help="commit whose new history is checked (default: HEAD)")
    args = parser.parse_args(argv)
    try:
        errors, count = scan_range(Path.cwd(), args.base, args.head)
    except (OSError, ValueError):
        print("FAIL: new commit text: cannot read range or local patterns; use valid commits with common full history")
        return 1
    if errors:
        for error in errors:
            print("FAIL: " + error)
        return 1
    print(f"PASS: new commit text ({count} commits)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
