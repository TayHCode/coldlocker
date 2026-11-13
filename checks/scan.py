import ast
import hashlib
import ipaddress
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


RULES = {
    "github-token": r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github[_]pat_[A-Za-z0-9_]{20,})",
    "openai-token": r"\bsk[-](?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}",
    "aws-key": r"\b(?:AKI[A]|ASI[A])[A-Z0-9]{16}\b",
    "slack-token": r"\bxox[baprs][-][A-Za-z0-9-]{10,}",
    "stripe-token": r"\b[sr]k[_](?:live|test)[_][A-Za-z0-9]{16,}",
    "google-key": r"\bAI[z]a[A-Za-z0-9_-]{35}",
    "gitlab-token": r"\bglpat[-][A-Za-z0-9_-]{20,}",
    "huggingface-token": r"\bhf[_][A-Za-z0-9]{30,}",
    "npm-token": r"\bnpm[_][A-Za-z0-9]{36,}",
    "jwt": r"\bey[J][A-Za-z0-9_-]{8,}[.]ey[J][A-Za-z0-9_-]{8,}[.][A-Za-z0-9_-]{8,}",
    "bearer-value": r"\b[Bb]earer\s+[A-Za-z0-9._~+/-]{8,}=*",
    "private-key": r"-----BEGIN[ ](?:[A-Z0-9]+ )*PRIVATE KEY-----",
    "home-path": r"[/]home[/][^\s/]+|[/]Users[/][^\s/]+|[A-Za-z]:[\\/]Users[\\/][^\s\\/]+",
}
PATTERNS = {name: re.compile(pattern) for name, pattern in RULES.items()}
EXCLUDED = {".git", "__pycache__", ".scratch", ".worktrees", ".hub-store", ".agent-hub-ops"}
PRIVATE_PATTERN_EXEMPT = {"config/commit-email-allowlist.txt"}


IPV4 = re.compile(r"(?<![\w.])(?:[0-9]{1,3}[.]){3}[0-9]{1,3}(?!\w|[.][0-9])")
HOST = re.compile(r"(?i)\b(?:[a-z][a-z0-9]*(?:-[a-z0-9]+)*-[0-9]+(?:[.][a-z0-9-]+)*"
                  r"|[a-z][a-z0-9-]*(?:[.][a-z0-9-]+)*[.](?:internal|local|lan|home|ts[.]net))\b")
NUMERIC_HOST_LABEL = re.compile(r"(?i)[a-z][a-z0-9]*(?:-[a-z0-9]+)*-[0-9]+\Z")
HOST_PREFIX_CONTEXT = re.compile(r"(?i)(?:\b(?:host(?:name)?|node|server|machine|ssh|dns|endpoint|remote|"
                                 r"address|domain|service|contact)(?:\s+(?:to|at|is|named|on))?['\"`]*"
                                 r"\s*(?:[:=]\s*)?['\"`]*\s*"
                                 r"|\bconnect(?:ed|ing|ion)?\s+to\s*|\b(?:ping|visit)\s+|(?:https?|ssh)://)\Z")
PACKAGE_VERSION_LABEL = re.compile(r"(?i)[a-z][a-z0-9._-]*-[0-9]+(?:\.[0-9]+){1,3}\Z")
ENCODING_LABEL = re.compile(r"(?i)(?:utf-(?:8|16|32)|windows-12(?:5[0-8]|6[0-9])|"
                            r"iso-8859-[0-9]{1,2}|latin-[0-9]|cp-?[0-9]{3,4})\Z")
EMAIL = re.compile(r"(?i)(?<![\w.!#$%&'*+/=?^_`{|}~-])[\w.!#$%&'*+/=?^_`{|}~-]+@"
                   r"((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63})(?![\w-])")
FIXTURE_HOSTS = {"example-01", "fixture-01", "test-01"}
UNAMBIGUOUS_HOST_SUFFIXES = (".internal", ".lan", ".ts.net")
CONTEXTUAL_HOST_SUFFIXES = (".local", ".home")
SAFE_EMAIL_DOMAINS = {"example.com", "example.net", "example.org"}
SAFE_EMAIL_TLDS = {"example", "invalid", "localhost", "test"}
FILE_RULE_EXEMPTIONS = {"config/commit-email-allowlist.txt": {"email-address"}}


def allowed_ipv4(address):
    octets = tuple(address.packed)
    return (octets in ((127, 0, 0, 1), (0, 0, 0, 0))
            or octets[:3] in ((192, 0, 2), (198, 51, 100), (203, 0, 113)))


