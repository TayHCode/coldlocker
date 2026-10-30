import ipaddress
import json
import re
import urllib.request
from urllib.parse import urlsplit

from calllog import META_KEY, SETUP_CHECK
from store import FileStore, ordinary_path, reference_name


DEFAULT_URL = 'http://' + '.'.join(('127', '0', '0', '1')) + ':4788/mcp'
LIMIT = 4 * 1024 * 1024


def endpoint_url(value):
    if not isinstance(value, str) or any(c.isspace() for c in value):
        raise ValueError('invalid Executor endpoint')
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or not parsed.port
            or not ipaddress.ip_address(parsed.hostname).is_loopback):
        raise ValueError('Executor requires a numeric loopback endpoint without credentials or queries')
    return value


def configuration(root):
    path = ordinary_path(root / 'config.json')
    value = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(value, dict):
        raise ValueError('invalid gateway configuration')
    gateway = value.get('gateway', {})
    if not isinstance(gateway, dict) or gateway.get('backend', 'mcphub') not in ('mcphub', 'executor'):
        raise ValueError('invalid gateway backend')
    if gateway.get('backend', 'mcphub') == 'mcphub':
        return {'backend': 'mcphub'}
    executor = gateway.get('executor', {})
    if not isinstance(executor, dict) or set(executor) - {'url', 'headers'}:
        raise ValueError('invalid Executor configuration')
    headers = executor.get('headers', {})
    if not isinstance(headers, dict):
        raise ValueError('invalid Executor headers')
    for name, ref in headers.items():
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9-]+', name)
                or name.lower() in ('host', 'content-type', 'accept', 'mcp-session-id', 'mcp-protocol-version',
                                    'content-length', 'transfer-encoding', 'connection')):
            raise ValueError('invalid Executor header')
        reference_name(ref)
    return {'backend': 'executor', 'url': endpoint_url(executor.get('url', DEFAULT_URL)), 'headers': headers}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Executor redirects are forbidden')


class Session:
    def __init__(self, root, config=None, headers=None):
        self.config = configuration(root) if config is None else config
        if self.config['backend'] != 'executor':
            raise ValueError('Executor backend is not selected')
        self.config['url'] = endpoint_url(self.config['url'])
        store = FileStore(root)
        self.headers = ({name: store.resolve(ref) for name, ref in self.config['headers'].items()}
                        if headers is None else dict(headers))
        if any('\r' in value or '\n' in value for value in self.headers.values()):
            raise ValueError('invalid Executor header value')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.session = None
        self.version = '2024-11-05'
        self.serial = 0

    def exchange(self, method, params=None, notification=False):
        self.serial += 1
        message = {'jsonrpc': '2.0', 'method': method}
        if not notification:
            message['id'] = self.serial
        message['params'] = {**(params or {}), '_meta': {META_KEY: SETUP_CHECK}}
        headers = {**self.headers, 'Content-Type': 'application/json',
                   'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': self.version}
        if self.session:
            headers['Mcp-Session-Id'] = self.session
        request = urllib.request.Request(self.config['url'], json.dumps(message).encode(), headers)
        with self.opener.open(request, timeout=5) as response:
            self.session = response.headers.get('Mcp-Session-Id', self.session)
            content_type = response.headers.get_content_type()
            if notification:
                return None
            if content_type == 'text/event-stream':
                data = []
                size = 0
                for raw in response:
                    size += len(raw)
                    if size > LIMIT:
                        raise ValueError('Executor response too large')
                    line = raw.decode().rstrip('\r\n')
                    if line.startswith('data:'):
                        data.append(line[5:].lstrip())
                    elif not line and data:
                        result = json.loads('\n'.join(data))
                        data = []
                        if isinstance(result, dict) and result.get('id') == self.serial:
                            break
                else:
                    raise ValueError('missing Executor response')
            else:
                data = response.read(LIMIT + 1)
                if len(data) > LIMIT:
                    raise ValueError('Executor response too large')
                result = json.loads(data)
        if (not isinstance(result, dict) or result.get('id') != self.serial
                or 'error' in result or not isinstance(result.get('result'), dict)):
            raise ValueError('Executor request failed')
        return result['result']

    def __enter__(self):
        try:
            result = self.exchange('initialize', {'protocolVersion': self.version, 'capabilities': {},
                'clientInfo': {'name': 'agentlocker', 'version': '4'}})
            self.version = result['protocolVersion']
            if not isinstance(self.version, str):
                raise ValueError('invalid Executor protocol version')
            self.exchange('notifications/initialized', notification=True)
            return self
        except (OSError, ValueError, KeyError, TypeError):
            self.__exit__()
            raise

    def __exit__(self, *args):
        if self.session:
            request = urllib.request.Request(self.config['url'], method='DELETE', headers={
                **self.headers, 'Mcp-Session-Id': self.session, 'MCP-Protocol-Version': self.version})
            try:
                self.opener.open(request, timeout=5).close()
            except (OSError, ValueError):
                pass

    def tools(self):
        tools = []
        cursors = set()
        params = {}
        for _ in range(100):
            page = self.exchange('tools/list', params)
            if not isinstance(page.get('tools'), list):
                raise ValueError('invalid Executor tools')
            for tool in page['tools']:
                if not isinstance(tool, dict) or not isinstance(tool.get('name'), str):
                    raise ValueError('invalid Executor tool')
                tools.append(tool)
            cursor = page.get('nextCursor')
            if not cursor:
                return tools
            if not isinstance(cursor, str) or cursor in cursors:
                raise ValueError('invalid Executor pagination')
            cursors.add(cursor)
            params = {'cursor': cursor}
        raise ValueError('too many Executor pages')


