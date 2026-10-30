import json
import os
from pathlib import Path
import re
import uuid

from checks import validators
from gateway_render import render_server
import safety
from store import ordinary_path


LIMIT = 1024 * 1024
CONFIG_NAMES = {'.mcp.json', '.claude.json', 'mcp.json', 'config.toml'}
COMMON_SKILLS = ('skills', '.agents/skills', '.claude/skills', '.codex/skills', '.cursor/skills')


def json_object(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            validators.require(key not in result, 'duplicate field')
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=unique)


def selected_path(root, relative='.'):
    root = Path(os.path.abspath(root))
    path = Path(os.path.abspath(root / relative))
    path.relative_to(root)
    ordinary_path(path, root)
    path.resolve(strict=True).relative_to(root.resolve(strict=True))
    return path


def read_file(root, relative, names):
    path = selected_path(root, relative)
    validators.require(path.name in names and path.is_file(), 'unexpected input file')
    with path.open('rb') as stream:
        content = stream.read(LIMIT + 1)
    validators.require(len(content) <= LIMIT, 'input too large')
    return content.decode('utf-8')


def package_bounds(path):
    count = 0
    for parent, directories, files in os.walk(path, followlinks=False):
        for name in files:
            count += 1
            validators.require(count <= 1000 and (Path(parent) / name).lstat().st_size <= LIMIT, 'package too large')


def skill_package_texts(path):
    for parent, directories, files in os.walk(path, followlinks=False):
        for name in directories + files:
            item = Path(parent) / name
            validators.require(not item.is_symlink(), 'symlinks are forbidden')
    package_bounds(path)
    result = []
    for parent, directories, files in os.walk(path, followlinks=False):
        for name in files:
            item = Path(parent) / name
            validators.require(item.is_file(), 'ordinary files required')
            result.append((item.relative_to(path).as_posix(), item.read_bytes().decode('utf-8', errors='replace')))
    return result


def skill_package(path, expected_name=None, files=None):
    files = skill_package_texts(path) if files is None else files
    for _, text in files:
        validators.safe_fragment(text)
    validators.skill(path, path.name if expected_name is None else expected_name)
    return path


def discover_skills(root):
    root = selected_path(root)
    validators.require(root.is_dir(), 'directory required')
    candidates = [root]
    for relative in ('.', *COMMON_SKILLS):
        directory = root if relative == '.' else root / relative
        if not directory.exists():
            continue
        directory = selected_path(root, relative)
        validators.require(directory.is_dir(), 'directory required')
        children = list(directory.iterdir())
        validators.require(len(children) <= 1000, 'too many folders')
        candidates.extend(child for child in children if child.is_dir() and not child.is_symlink())
    found = []
    for path in sorted(set(candidates)):
        if (path / 'SKILL.md').exists():
            skill_package(selected_path(root, path.relative_to(root)))
            found.append({'name': path.name, 'path': path.relative_to(root).as_posix()})
    return found


def parse_mcp(text, filename):
    validators.require(filename in CONFIG_NAMES, 'unsupported configuration')
    if filename == 'config.toml':
        try:
            import tomllib
        except ImportError:
            raise ValueError('TOML import requires Python 3.11 or later') from None
        value = tomllib.loads(text)
        groups = [value.get('mcp_servers', {})]
    else:
        value = json_object(text)
        groups = [value.get('mcpServers', {})]
        projects = value.get('projects', {})
        validators.require(isinstance(projects, dict), 'invalid projects')
        groups.extend(project.get('mcpServers', {}) for project in projects.values() if isinstance(project, dict))
    servers = []
    secrets = {}
    credential_values = set()
    seen = set()
    for group in groups:
        validators.require(isinstance(group, dict), 'invalid server collection')
        for name, source in group.items():
            if filename == 'config.toml':
                name = re.sub('[^a-z0-9-]+', '-', str(name).lower()).strip('-')[:63] or 'unnamed'
            validators.name(name)
            validators.require(name not in seen and isinstance(source, dict), 'duplicate or invalid server')
            seen.add(name)
            kind = source.get('type', source.get('transport', 'http' if 'url' in source else 'stdio'))
            validators.require(kind in ('stdio', 'http', 'sse', 'streamable-http'), 'unsupported transport')
            transport = 'stdio' if kind == 'stdio' else 'http'
            server = {'name': name, 'description': 'Imported server', 'transport': transport}
            if transport == 'stdio':
                server.update(command=source.get('command'), args=source.get('args', []))
            else:
                server['url'] = source.get('url')
            for field, aliases in (('env', ('env',)), ('headers', ('headers', 'http_headers'))):
                mappings = [source[alias] for alias in aliases if alias in source]
                validators.require(len(mappings) <= 1, 'ambiguous mapping')
                if not mappings:
                    continue
                mapping = mappings[0]
                validators.require(isinstance(mapping, dict), 'invalid mapping')
                server[field] = {}
                for key, literal in mapping.items():
                    validators.require(isinstance(literal, str), 'invalid credential')
                    if not literal:
                        server[field][key] = ''
                        continue
                    reference = 'import-' + uuid.uuid4().hex
                    server[field][key] = 'secret://' + reference
                    secrets[reference] = literal
                    if (re.search(r'token|secret|key|password|authorization|credential', key, re.I)
                            or safety.secret_findings([('environment', literal)])):
                        credential_values.add(literal)
            validators.require(not any(source.get(k) for k in ('bearer_token_env_var', 'env_http_headers', 'env_vars')), 'environment indirection requires manual migration')
            validators.mcp(server)
            render_server(server)
            servers.append(server)
    validators.require(bool(servers), 'no servers found')
    public = []
    for server in servers:
        public.extend(value for key, value in server.items() if key not in ('env', 'headers', 'args'))
        public.extend(server.get('args', []))
        for field in ('env', 'headers'):
            public.extend(server.get(field, {}))
    validators.require(not any(value in field for value in credential_values for field in public),
                       'credentials repeated outside secret fields')
    return servers, secrets


def parse_routine(text):
    value = json_object(text)
    validators.require(isinstance(value, dict), 'routine object required')
    value['enabled'] = False
    validators.routine(value)
    validators.relative_path(value['prompt'])
    validators.require(len(Path(value['prompt']).parts) == 1 and value['prompt'] != 'routine.json', 'prompt must be a sibling file')
    return value


def parse_metadata(text, kind):
    value = json_object(text)
    return validators.metadata(value, kind)
