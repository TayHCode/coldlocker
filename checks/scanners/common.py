import configparser
import contextlib
import hashlib
import heapq
import json
import os
from pathlib import Path
import re
import sqlite3
import xml.etree.ElementTree as ET

import safety
from checks import validators
from importers import LIMIT, json_object, package_bounds, selected_path
from portable import python_command


KINDS = {'skill': 'skills', 'rule': 'rules', 'routine': 'routines',
         'mcp_server': 'mcp_servers', 'plugin': 'plugins', 'connection': 'connections'}
STATUSES = ('new', 'needs_review', 'duplicate', 'already_in_locker', 'conflict', 'vendor', 'connect', 'skipped')
PRIVATE = 'definition contains a secret or private path/name'
REPLACES = 'replaces the stored copy that had a credential in its args'
REUSED = 'contains a credential value that also appears in another config'
NEXT_STEPS = (('schedule is kept by the Claude Desktop app', 'Write a routine JSON with a schedule (the SKILL.md body is the prompt), then run {python} hub.py import-routine <ROUTINE_JSON>.'),
              ('Codex command-approval', 'Keep them in Codex; re-create any you want in another client by hand.'),
              ('definition contains', 'Remove the flagged value from the source, then scan again.'),
              ('package too large', 'Keep each file under 1 MiB and the package under 1000 files, then scan again.'),
              ('skills.tgz is not unpacked', 'Extract skills.tgz into the export folder, then scan again.'),
              ('managed by agents', 'Change it through agents sync; the locker does not import it.'),
              ('credential|environment indirection|interpolation', 'Move the credential into the private store (see docs/mcp-auth.md), then scan again.'))
NOT_SCANNED = (('grok', '.grok'), ('antigravity', '.antigravity'), ('opencodex', '.opencodex'),
               ('pi', '.pi'), ('copilot', '.copilot'), ('cagent', '.cagent'))
MANUAL_IMPORT = 'Import by hand: {python} hub.py import-skill <SKILL_DIR> or {python} hub.py import-rule <RULE_MD>'
SOURCE_PATH = re.compile(r'~/[\w.+\-/]+')
EXCLUDE = {'.git', '.worktrees', 'node_modules', '.hub-store', '__pycache__', '.trash'}
ERRORS = (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, sqlite3.Error, ET.ParseError, configparser.Error, ImportError)


def digest(data):
    return hashlib.sha256(data.replace(b'\r\n', b'\n')).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def credential_finding(origin):
    return safety.finding('credential', 'credential-value', f'contains a credential value from {origin}', 'scan', True)


def home_pattern(home, platform):
    forms = '|'.join(re.escape(v) for v in dict.fromkeys((str(home), home.as_posix())))
    return re.compile('(?:' + forms + r')(?![\w.-])', re.I if platform == 'win32' else 0)


def slug(value):
    value = re.sub('[^a-z0-9-]+', '-', str(value).lower()).strip('-')[:63]
    return validators.name(value or 'unnamed')


def read(path):
    selected_path(path.parent, path.name)
    with path.open('rb') as stream:
        data = stream.read(LIMIT + 1)
    validators.require(len(data) <= LIMIT, 'input too large')
    return data


def tree_hash(path):
    package_bounds(path)
    entries = []
    for parent, directories, files in os.walk(path, followlinks=False):
        validators.require(not any((Path(parent) / n).is_symlink() for n in directories + files), 'symlink')
        validators.require(not EXCLUDE.intersection(directories), 'excluded package content')
        for name in sorted(files):
            item = Path(parent) / name
            entries.append((item.relative_to(path).as_posix(), digest(read(item))))
    return digest(''.join(f'{name}\0{sha}\n' for name, sha in sorted(entries)).encode())


