import ipaddress
import re
from urllib.parse import urlsplit

from checks.scan import PATTERNS

I = re.I
INJECTION = 'prompt-injection'
COMMAND = 'dangerous-command'
PACKAGE = 'mcp-package'
REMOTE = 'mcp-remote'
SECRET = 'secret'

NEGATION = re.compile(r"^\s*(?:(?:[-*+>]|\d+[.)])\s+)*[*_]*(?:(?:warning|caution)\b[*_]*:?|(?:never|do not|don't|dont|avoid|must not|should not|shouldn't)\b)", I)
CLAUSE_END = re.compile(r"[.!?;:,]|\b(?:then|and|but)\b", I)
EXAMPLE_LEAD = re.compile(r",?\s*(?:for example|for instance|e\.g\.|such as)\s*", I)
LEAD_NEGATION = re.compile(r"\b(?:do not|don't|never|must not|should not)\s+(?:ever\s+)?\Z", I)
REJECTION = re.compile(r"""\A[,.;:]?["'”’]?\s*[,.;:\u2013\u2014-]?\s*(?:do not|don't|never|must not|should not)\s+(?:follow|obey|act on|comply|run|execute)\b""", I)
OPEN_QUOTES = '"\'“‘'
LOADER_KEYS = re.compile(r"(?:NODE_OPTIONS|LD_PRELOAD|PYTHONSTARTUP|DYLD_\w+)\Z", I)
SHELL_VERB = r"(?:cat|less|more|head|tail|cp|scp|rsync|tar|zip|base64|xxd|strings|grep|curl|wget|nc|ncat|sqlite3|Get-Content|copy)"
PROSE_VERB = r"(?:read|open|copy|send|upload|collect|steal|exfiltrate|dump|show|print|display|paste|include|output|echo|reveal)"
SHELL = r"(?:ba|z|da|k)?sh"
RUNTIME = r"(?:" + SHELL + r"|python3?|perl|ruby|node)"
BROAD_TARGET = (r"(?:/\*?|~/?\*?|\$HOME/?\*?|\$\{HOME\}/?\*?|\.\.?/?\*?|\*|"
                r"/(?:etc|usr|var|home|root|bin|boot|opt|lib|sys|Users)/?\*?|[A-Za-z]:\\\\?\*?)")
SECRET_PATH = (r"(?:\.ssh/(?!config\b|known_hosts\b)(?![\w.-]*\.pub\b)[\w.-]*|\.aws/(?:credentials|config)|\.azure/|\.kube/config|"
               r"\.netrc\b|\.npmrc\b|\.pypirc\b|\.docker/config\.json|\.gnupg\b|\.config/gcloud|\.config/gh/hosts\.yml|"
               r"\.git-credentials|Library/Keychains|login\.keychain|\.config/google-chrome|\.config/chromium|"
               r"\.mozilla/firefox|Application Support/(?:Google/Chrome|Firefox)|Chrome/(?:User Data|Default)|Login Data)")
ENV_FILE = r"(?<!\bto )(?<!\bas )(?<![\w.-])\.env(?:\.(?:local|production|development|prod))?(?![\w.-])"