def address_findings(text, assembled=False):
    result = set()
    for match in IPV4.finditer(text):
        try:
            address = ipaddress.IPv4Address(match.group())
        except ipaddress.AddressValueError:
            continue
        if not allowed_ipv4(address):
            octets = address.packed
            result.add("tailnet-ip" if octets[0] == 100 and 64 <= octets[1] < 128 else "ipv4-address")
    if assembled:
        host = text
        if text.startswith(("http://", "https://", "ssh://")):
            try:
                host = urlsplit(text).hostname or ""
            except ValueError:
                host = ""
        if HOST.fullmatch(host) and host.lower() not in FIXTURE_HOSTS:
            result.add("assembled-host")
    return result


def email_matches(text):
    return [match for match in EMAIL.finditer(text)
            if match.group(1).lower() not in SAFE_EMAIL_DOMAINS
            and match.group(1).rsplit(".", 1)[-1].lower() not in SAFE_EMAIL_TLDS]


def plaintext_host_findings(text, structured_identifier=False):
    emails = list(EMAIL.finditer(text))
    result = set()
    for match in HOST.finditer(text):
        host = match.group().lower()
        if (host in FIXTURE_HOSTS or PACKAGE_VERSION_LABEL.fullmatch(host) or ENCODING_LABEL.fullmatch(host)
                or any(match.start() < email.end() and email.start() < match.end() for email in emails)):
            continue
        context = text[max(0, match.start() - 40):match.start()]
        has_context = bool(HOST_PREFIX_CONTEXT.search(context))
        standalone = text.strip().lower() == host
        if host.endswith(UNAMBIGUOUS_HOST_SUFFIXES):
            result.add("private-host")
        elif (host.endswith(CONTEXTUAL_HOST_SUFFIXES)
              and (has_context or standalone)
              and not text[match.end():].lstrip().startswith("(")):
            result.add("private-host")
        elif has_context or (standalone and not
                             (structured_identifier and NUMERIC_HOST_LABEL.fullmatch(host))):
            result.add("private-host")
    return result


