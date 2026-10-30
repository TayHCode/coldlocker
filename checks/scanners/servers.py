import copy
import hashlib
import json
import re
from urllib.parse import urlsplit

from checks import validators
from checks.scan import PATTERNS
from importers import json_object, parse_mcp

from .common import ERRORS, digest, disabled_names, encoded, read, selected_runner, slug


SECRET_KEY = re.compile(r'token|secret|key|password|authorization|credential', re.I)
SECRET_SHAPES = [pattern for name, pattern in PATTERNS.items() if name != 'home-path']


def environment_headers(name, source):
    if 'url' not in source:
        return source, {}
    source = dict(source)
    named = {}
    if source.get('bearer_token_env_var'):
        named['Authorization'] = source['bearer_token_env_var']
    named.update(source.get('env_http_headers') or {})
    source.pop('bearer_token_env_var', None)
    source.pop('env_http_headers', None)
    found = {}
    for key, variable in named.items():
        validators.require(isinstance(variable, str) and bool(variable), 'invalid environment header')
        found[key] = 'env:' + variable
    for field in ('headers', 'http_headers'):
        if isinstance(source.get(field), dict):
            kept = {}
            for key, value in source[field].items():
                if isinstance(value, str) and re.search(r'\$\{|\$env\{|\{env:', value):
                    found[key] = 'template:' + value
                else:
                    kept[key] = value
            source[field] = kept
    present = {key.lower() for field in ('headers', 'http_headers') for key in source.get(field, {})}
    validators.require(len(found) == len({key.lower() for key in found}) and not present & {key.lower() for key in found}, 'ambiguous header')
    return source, {key: 'scan-' + hashlib.sha256(encoded([name, 'headers', key, origin])).hexdigest()[:32] for key, origin in found.items()}