COMMAND_RULES = (
    ('pipe-to-shell', r"\b(?:curl|wget)\b[^\n]*\|\s*(?:sudo\s+)?" + RUNTIME + r"\b",
     'Downloads a script and runs it without inspection'),
    ('pipe-to-shell', r"\b" + SHELL + r"\s+(?:-c\s+)?[\"']?(?:<\(|\$\()\s*(?:curl|wget)\b",
     'Downloads a script and runs it without inspection'),
    ('pipe-to-shell', r"\b(?:iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b[^\n]*\|\s*(?:iex|Invoke-Expression)\b",
     'Downloads a script and runs it without inspection'),
    ('eval-download', r"\b(?:eval|source)\s+[\"']?(?:<\(|\$\()\s*(?:curl|wget)\b",
     'Evaluates content fetched from the network'),
    ('eval-download', r"\b(?:exec|eval)\s*\([^\n]*(?:urlopen|requests\.(?:get|post)|httpx\.get|\bfetch\()",
     'Evaluates content fetched from the network'),
    ('eval-download', r"\b(?:iex|Invoke-Expression)\b[^\n]*(?:DownloadString|\biwr\b|\birm\b|Invoke-WebRequest)",
     'Evaluates content fetched from the network'),
    ('base64-exec', r"\bbase64\s+(?:-d|-D|--decode)\b[^\n]*\|\s*(?:sudo\s+)?(?:" + RUNTIME + r"|eval)\b",
     'Decodes a hidden payload and runs it'),
    ('base64-exec', r"\b(?:exec|eval)\s*\(\s*(?:base64\.b64decode|b64decode|atob|Buffer\.from)\b",
     'Decodes a hidden payload and runs it'),
    ('base64-exec', r"\beval\s+[\"']?\$\([^\n]*base64\s+(?:-d|-D|--decode)\b",
     'Decodes a hidden payload and runs it'),
    ('base64-exec', r"\bpowershell[^\n]*\s-e(?:nc|ncodedcommand)?\s+[A-Za-z0-9+/=]{20,}",
     'Decodes a hidden payload and runs it'),
    ('rm-rf-broad', r"\brm\s+(?:-[A-Za-z-]+\s+)*-[A-Za-z]*[rR][A-Za-z]*\s+(?:-[A-Za-z-]+\s+)*[\"']?"
                    + BROAD_TARGET + r"[\"']?(?=\s|$|[;&|`)])",
     'Deletes a broad set of files (a home, system or root directory)'),
    ('rm-rf-broad', r"\b(?:Remove-Item|rd|rmdir|del)\b[^\n]*(?:-Recurse|/s)\b[^\n]*\s[A-Za-z]:\\\*?(?=\s|$)",
     'Deletes a broad set of files (a home, system or root directory)'),
    ('credential-read', r"\bsecurity\s+(?:find-(?:generic|internet)-password|dump-keychain)\b|\bsecret-tool\s+lookup\b",
     'Reads saved credentials from a keychain'),
    ('outbound-post', r"(?:\bprintenv\b|\benv\b|\bexport\s+-p\b)\s*\|\s*(?:curl|wget|nc|ncat)\b",
     'Sends environment variables to another machine'),
    ('outbound-post', r"\b(?:curl|wget)\b[^\n]*?(?:\s-d|\s--data(?:-binary|-raw|-urlencode)?|\s-F|\s--form|\s-T|\s--upload-file|\s--post-file)"
                      r"(?:\s+|=)[\"']?(?:[\w.-]+=)?(?:@\s*(?:~|/|\$HOME|\.env|\.ssh|\.aws)|\$\(\s*(?:cat|env|printenv)|`(?:cat|env|printenv)"
                      r"|\$\{?[A-Za-z_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Za-z_]*\}?[\"']?(?:\s|$))",
     'Uploads environment values or local files to another machine'),
    ('outbound-post', r"\b(?:requests|httpx|urllib\.request)\b[^\n]*os\.environ|process\.env[^\n]*\b(?:fetch|axios)\b|\b(?:fetch|axios)\b[^\n]*process\.env",
     'Sends environment variables to another machine'),
    ('outbound-post', r"\b(?:nc|ncat)\s+\S+\s+\d+\s*<\s*(?:~|/|\$HOME)",
     'Uploads a local file to another machine'),
)
COMMAND_LABELS = {
    'pipe-to-shell': 'curl|sh-style install', 'eval-download': 'eval of downloaded content',
    'base64-exec': 'base64 decode then exec', 'rm-rf-broad': 'rm -rf on a broad path',
    'credential-read': 'credential file read', 'outbound-post': 'outbound post of env vars or files',
}