def walk(base, depth=8, limit=5000, on_limit=None):
    pending = [(base, 0)]
    visited = 0
    while pending:
        directory, level = pending.pop()
        try:
            selected_path(directory)
            with os.scandir(directory) as stream:
                remaining = limit - visited
                entries = heapq.nsmallest(max(1, remaining + 1), stream,
                                          key=lambda e: (e.name != 'SKILL.md', e.name))
            if len(entries) > remaining:
                if on_limit:
                    on_limit(base, limit)
                entries = entries[:max(0, remaining)]
            visited += len(entries)
            for entry in entries:
                if entry.name in EXCLUDE or entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False) and level < depth:
                    pending.append((Path(entry.path), level + 1))
                elif entry.is_file(follow_symlinks=False):
                    yield Path(entry.path)
        except ERRORS:
            continue
        if pending and visited >= limit:
            if on_limit:
                on_limit(base, limit)
            break


def next_step(reason):
    step = next((step for pattern, step in NEXT_STEPS if re.search(pattern, reason)), 'Fix the cause in the source, then scan again, or leave it out.')
    return step.replace('{python}', python_command())


def conflict(item, reason, changed=False):
    renamable = item['kind'] in ('skill', 'rule')
    path = item['source'] if SOURCE_PATH.fullmatch(item['source']) else '<path>'
    if renamable and changed:
        step = f"re-import it with {python_command()} hub.py import-{item['kind']} {path} --name {item['name']} --origin scan-{item['agent']}"
    elif renamable:
        step = f"import one with {python_command()} hub.py import-{item['kind']} {path} --name <new>"
    else:
        step = 'rename one in its source, then scan again'
    item.update(status='conflict', importable=False, reason=reason, next_step=step[0].upper() + step[1:])


def located(files):
    lines = {}
    for label, text in files:
        for number, category in validators.findings(text):
            lines.setdefault((category, label), []).append(number)
    return ', '.join(f"{category} at line{'s' if len(numbers) > 1 else ''} {', '.join(map(str, numbers))} ({label})"
                     for (category, label), numbers in lines.items())


def paths(home, platform, env):
    config = Path(env.get('XDG_CONFIG_HOME', home / '.config'))
    if platform == 'darwin':
        app = home / 'Library/Application Support'
        claude_desktop = [app / 'Claude/claude_desktop_config.json']
    elif platform == 'win32':
        app = Path(env.get('APPDATA', home / 'AppData/Roaming'))
        claude_desktop = [app / 'Claude/claude_desktop_config.json']
    else:
        app = config
        claude_desktop = []
    return {'opencode': [config / 'opencode', app / 'opencode'],
            'claude_desktop': claude_desktop,
            't3': [home / '.t3', app / 'T3 Code', app / 't3-code',
                   Path(env.get('LOCALAPPDATA', home / 'AppData/Local')) / 'T3 Code']}


def texts(payload, name='item'):
    if isinstance(payload, Path):
        files = [payload] if payload.is_file() else list(walk(payload, limit=1001))
        base = payload.parent if payload.is_file() else payload
        result = []
        for file in files:
            with contextlib.suppress(*ERRORS):
                result.append((file.relative_to(base).as_posix(), read(file).decode('utf-8', errors='replace')))
        return result
    if isinstance(payload, bytes):
        return [(name + '.md', payload.decode('utf-8', errors='replace'))]
    if isinstance(payload, dict):
        return [(label, text) for label, text in payload.items() if isinstance(text, str)]
    if isinstance(payload, tuple):
        return [('prompt.md', payload[1])]
    return []


def disabled_names(value, *keys):
    return {name for key in keys for name in (value.get(key) if isinstance(value.get(key), list) else []) if isinstance(name, str)}


def linked(items):
    servers = {(i['agent'], i['source'], i['name']): i['id'] for i in items if i['kind'] == 'mcp_server'}
    return {i['id']: servers[i['agent'], i['source'], i['name']] for i in items
            if i['kind'] == 'connection' and (i['agent'], i['source'], i['name']) in servers}


def mcp_json_disabled(base):
    switched_off = set()
    for name in ('settings.json', 'settings.local.json'):
        with contextlib.suppress(*ERRORS):
            switched_off |= disabled_names(json_object(read(base / '.claude' / name).decode()), 'disabledMcpjsonServers')
    return switched_off


def selected_runner():
    import scanner
    return scanner.default_runner