class ServerMixin:
    def server(self, agent, path, name, source, opencode=False, ownership='user', disabled=()):
        try:
            validators.require(isinstance(source, dict), 'server object required')
            validators.require(name not in disabled and source.get('enabled', True) is True, 'disabled server')
            source = {k: v for k, v in source.items() if k != 'enabled'}
            for field in ('env', 'environment', 'headers', 'http_headers'):
                mapping = source.get(field, {})
                if isinstance(mapping, dict):
                    for key, value in mapping.items():
                        if isinstance(value, str) and value and not validators.SECRET.fullmatch(value) and (
                                SECRET_KEY.search(key) or any(pattern.search(value) for pattern in SECRET_SHAPES)):
                            self.credential_values.setdefault(value, f'{name} {"env" if field in ("env", "environment") else "header"} {key}')
            if opencode:
                source = dict(source)
                kind = source.pop('type', None)
                if kind == 'local':
                    command = source.pop('command')
                    validators.require(isinstance(command, list) and bool(command), 'command array required')
                    source.update(type='stdio', command=command[0], args=command[1:])
                elif kind == 'remote':
                    source['type'] = 'http'
                else:
                    raise ValueError('unsupported type')
                if 'environment' in source:
                    source['env'] = source.pop('environment')
            original_command = source.get('command')
            original_args = list(source['args']) if isinstance(source.get('args'), list) else []
            path_bindings = {}
            if isinstance(source.get('command'), str):
                portable = self.tilde(source['command'])
                binding = self.source_path_binding(original_command, portable)
                if binding:
                    path_bindings['command'] = binding
                source = dict(source, command=portable)
            if isinstance(source.get('args'), list):
                source = dict(source, args=[self.tilde(a) if isinstance(a, str) else a for a in source['args']])
            stable = {}
            if isinstance(source.get('args'), list):
                args = list(source['args'])
                credential_args = validators.credential_args(args)
                for index, (prefix, value) in credential_args.items():
                    reference = 'scan-' + hashlib.sha256(encoded([name, 'args', index, value])).hexdigest()[:32]
                    stable[reference] = value
                    args[index] = prefix + 'secret://' + reference
                    self.credential_values.setdefault(value, f'{name} argument')
                bound_args = {}
                for index, original in enumerate(original_args):
                    if index in credential_args or index >= len(args) or not isinstance(original, str):
                        continue
                    binding = self.source_path_binding(original, args[index])
                    if binding:
                        bound_args[index] = binding
                if bound_args:
                    path_bindings['args'] = bound_args
                source = dict(source, args=args)
            source, indirect = environment_headers(name, source)
            for field in ('env', 'headers', 'http_headers'):
                for value in source.get(field, {}).values():
                    validators.require(not re.search(r'\$\{|\$env\{|\{env:', value), 'environment indirection')
            review_source = copy.deepcopy(source)
            for field in ('env', 'headers', 'http_headers'):
                mapping = review_source.get(field, {})
                if isinstance(mapping, dict):
                    for key, value in mapping.items():
                        if isinstance(value, str) and value and (
                                SECRET_KEY.search(key) or any(pattern.search(value) for pattern in SECRET_SHAPES)):
                            mapping[key] = 'secret://stored-credential'
            servers, secrets = parse_mcp(json.dumps({'mcpServers': {slug(name): source}}), '.mcp.json')
            server = servers[0]
            for field in ('env', 'headers'):
                for key, ref in server.get(field, {}).items():
                    if ref[9:] not in secrets:
                        continue
                    value = secrets[ref[9:]]
                    if validators.SECRET.fullmatch(value):
                        server[field][key] = value
                    else:
                        reference = 'scan-' + hashlib.sha256(encoded([name, field, key, value])).hexdigest()[:32]
                        stable[reference] = value
                        server[field][key] = 'secret://' + reference
            if indirect:
                server['headers'] = {**server.get('headers', {}), **{key: 'secret://' + ref for key, ref in indirect.items()}}
                validators.mcp(server)
            upstream = None
            if server['transport'] == 'http':
                url = server['url']
                parsed = urlsplit(url)
                validators.require(not parsed.username and not parsed.password and not parsed.query and not parsed.fragment, 'private URL')
                kind = 'executor' if parsed.hostname in ('127.0.0.1', 'localhost', '::1') and parsed.port == 4788 else 'composio' if parsed.hostname and (parsed.hostname == 'composio.dev' or parsed.hostname.endswith('.composio.dev')) else 'mcphub' if 'mcphub' in name.lower() or parsed.port == 43871 else None
                if kind == 'executor' and parsed.hostname != 'localhost':
                    from executor_connector import endpoint_url
                    endpoint_url(url)
                if kind == 'composio':
                    from composio_connector import definition
                    definition(name, url)
                if kind:
                    upstream = {'kind': kind, 'endpoint': url}
                connection = {'name': slug(name), 'provider': kind or agent, 'endpoint': url,
                              'auth_status': 'needs-auth' if name in self.needs_auth or indirect else 'configured' if server.get('headers') else 'unknown'}
                validators.metadata(connection, 'connection')
                self.add(agent, 'connection', name, path, connection, upstream=upstream, ownership=ownership)
            if upstream and upstream['kind'] == 'executor':
                self.executor_sources(path, server, stable)
            self.add(agent, 'mcp_server', name, path, server, secrets=stable, upstream=upstream, ownership=ownership,
                     review_payload=review_source, path_bindings=path_bindings,
                     flags=['suggest-gateway-backend-executor'] if upstream and upstream['kind'] == 'executor' else [])
        except ERRORS:
            self.failed(agent, 'mcp_server', path, name, ownership, definition=source if isinstance(source, dict) else None)

    def executor_sources(self, path, server, secrets):
        endpoint = server['url']
        if not self.live or self.runner is not selected_runner() or endpoint in self.executor_endpoints:
            return
        self.executor_endpoints.add(endpoint)
        try:
            from executor_connector import Session, endpoint_url
            from executor_sources import invoke
            endpoint_url(endpoint)
            headers = {k: secrets[v[9:]] for k, v in server.get('headers', {}).items()}
            config = {'backend': 'executor', 'url': endpoint, 'headers': {}}
            with Session(self.root, config=config, headers=headers) as session:
                integrations = invoke(session, 'executor.coreTools.integrations.list', {})['integrations']
            validators.require(isinstance(integrations, list) and len(integrations) <= 1000, 'invalid source list')
            for entry in integrations:
                try:
                    name = slug('executor-' + entry['slug'])
                    data = {'name': name, 'provider': 'executor', 'endpoint': endpoint, 'auth_status': 'unknown'}
                    validators.metadata(data, 'connection')
                    self.add('system', 'connection', name, path, data,
                             upstream={'kind': 'executor', 'endpoint': endpoint}, flags=['executor-owned'])
                except ERRORS:
                    self.failed('system', 'connection', path)
        except ERRORS:
            return

    def config(self, agent, path, opencode=False, ownership='user', disabled=()):
        if not path.exists() and not path.is_symlink():
            return
        try:
            text = read(path).decode()
            if path.suffix == '.toml':
                try:
                    import tomllib
                except ImportError:
                    raise ValueError('TOML requires Python 3.11+') from None
                value = tomllib.loads(text)
                groups = [(value.get('mcp_servers', {}), path, disabled)]
            else:
                if path.suffix == '.jsonc':
                    text = re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/', lambda m: m[0] if m[0].startswith('"') else '', text)
                    text = re.sub(r',\s*([}\]])', r'\1', text)
                value = json_object(text)
                groups = [(value.get('mcp' if opencode else 'mcpServers', {}), path, disabled)]
                projects = value.get('projects', {})
                if isinstance(projects, dict):
                    groups += [(p.get('mcpServers', {}), path.with_name(path.name + '#project-' + digest(encoded(key))[:12]),
                                disabled_names(p, 'disabledMcpServers', 'disabledMcpjsonServers'))
                               for key, p in projects.items() if isinstance(p, dict)]
            for group, source_path, off in groups:
                if not isinstance(group, dict):
                    self.failed(agent, 'mcp_server', path)
                    continue
                for name, source in group.items():
                    self.server(agent, source_path, name, source, opencode, ownership, off)
            if isinstance(value.get('plugins'), dict):
                for name, entry in value['plugins'].items():
                    self.plugin(agent, path, name, entry if isinstance(entry, dict) else {'enabled': bool(entry)})
            if opencode:
                for plugin in value.get('plugin', []):
                    self.plugin(agent, path, plugin, {'source': plugin})
        except ERRORS:
            self.failed(agent, 'mcp_server', path)

    def connections(self, agent, path):
        if not path.is_file():
            return
        try:
            value = json_object(read(path).decode())
            entries = value.get('connections', value.get('connectors', []))
            if isinstance(entries, dict):
                entries = [dict(v, name=k) for k, v in entries.items() if isinstance(v, dict)]
            for entry in entries:
                try:
                    name = slug(entry['name'])
                    endpoint = entry.get('endpoint', entry.get('url', ''))
                    data = {'name': name, 'provider': slug(entry.get('provider', agent)), 'endpoint': endpoint,
                            'auth_status': entry.get('auth_status', 'unknown')}
                    validators.metadata(data, 'connection')
                    self.add(agent, 'connection', name, path, data, upstream=None)
                except ERRORS:
                    self.failed(agent, 'connection', path)
        except ERRORS:
            self.failed(agent, 'connection', path)