INSTRUCTION = tuple(re.compile(pattern, I) for pattern in (
    r"\b(?:ignore|disregard|forget|override|bypass)\s+(?:all\s+|any\s+|the\s+|your\s+|these\s+|those\s+|every\s+)*"
    r"(?:previous|prior|above|earlier|preceding|original|system|developer|safety|existing)\s+"
    r"(?:instructions?|rules?|prompts?|guidelines?|directions?|messages?|constraints?|polic(?:y|ies))",
    r"\b(?:ignore|disregard|stop listening to|do not (?:follow|obey|listen to))\s+(?:what\s+|anything\s+)?(?:the\s+)?"
    r"(?:user|human|system prompt|developer)(?![\w-])",
    r"\b(?:do not|don't|never|without)\s+(?:tell|telling|inform|informing|notify|notifying|alert|alerting|warn|warning|ask|asking)\s+(?:to\s+)?(?:the\s+)?(?:user|human|operator)\b(?!\s+to\b)",
    r"\b(?:do not|don't|never)\s+(?:mention|reveal|disclose|show)\s+(?:this|these|it|that)\b[^\n.]{0,40}\b(?:user|human|operator)\b",
    r"\byou\s+are\s+now\s+(?:in\s+)?(?:a|an|the|dan|developer|unrestricted)\b",
    r"\b(?:reveal|print|output|repeat)\s+(?:your\s+|the\s+)?(?:system\s+prompt|hidden\s+instructions)\b",
    r"\bnew\s+(?:system\s+)?instructions?\s*:",
    r"\bexfiltrat(?:e|es|ed|ing)\b",
))
EXFIL = re.compile(
    r"\b(?:send|post|upload|forward|transmit|leak|email)\b[^\n.]{0,80}\b(?:api[ _-]?keys?|credentials?|passwords?|secrets?|tokens?|ssh keys?|private keys?|"
    r"environment variables?|env vars?|cookies|conversation|chat history|system prompt)\b[^\n.]{0,80}\b(?:to|at|via)\b[^\n.]{0,40}"
    r"(?:https?://|\bserver\b|\bendpoint\b|\bwebhook\b|@)", I)
INVISIBLE = re.compile("[​‎‏‪-‮⁠-⁤⁦-⁩﻿\U000e0000-\U000e007f]")
JOINER = re.compile("(?<=[\x20-\x7e])[‌‍](?=[\x20-\x7e])")
COMMENT = re.compile(r"<!--(.*?)-->", re.S)
HIDDEN = (
    re.compile(r"<details\b(?![^>]*\bopen\b)[^>]*>(.*?)</details>", I | re.S),
    re.compile(r"<(\w+)\b[^>]*(?:style\s*=\s*[\"'][^\"']*(?:display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0|opacity\s*:\s*0)"
               r"|\bhidden\b)[^>]*>(.*?)</\1>", I | re.S),
    re.compile(r"^\[(?://|comment)\]:\s*(?:#|<>)\s*[\(\"'<]?(.*)$", I | re.M),
)
SECRET_RULES = {'github-token': 'GitHub token', 'openai-token': 'API key', 'aws-key': 'AWS access key',
                'slack-token': 'Slack token', 'stripe-token': 'Stripe key', 'google-key': 'Google API key',
                'gitlab-token': 'GitLab token', 'huggingface-token': 'Hugging Face token', 'npm-token': 'npm token',
                'jwt': 'JWT', 'bearer-value': 'bearer token', 'private-key': 'private key'}
NPX = {'npx', 'bunx', 'pnpx'}
PYX = {'uvx', 'pipx'}
EXACT_NPM = re.compile(r"(?:@[^/@\s]+/)?[^@/\s]+@\d+\.\d+\.\d+(?:[-+][\w.-]+)?\Z")
EXACT_PY = re.compile(r"[A-Za-z0-9][\w.-]*(?:\[[\w,-]+\])?(?:==|@)v?\d+(?:\.\d+)*(?:[-+.\w]*)\Z")
URL_SOURCE = re.compile(r"(?:git\+|https?://|ssh://|file:)", I)
URL_TEXT = re.compile(r"(?:https?|wss?)://[^\s\"'<>)\]]+", I)
INSTALL = re.compile(r"\b(?:npm|pnpm|yarn|bun|pip3?|uv\s+pip)\s+(?:install|i|add)\b", I)


def finding(category, check, reason, location, blocking=False):
    return {'category': category, 'check': check, 'reason': reason, 'location': location, 'blocking': blocking}


def locations(label, lines):
    shown = ','.join(str(n) for n in sorted(set(lines))[:4])
    return f'{label}:{shown}' + (' +more' if len(set(lines)) > 4 else '')


def merge(items):
    grouped = {}
    for item in items:
        grouped.setdefault((item['check'], item['reason'], item['label']), []).append(item)
    result = []
    for (check, reason, label), group in grouped.items():
        category = group[0]['category']
        lines = [n for item in group for n in item['lines']]
        result.append(finding(category, check, reason, locations(label, lines) if lines else label, group[0].get('blocking', False)))
    return result


def line_of(text, position):
    return text.count('\n', 0, position) + 1


def has_instruction(text):
    return any(p.search(text) for p in INSTRUCTION) or bool(EXFIL.search(text))