def status(root):
    config = configuration(root)
    try:
        with Session(root) as session:
            tools = session.tools()
        return {'backend': 'executor', 'status': 'running', 'url': config['url'],
                'tools': [FileStore(root).redact(tool['name']) for tool in tools]}
    except (OSError, ValueError, KeyError, TypeError):
        return {'backend': 'executor', 'status': 'unreachable', 'url': config['url'], 'tools': []}


def connection(root):
    config = configuration(root)
    current = status(root)
    hint = 'Start Executor separately: executor web --foreground; python3 gateway.py status'
    if current['status'] != 'running':
        return {**current, 'command': hint, 'snippets': {}}
    env_headers = {name: 'AGENTLOCKER_EXECUTOR_' + name.upper().replace('-', '_') for name in config['headers']}
    entry = {'url': config['url']}
    if env_headers:
        entry['headers'] = {name: '${' + env + '}' for name, env in env_headers.items()}
        hint += '; set client header environment variables privately (see Executor connector guide)'
    fragment = lambda value: json.dumps({'mcpServers': {'agentlocker-gateway': value}}, indent=2)
    codex = '[mcp_servers.agentlocker-gateway]\nurl = ' + json.dumps(config['url']) + '\n'
    if env_headers:
        codex += '[mcp_servers.agentlocker-gateway.env_http_headers]\n'
        codex += ''.join(json.dumps(name) + ' = ' + json.dumps(env) + '\n' for name, env in env_headers.items())
    return {**current, 'command': hint, 'snippets': {
        'Claude': fragment({'type': 'http', **entry}), 'Cursor': fragment(entry), 'Codex': codex}}


def command(root, name):
    if name == 'status':
        current = status(root)
        print(json.dumps(current))
        return 0 if current['status'] == 'running' else 1
    if name == 'url':
        print(configuration(root)['url'])
        return 0
    if name in ('start', 'stop', 'prepare', 'sync', 'refresh', 'install'):
        print('Executor is user-managed; no changes made. Start it separately with executor web --foreground.')
        return 0
    raise ValueError('unsupported Executor command')
