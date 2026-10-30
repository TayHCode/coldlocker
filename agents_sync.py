import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from checks import validators
from checks.scan import findings
from gateway_render import render_server, write_output
from importers import LIMIT, json_object, skill_package
from mcp_endpoint import repo_file
from store import ordinary_path

try:
    import pwd
except ImportError:
    pwd = None


VERSION = '0.9.2'
VARIABLE = re.compile(r'\$\{([A-Z][A-Z0-9_]*)\}')
SENSITIVE = re.compile(r'token|secret|key|password|authorization|credential|auth|passphrase', re.I)


def ordinary(path):
    path = Path(os.path.abspath(path))
    ordinary_path(path)
    validators.require(all(not p.exists() or p.is_dir() for p in path.parents), 'parent is not a directory')
    return path


def project(path):
    path = ordinary(path)
    homes = {Path.home()}
    if pwd and hasattr(os, 'getuid'):
        homes.add(Path(pwd.getpwuid(os.getuid()).pw_dir))
    for home in homes:
        protected = [home / name for name in ('.codex', '.cursor', '.agents', '.config/opencode')]
        protected.append(home / '.claude')
        validators.require(not path.is_relative_to(home) or not path.relative_to(home).parts
                           or not path.relative_to(home).parts[0].startswith('.claude'), 'protected client target')
        validators.require(not any(path.is_relative_to(p) or p.is_relative_to(path) for p in protected),
                           'protected client target or ancestor')
    return path


def load(path):
    path = ordinary(path)
    validators.require(path.is_file() and path.stat().st_size <= LIMIT, 'missing or oversized configuration')
    return json_object(path.read_text(encoding='utf-8'))


def config(target):
    path = ordinary(target / '.agents/agents.json')
    if not path.exists():
        return {'schemaVersion': 4, 'instructions': {'path': 'AGENTS.md'},
                'integrations': {'enabled': ['claude', 'codex', 'cursor', 'opencode'],
                                 'options': {'claudeScope': 'project', 'cursorAutoApprove': False}},
                'syncMode': 'commit-generated', 'mcp': {'servers': {}}}
    value = load(path)
    validators.require(isinstance(value, dict) and value.get('schemaVersion') == 4,
                       'requires agents schema 4; migrate with upstream CLI first')
    validators.require(isinstance(value.get('mcp'), dict) and isinstance(value['mcp'].get('servers'), dict),
                       'invalid agents MCP registry')
    return value


def secret_reference(value):
    if validators.SECRET.fullmatch(value):
        return value
    match = VARIABLE.fullmatch(value)
    if not match:
        return None
    variable = match[1]
    prefix = 'AGENTLOCKER_SECRET_'
    if variable.startswith(prefix):
        try:
            reference = 'secret://' + bytes.fromhex(variable[len(prefix):]).decode()
        except (ValueError, UnicodeError):
            raise ValueError('invalid locker placeholder') from None
        validators.require(validators.SECRET.fullmatch(reference), 'invalid locker placeholder')
        return reference
    return 'secret://agents-' + hashlib.sha256(variable.encode()).hexdigest()[:24]


def parse_server(name, source):
    validators.name(name)
    validators.require(isinstance(source, dict), 'invalid upstream server')
    allowed = {'transport', 'command', 'args', 'url', 'env', 'headers', 'description', 'label', 'enabled', 'targets'}
    validators.require(set(source) <= allowed and source.get('enabled', True) is True,
                       'unsupported or disabled server; keep advanced settings upstream and use gateway export')
    result = {'name': name, 'description': source.get('description') or 'Imported from agents sync',
              **{k: v for k, v in source.items() if k in {'transport', 'command', 'args', 'url'}}}
    for field in ('env', 'headers'):
        if field not in source:
            continue
        mapping = source[field]
        validators.require(isinstance(mapping, dict), 'invalid environment or headers')
        result[field] = {}
        for key, value in mapping.items():
            validators.require(isinstance(key, str) and isinstance(value, str), 'invalid environment or headers')
            reference = secret_reference(value)
            if reference:
                result[field][key] = reference
            else:
                validators.require(not SENSITIVE.search(key) and not VARIABLE.search(value)
                                   and '${' not in value and len(value) < 24,
                                   'literal credentials or templates refused; use a whole ${VAR} placeholder, '
                                   'then privately populate its secret reference in Store')
                result[field][key] = value
    validators.mcp(result)
    render_server(result)
    for arg in result.get('args', []):
        validators.require(not SENSITIVE.search(arg.split('=')[0]) if arg.startswith('-') else True,
                           'credential arguments require manual migration to upstream gateway')
    return result


def packages(directory):
    ordinary(directory)
    if not directory.exists():
        return []
    found = []
    for parent, directories, files in os.walk(directory, followlinks=False):
        for name in directories + files:
            ordinary(Path(parent) / name)
        if 'SKILL.md' in files:
            package = skill_package(Path(parent))
            found.append(package)
            directories[:] = []
    names = [p.name for p in found]
    validators.require(len(names) == len(set(names)), 'duplicate skill names')
    return sorted(found)