def negated(line, match):
    opening = NEGATION.match(line)
    between = EXAMPLE_LEAD.sub(' ', line[opening.end():match.start()]) if opening else ''
    return bool(opening and opening.end() <= match.start() and not CLAUSE_END.search(between))


def defensive(line, match):
    before = line[:match.start()]
    quoted = before.endswith(tuple(OPEN_QUOTES)) and bool(REJECTION.match(line[match.end():]))
    return bool(LEAD_NEGATION.search(before)) or quoted


def unnegated(pattern, line):
    return any(not negated(line, match) for match in re.finditer(pattern, line, I))


def text_findings(label, text):
    found = []
    lines = text.splitlines()

    def add(category, check, reason, line, blocking=False):
        found.append(dict(category=category, check=check, reason=reason, label=label, lines=[line] if line else [], blocking=blocking))

    for number, line in enumerate(lines):
        if INVISIBLE.search(line.lstrip('﻿') if number == 0 else line) or JOINER.search(line):
            add(INJECTION, 'invisible-characters', 'Contains zero-width or direction-changing characters that hide text from a reader', number + 1)
        for check, pattern, reason in COMMAND_RULES:
            if unnegated(pattern, line):
                add(COMMAND, check, f'{reason} ({COMMAND_LABELS[check]})', number + 1)
        for secret in (SECRET_PATH, ENV_FILE):
            if unnegated(secret, line):
                verbs = SHELL_VERB if secret is ENV_FILE else '(?:' + SHELL_VERB + '|' + PROSE_VERB + ')'
                if re.search(r"(?<![\w-])" + verbs + r"(?![\w-])", line, I):
                    add(COMMAND, 'credential-read', 'Reads a credential file such as SSH keys, cloud credentials, .env files, keychains or browser profiles (credential file read)', number + 1)
        for pattern in INSTRUCTION:
            if any(not defensive(line, match) for match in pattern.finditer(line)):
                add(INJECTION, 'override-instructions', 'Tells the agent to ignore, hide things from or work against the user or its system instructions', number + 1)
        if unnegated(EXFIL.pattern, line):
            add(INJECTION, 'exfiltration-instruction', 'Tells the agent to send private data to an outside address', number + 1)
        for name, kind in SECRET_RULES.items():
            if PATTERNS[name].search(line):
                add(SECRET, 'embedded-secret', f'Contains what looks like a {kind}. Remove it and keep it in the private store', number + 1, True)
    for match in COMMENT.finditer(text):
        if has_instruction(match.group(1)):
            add(INJECTION, 'hidden-instructions', 'Hides instructions for the agent in an HTML comment', line_of(text, match.start()))
    for pattern in HIDDEN:
        for match in pattern.finditer(text):
            if has_instruction(match.group(match.lastindex or 0)):
                add(INJECTION, 'hidden-instructions', 'Hides instructions for the agent in collapsed or invisible text', line_of(text, match.start()))
    return found


def scan_texts(files):
    found = []
    for label, text in files:
        found.extend(text_findings(label, text))
    return merge(found)


def secret_findings(files):
    return [f for f in scan_texts(files) if f['category'] == SECRET]


def is_loopback(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def urls_in(values):
    return [url for value in values if isinstance(value, str) for url in URL_TEXT.findall(value)]


def remote_findings(label, urls, known_hosts):
    found = []
    for url in urls:
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or '').lower()
        except ValueError:
            host = ''
        if not host or is_loopback(host) or host in known_hosts:
            continue
        found.append(finding(REMOTE, 'unknown-remote', f'Connects to a remote host not already known to this locker ({host})'
                             + (' over unencrypted http' if parsed.scheme == 'http' else ''), label))
    return found


def package_spec(command, args):
    name = command.replace('\\', '/').rsplit('/', 1)[-1].lower()
    for suffix in ('.cmd', '.exe'):
        name = name.removesuffix(suffix)
    args = [a for a in args if isinstance(a, str)]
    if name in ('pnpm', 'yarn') and args[:1] == ['dlx']:
        name, args = 'npx', args[1:]
    elif name == 'npm' and args[:1] in (['exec'], ['x']):
        name, args = 'npx', args[1:]
    elif name == 'pipx' and args[:1] == ['run']:
        args = args[1:]
    elif name == 'uv' and args[:2] == ['tool', 'run']:
        name, args = 'uvx', args[2:]
    elif name not in NPX | PYX:
        return None, None
    valued = {'-p', '--package', '--from', '--with', '--python', '-c', '--call'}
    package = None
    skip = False
    for index, arg in enumerate(args):
        if skip:
            skip = False
            continue
        if arg in ('-p', '--package', '--from') and index + 1 < len(args):
            return name, args[index + 1]
        if arg.startswith(('--package=', '--from=')):
            return name, arg.split('=', 1)[1]
        if arg in valued:
            skip = True
        elif not arg.startswith('-'):
            package = arg
            break
    return name, package