def literal_value(node, names, depth=0):
    if depth > 20:
        raise ValueError
    get = lambda item: literal_value(item, names, depth + 1)
    if isinstance(node, ast.Constant) and type(node.value) in (str, int):
        return node.value
    if isinstance(node, ast.Name) and node.id in names:
        return names[node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        if len(node.elts) > 64:
            raise ValueError
        return tuple(get(item) for item in node.elts)
    if isinstance(node, ast.BinOp):
        left, right = get(node.left), get(node.right)
        if isinstance(node.op, ast.Add) and type(left) is type(right) and isinstance(left, (str, int, tuple)):
            value = left + right
        elif isinstance(node.op, ast.Mod) and isinstance(left, str):
            if "*" in left or any(int(width) > 4096 for width in re.findall(r"[0-9]+", left)):
                raise ValueError
            value = left % right
        else:
            raise ValueError
        if isinstance(value, (str, tuple)) and len(value) > 4096:
            raise ValueError
        return value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                parts.append(part.value)
            else:
                value = get(part.value)
                if part.conversion == 114:
                    value = repr(value)
                elif part.conversion in (115, 97):
                    value = str(value) if part.conversion == 115 else ascii(value)
                spec = get(part.format_spec) if part.format_spec else ""
                if any(int(width) > 4096 for width in re.findall(r"[0-9]+", spec)):
                    raise ValueError
                if len(spec) > 32 or not re.fullmatch(r"[0-9<>=^ .dfs]*", spec):
                    raise ValueError
                parts.append(format(value, spec))
        return "".join(parts)
    if isinstance(node, ast.Call) and not node.keywords:
        if isinstance(node.func, ast.Name) and node.func.id == "str" and len(node.args) == 1:
            return str(get(node.args[0]))
        if (isinstance(node.func, ast.Name) and node.func.id == "map" and len(node.args) == 2
                and isinstance(node.args[0], ast.Name) and node.args[0].id == "str"):
            return tuple(str(item) for item in get(node.args[1]))
        if isinstance(node.func, ast.Attribute) and len(node.args) == 1:
            value, args = get(node.func.value), get(node.args[0])
            if node.func.attr == "join" and isinstance(value, str) and isinstance(args, tuple):
                return value.join(args)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            value = get(node.func.value)
            if (isinstance(value, str) and re.fullmatch(r"(?:[^{}]|\{[0-9]*\})*", value)):
                if len(value) > 4096:
                    raise ValueError
                return value.format(*(get(arg) for arg in node.args))
    raise ValueError


def bind_literal(target, value, names):
    if isinstance(target, ast.Name):
        names.pop(target.id, None)
        if value is not None:
            names[target.id] = value
    elif isinstance(target, (ast.Tuple, ast.List)):
        values = value if isinstance(value, tuple) and len(value) == len(target.elts) else (None,) * len(target.elts)
        for item, member in zip(target.elts, values):
            bind_literal(item, member, names)


def python_findings(text):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return []
    names = {}
    lines = text.splitlines(keepends=True)
    result = set()
    for node in sorted(ast.walk(tree), key=lambda item: getattr(item, "lineno", 0)):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            try:
                value = literal_value(node.value, names)
            except (ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError):
                value = None
            for target in targets:
                bind_literal(target, value, names)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            start, end = node.lineno - 1, node.end_lineno - 1
            if start == end:
                segment = lines[start].encode()[node.col_offset:node.end_col_offset].decode(errors="replace")
            else:
                segment = (lines[start].encode()[node.col_offset:].decode(errors="replace")
                           + "".join(lines[start + 1:end])
                           + lines[end].encode()[:node.end_col_offset].decode(errors="replace"))
            if re.search(r"[\"']\s+[\"']", segment):
                result.update((node.lineno, rule) for rule in address_findings(node.value, True))
        if isinstance(node, (ast.BinOp, ast.JoinedStr, ast.Call)):
            try:
                value = literal_value(node, names)
            except (ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError):
                continue
            if isinstance(value, str):
                result.update((node.lineno, rule) for rule in address_findings(value, True))
    return result


def findings(text, filename=None, structured_identifier=False):
    result = {(number, name) for number, line in enumerate(text.splitlines(), 1)
              for name, pattern in PATTERNS.items() if pattern.search(line)}
    for number, line in enumerate(text.splitlines(), 1):
        result.update((number, rule) for rule in address_findings(line))
        result.update((number, "private-host") for _ in plaintext_host_findings(line, structured_identifier))
        result.update((number, "email-address") for _ in email_matches(line))
    if filename and str(filename).endswith(".py"):
        result.update(python_findings(text))
    else:
        fragment = r"[\"']([^\"'\n]*)[\"']"
        for number, line in enumerate(text.splitlines(), 1):
            if re.search(r"(?:\bf[\"']|[\"']\s*%|[.]join\()", line):
                result.update((number, rule) for _, rule in python_findings(line.strip()))
            for match in re.finditer(fragment + r"(?:\s*\+\s*" + fragment + r")+", line):
                value = "".join(re.findall(fragment, match.group()))
                result.update((number, rule) for rule in address_findings(value, True))
    if str(filename).replace("\\", "/") == "skills/poteto-mode/scripts/watch-pr/github.ts":
        lines = text.splitlines()
        result = {finding for finding in result if not (
            finding[1] == "email-address" and all(
                match.group() in {"@".join(("git", "github.com")),
                                  "@".join(("//git", "github.com"))}
                for match in email_matches(lines[finding[0] - 1])))}
    return sorted(result)


def git_files(root):
    result = subprocess.run(["git", "ls-files", "-z", "--"], cwd=root,
                            capture_output=True)
    if result.returncode == 0:
        return [Path(name.decode()) for name in result.stdout.split(b"\0") if name]
    return None


def local_patterns(root):
    path = root / ".hub-store" / "private-patterns.txt"
    patterns, errors = [], []
    if not path.is_file() or path.is_symlink():
        return patterns, errors
    for number, line in enumerate(path.read_text(errors="replace", encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                patterns.append(re.compile(line.strip(), re.IGNORECASE))
            except re.error:
                errors.append(f"{path.relative_to(root).as_posix()}:{number}: invalid pattern")
    return patterns, errors


def scan(root, paths=None):
    extra, errors = local_patterns(root)
    paths = git_files(root) if paths is None else paths
    if paths is None:
        paths = [path.relative_to(root) for path in root.rglob("*")
                 if not EXCLUDED.intersection(path.relative_to(root).parts)
                 and path.is_file() and not path.is_symlink()]
    for relative in sorted(paths):
        path = root / relative
        try:
            content = os.readlink(path) if path.is_symlink() else path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            errors.append(f"{relative.as_posix()}: unreadable tracked file")
            continue
        found = set(findings(content, relative))
        # Retain the verified upstream MIT copyright contact, bound to the whole document.
        if (relative.as_posix() == "licenses/npm/commander-14.0.0/LICENSE"
                and hashlib.sha256(content.encode()).hexdigest()
                == "04512a63dce4d2d506ad612dc0bd7681ccf6e3655f7b6eaef7dfac8323d1ec0b"):
            found.discard((3, "email-address"))
        exempt = FILE_RULE_EXEMPTIONS.get(relative.as_posix(), set())
        found = {finding for finding in found if finding[1] not in exempt}
        if relative.as_posix() not in PRIVATE_PATTERN_EXEMPT:
            found.update((number, "private-pattern") for number, text in enumerate(content.splitlines(), 1)
                         if any(pattern.search(text) for pattern in extra))
        errors.extend(f"{relative.as_posix()}:{line}: {rule}" for line, rule in sorted(found))
    return errors