def unchanged(destination, source):
    ordinary(destination)
    if destination.exists():
        if destination.is_dir():
            skill_package(destination)
        validators.require(validators.digest(destination) == validators.digest(source),
                           'conflict: existing content differs; reconcile explicitly before retrying')
        return True
    return False


def location(root, entries, name, relative):
    entry = next((e for e in entries if e['name'] == name), None)
    return root / (entry['path'] if entry else f'{validators.LOCAL}/{relative}')


def import_project(target, root, owner='agentlocker', approve_risky=False):
    from hub import import_item, screen
    from scanner import texts
    target, root = project(target), project(root)
    value = config(target)
    validators.require((target / '.agents/agents.json').exists(), 'initialize the upstream project first')
    manifest = validators.layered(root, validators.manifest(load(root / 'manifest.json')))
    servers = [parse_server(name, source) for name, source in value['mcp']['servers'].items()]
    pending = []
    for source in packages(target / '.agents/skills'):
        destination = location(root, manifest['skills'], source.name, f'skills/{source.name}')
        validators.require(not destination.exists() or
                           any(e['name'] == source.name for e in manifest['skills']),
                           'conflict: unfamiliar skill destination')
        if not unchanged(destination, source):
            pending.append(('skill', source))
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        for server in servers:
            source = Path(temporary) / (server['name'] + '.json')
            source.write_bytes((json.dumps(server, indent=2) + '\n').encode('utf-8'))
            destination = location(root, manifest['mcp_servers'], server['name'], f'mcp/servers/{source.name}')
            validators.require(not destination.exists() or
                               any(e['name'] == server['name'] for e in manifest['mcp_servers']),
                               'conflict: unfamiliar server destination')
            if not unchanged(destination, source):
                pending.append(('mcp', source))
        for kind, source in pending:
            name = source.name if kind == 'skill' else source.stem
            collection = 'skills' if kind == 'skill' else 'mcp_servers'
            validators.require(not any(e['name'] == name for e in manifest[collection]),
                               'conflict: manifest already owns missing destination')
        validators.label(owner)
        for kind, source in pending:
            payload, files = (None, texts(source)) if kind == 'skill' else (json.loads(source.read_text(encoding='utf-8')), [])
            screen(kind, payload, files, approve_risky, root)
        for kind, source in pending:
            import_item(argparse.Namespace(command='import-' + kind, path=str(source), owner=owner,
                                           origin='agents-sync', name=None, approve_risky=approve_risky), root)
    print('agents import complete; copied items:', len(pending))
    return 0


def export_project(target, root, gateway_url=None, check=False):
    target, root = project(target), project(root)
    manifest = validators.layered(root, validators.manifest(load(root / 'manifest.json')))
    original = config(target)
    value = copy.deepcopy(original)
    servers = {}
    for entry in manifest['mcp_servers']:
        source = repo_file(root, entry['path'])
        validators.require(validators.digest(source) == entry['sha256'], 'server hash mismatch')
        server = validators.mcp(load(source))
        validators.require(server['name'] == entry['name'], 'server name mismatch')
        rendered = render_server(server, root)
        rendered['transport'] = 'http' if rendered.pop('type') == 'streamable-http' else 'stdio'
        servers[entry['name']] = rendered
    if gateway_url:
        gateway = {'name': 'agentlocker-gateway', 'description': 'Shared gateway',
                   'transport': 'http', 'url': gateway_url}
        validators.mcp(gateway)
        render_server(gateway)
        validators.require('agentlocker-gateway' not in servers, 'gateway name conflict')
        servers['agentlocker-gateway'] = {'transport': 'http', 'url': gateway_url}
    for name, server in servers.items():
        existing = value['mcp']['servers'].get(name)
        if name in value['mcp']['servers']:
            equivalent = existing == server
            if not equivalent:
                try:
                    normalized = render_server(parse_server(name, existing), root)
                    normalized['transport'] = 'http' if normalized.pop('type') == 'streamable-http' else 'stdio'
                    equivalent = normalized == server
                except (ValueError, TypeError, KeyError):
                    equivalent = False
            validators.require(equivalent,
                               'conflict: upstream server differs; reconcile explicitly before retrying')
        else:
            value['mcp']['servers'][name] = server
    public_registry(value)
    pending = []
    for entry in manifest['skills']:
        source = skill_package(repo_file(root, entry['path']))
        validators.require(validators.digest(source) == entry['sha256'], 'skill hash mismatch')
        destination = target / '.agents/skills' / entry['name']
        if not unchanged(destination, source):
            pending.append((source, destination))
    path = ordinary(target / '.agents/agents.json')
    changed = not path.exists() or original != value or bool(pending)
    if check:
        return 2 if changed else 0
    for source, destination in pending:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
    if not path.exists() or original != value:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_output(path, json.dumps(value, indent=2) + '\n')
    print('agents export complete; source definitions only, Store values never resolved')
    return 0