def server_findings(label, server, known_hosts, upstream=False):
    found = []
    command = server.get('command')
    args = server.get('args', [])
    if isinstance(command, str) and isinstance(args, list):
        runner, package = package_spec(command, args)
        if runner and package:
            local = package.startswith(('.', '/', '~')) and not URL_SOURCE.match(package)
            if URL_SOURCE.match(package):
                found.append(finding(PACKAGE, 'package-from-url', 'Runs code fetched from a URL or git source instead of a package registry', label))
            elif not local:
                exact = EXACT_NPM if runner in NPX else EXACT_PY
                if not exact.match(package):
                    found.append(finding(PACKAGE, 'unpinned-package', f'Runs {runner} without pinning an exact package version, so it can change under you', label))
        elif runner:
            found.append(finding(PACKAGE, 'unpinned-package', f'Runs {runner} without a recognizable package name', label))
        joined = ' '.join([command, *[a for a in args if isinstance(a, str)]])
        if INSTALL.search(joined):
            found.append(finding(PACKAGE, 'install-step', 'Installs packages when the server starts, which runs their install scripts', label))
    environment = {field: server[field] for field in ('env', 'headers') if isinstance(server.get(field), dict)}
    mapped = [value for mapping in environment.values() for value in mapping.values() if isinstance(value, str)]
    if not upstream:
        values = [server.get('url'), *(args if isinstance(args, list) else []), *mapped]
        found.extend(remote_findings(label, urls_in(values), known_hosts))
    if any(LOADER_KEYS.match(key) for key in environment.get('env', {})):
        found.append(finding(COMMAND, 'env-injection', 'Sets an environment variable that makes the server load extra code at startup (env-injection)', label))
    strings = [command, *(args if isinstance(args, list) else [])]
    command_line = ' '.join(s for s in strings if isinstance(s, str))
    for text, categories in ((command_line, None), *((value, {COMMAND}) for value in mapped)):
        for item in text_findings(label, text):
            if categories is None or item['category'] in categories:
                found.append(finding(item['category'], item['check'], item['reason'], label, item['blocking']))
    return found


def metadata_findings(label, value, known_hosts, upstream=False):
    strings = []

    def collect(node):
        if isinstance(node, dict):
            for key, item in node.items():
                collect(item)
        elif isinstance(node, list):
            for item in node:
                collect(item)
        elif isinstance(node, str):
            strings.append(node)
    collect(value)
    found = scan_texts([(label, '\n'.join(strings))]) if strings else []
    found = [dict(item, location=label) for item in found]
    if isinstance(value, dict) and not upstream and 'endpoint' in value:
        found.extend(remote_findings(label, urls_in([value['endpoint']]), known_hosts))
    for source in (value.get('source') if isinstance(value, dict) else None,):
        if isinstance(source, str) and re.match(r"http://", source, I):
            found.append(finding(REMOTE, 'unencrypted-source', 'Plugin source is fetched over unencrypted http', label))
        if isinstance(source, str) and re.search(r"\.(?:sh|ps1|zip|tgz|tar\.gz)(?:\?|$)", source, I):
            found.append(finding(PACKAGE, 'package-from-url', 'Plugin source is a raw script or archive URL instead of a marketplace entry', label))
    return found


def dedupe(found):
    seen = set()
    result = []
    for item in found:
        key = (item['check'], item['reason'], item['location'])
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def review(kind, payload, files=(), known_hosts=frozenset(), upstream=False, label='definition'):
    if kind in ('skill', 'rule', 'routine'):
        found = scan_texts(files)
    elif kind == 'mcp_server':
        found = server_findings(label, payload, known_hosts, upstream)
    else:
        found = metadata_findings(label, payload, known_hosts, upstream)
    return dedupe(found)