def public_registry(value):
    def inspect(item, sensitive=False):
        if isinstance(item, dict):
            for key, child in item.items():
                inspect(child, bool(SENSITIVE.search(key)))
        elif isinstance(item, list):
            for child in item:
                inspect(child, sensitive)
        elif isinstance(item, str):
            validators.require(not sensitive or secret_reference(item),
                               'literal credentials refused; use whole variable placeholders')
            for match in re.finditer(r'\$\{[^}]*\}', item):
                validators.require(VARIABLE.fullmatch(match[0]), 'defaulted templates may contain secrets')
            validators.require(not any(kind != 'home-path'
                                       for _, kind in findings(item)), 'secret-looking value refused')
    for source in value['mcp']['servers'].values():
        validators.require(isinstance(source, dict), 'invalid upstream server')
        inspect(source)
        if 'url' in source:
            render_server({'transport': 'http', 'url': source['url']})
        for field in ('env', 'headers'):
            for literal in source.get(field, {}).values():
                validators.require(isinstance(literal, str) and
                                   (secret_reference(literal) or len(literal) < 24),
                                   'secret-looking environment or header value refused')
        args = source.get('args', [])
        validators.require(not any(isinstance(arg, str) and arg.startswith('-')
                                   and SENSITIVE.search(arg.split('=')[0]) for arg in args),
                           'credential arguments require upstream sync independently')


def confined_tree(root):
    for parent, directories, files in os.walk(root, followlinks=False):
        directories[:] = [name for name in directories if name != '.git']
        for name in directories + files:
            path = Path(parent) / name
            if path.is_symlink():
                validators.require(path.resolve().is_relative_to(root), 'sync tree links outside target')


def drive(target, root, binary='agents', throwaway_root=None, gateway_url=None, check=False):
    target, root = project(target), project(root)
    sandbox = ordinary(throwaway_root or root / '.scratch/agents-sync-home')
    validators.require(sandbox.is_relative_to(root / '.scratch') and sandbox != root / '.scratch',
                       'throwaway root must be a dedicated directory under locker scratch')
    validators.require(not target.is_relative_to(sandbox) and not sandbox.is_relative_to(target),
                       'project and throwaway HOME must be separate')
    confined_tree(target)
    confined_tree(sandbox)
    validators.require(not (target / '.agents/local.json').exists(),
                       'local overrides may materialize credentials; use a separate placeholder-only project '
                       'without local.json, or sync upstream independently')
    value = config(target)
    public_registry(value)
    validators.require(value.get('instructions', {}).get('path', 'AGENTS.md') == 'AGENTS.md',
                       'sync requires project-local AGENTS.md instructions')
    executable = shutil.which(str(binary))
    validators.require(executable, 'install the pinned agents CLI locally or supply --agents-bin')
    executable = str(Path(executable).resolve())
    result = export_project(target, root, gateway_url, check)
    if result:
        print('agents export drift detected; run agents-export before checking upstream drift')
        return result
    environment = {'PATH': os.environ.get('PATH', os.defpath), 'LANG': 'C.UTF-8', 'HOME': str(sandbox),
                   'AGENTS_HOME_DIR': str(sandbox), 'CI': '1', 'NO_COLOR': '1'}
    for key, relative in (('XDG_CONFIG_HOME', 'config'), ('XDG_CACHE_HOME', 'cache'),
                          ('XDG_DATA_HOME', 'data'), ('XDG_STATE_HOME', 'state'), ('TMPDIR', 'tmp')):
        path = ordinary(sandbox / relative)
        if not check:
            path.mkdir(parents=True, exist_ok=True)
        environment[key] = str(path)
    command = [executable, '--no-update-check', 'sync', '--path', str(target)]
    if check:
        command.append('--check')
    completed = subprocess.run(command, cwd=target, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    print('agents sync exit:', completed.returncode, '(upstream output suppressed to protect credentials)')
    return completed.returncode if completed.returncode >= 0 else 1


def register(commands, root):
    for operation in ('import', 'export', 'sync'):
        command = commands.add_parser('agents-' + operation, help=operation.capitalize() + ' through amtiYo/agents.')
        command.add_argument('--target', required=True)
        command.add_argument('--locker-root', type=Path, default=root)
        if operation == 'import':
            command.add_argument('--owner', default='agentlocker')
            command.add_argument('--approve-risky', action='store_true', help='Import even though the safety scan flagged it for review. Embedded secrets are always refused.')
        else:
            command.add_argument('--gateway-url')
        if operation == 'sync':
            command.add_argument('--agents-bin', default='agents')
            command.add_argument('--throwaway-root', type=Path)
            command.add_argument('--check', action='store_true')
        command.set_defaults(action=run)


def run(args):
    try:
        if args.command == 'agents-import':
            return import_project(args.target, args.locker_root, args.owner, getattr(args, 'approve_risky', False))
        if args.command == 'agents-export':
            return export_project(args.target, args.locker_root, args.gateway_url)
        return drive(args.target, args.locker_root, args.agents_bin, args.throwaway_root,
                     args.gateway_url, args.check)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError) as error:
        from hub import Refused
        if isinstance(error, Refused):
            print(error, file=sys.stderr)
        elif isinstance(error, ValueError):
            print('agents connector refused: ' + str(error))
        else:
            print('agents connector failed: invalid definition, unsupported settings, unsafe target or CLI failure')
        return 1
