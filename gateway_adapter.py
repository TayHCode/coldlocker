import hashlib
import json
import os
import re
import time

from gateway_render import placeholder, render, source_path_bindings, write_output
from store import FileStore, ordinary_path
from ui_auth import Authentication


VERSION = '1.1.7'
LOOPBACK = '.'.join(('127', '0', '0', '1'))
VARIABLE = re.compile(r'\$\{AGENTLOCKER_SECRET_([0-9A-F]+)\}')
SKIPPED_NEXT_STEP = 'set it on the Sign in screen'


def private_directory(path):
    ordinary_path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def save(path, value):
    ordinary_path(path)
    write_output(path, json.dumps(value, indent=2) + '\n')
    path.chmod(0o600)


def oauth_name(server, path):
    identity = json.dumps([server, path], separators=(',', ':'))
    return 'gw-' + hashlib.sha256(identity.encode()).hexdigest()[:48]


def stash(store, server, value, path=()):
    if isinstance(value, dict):
        return {k: stash(store, server, v, (*path, k)) for k, v in value.items()}
    if isinstance(value, list):
        return [stash(store, server, v, (*path, i)) for i, v in enumerate(value)]
    if isinstance(value, str) and value:
        name = oauth_name(server, path)
        store.put(name, value)
        return {'storeRef': 'secret://' + name}
    return value


def restore(store, value):
    if isinstance(value, dict):
        if set(value) == {'storeRef'}:
            return store.resolve(value['storeRef'])
        return {k: restore(store, v) for k, v in value.items()}
    if isinstance(value, list):
        return [restore(store, v) for v in value]
    return value


def sync(settings, store):
    count = 0
    for server, config in settings['mcpServers'].items():
        if 'oauth' in config:
            tree = stash(store, server, config['oauth'])
            store.put(oauth_name(server, None), json.dumps(tree))
            count += 1
    return count


def seed(settings, store):
    for server, config in settings['mcpServers'].items():
        name = oauth_name(server, None)
        if store.has(name):
            config['oauth'] = restore(store, json.loads(store.resolve('secret://' + name)))
    return settings


def environment(settings, store):
    allowed = {'PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TMPDIR', 'SSL_CERT_FILE',
               'SSL_CERT_DIR', 'NODE_EXTRA_CA_CERTS', 'HTTP_PROXY', 'HTTPS_PROXY',
               'NO_PROXY', 'http_proxy', 'https_proxy', 'no_proxy'}
    result = {k: v for k, v in os.environ.items() if k in allowed}
    for encoded in VARIABLE.findall(json.dumps(settings)):
        name = bytes.fromhex(encoded).decode()
        result['AGENTLOCKER_SECRET_' + encoded] = store.resolve('secret://' + name)
    return result


def unset_references(settings, store):
    result = {}
    for server, config in settings['mcpServers'].items():
        names = {bytes.fromhex(encoded).decode() for encoded in VARIABLE.findall(json.dumps(config))}
        missing = sorted(name for name in names if not store.has(name))
        if missing:
            result[server] = missing
    return result


def drop_unset(settings, store):
    unset = unset_references(settings, store)
    settings['mcpServers'] = {k: v for k, v in settings['mcpServers'].items() if k not in unset}
    return settings


def launch_settings(settings, source):
    """Resolve machine-local stdio paths only in the protected gateway settings."""
    settings = json.loads(json.dumps(settings))
    path_store = FileStore(source)
    for name, binding in source_path_bindings(source).items():
        config = settings.get('mcpServers', {}).get(name)
        if config is None:
            continue
        if not isinstance(config, dict):
            raise ValueError('invalid server settings')
        command = binding.get('command')
        if command:
            resolved = path_store.resolve(command['reference'])
            if not resolved.startswith('/') and not re.match(r'^[A-Za-z]:[\\/]', resolved):
                raise ValueError('invalid source command binding')
            if config.get('command') == command['portable']:
                config['command'] = resolved
            elif config.get('command') != resolved:
                raise ValueError('source command binding changed; rescan and import the server')
        args = binding.get('args', {})
        if args:
            values = config.get('args', [])
            if not isinstance(values, list):
                raise ValueError('invalid server arguments')
            for index, argument in args.items():
                position = int(index)
                if position >= len(values):
                    raise ValueError('source argument binding changed; rescan and import the server')
                resolved = path_store.resolve(argument['reference'])
                candidate = resolved.partition('=')[2] if resolved.startswith('-') and '=' in resolved else resolved
                if not candidate.startswith('/') and not re.match(r'^[A-Za-z]:[\\/]', candidate):
                    raise ValueError('invalid source argument binding')
                if values[position] == argument['portable']:
                    values[position] = resolved
                elif values[position] != resolved:
                    raise ValueError('source argument binding changed; rescan and import the server')
            config['args'] = values
    return settings


def skipped(root, store):
    return unset_references(render(root), store)


def ui_bindings(settings, source, store):
    authentication = Authentication(source, store)
    for name, config in settings['mcpServers'].items():
        binding = authentication.metadata(name)
        if not binding:
            continue
        server = authentication.server(name)
        if (config.get('type') != 'streamable-http' or binding.get('resource') != config.get('url')
                or binding.get('resource') != server['url']):
            raise ValueError('UI binding resource changed; reconnect in setup')
        if binding.get('method') not in ('bearer', 'oauth'):
            raise ValueError('invalid UI binding method')
        if binding['method'] == 'oauth' and binding['expires_at'] <= time.time() + 60:
            authentication.refresh(name)
            binding = authentication.metadata(name)
        headers = dict(config.get('headers', {}))
        for header, target in binding['headers'].items():
            if not isinstance(header, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", header):
                raise ValueError('invalid binding header')
            value = store.resolve('secret://' + target)
            if not value or any(c in value for c in '\r\n\x00'):
                raise ValueError('invalid binding credential')
            headers = {key: value for key, value in headers.items() if key.lower() != header.lower()}
            headers[header] = placeholder('secret://' + target)
        config['headers'] = headers
        config.pop('oauth', None)
    return settings


def prepare(root, source):
    store = FileStore(root)
    runtime = private_directory(private_directory(root / '.hub-store') / 'gateway')
    path = ordinary_path(runtime / 'mcp_settings.json')
    previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if previous:
        sync(previous, store)
    settings = {**previous, **drop_unset(ui_bindings(seed(render(source), store), source, store), store)}
    settings['users'] = previous.get('users', [])
    settings['systemConfig'] = previous.get('systemConfig', {
        'oauthServer': {'enabled': False},
        'routing': {'enableBearerAuth': False, 'skipAuth': False},
    })
    save(path, settings)
    save(runtime / 'source.json', {'path': str(source.resolve())})
    return runtime


def redact_log(store, text):
    text = re.sub(r'https?://[^\s"\'<>]+[?][^\s"\'<>]*', '[REDACTED_URL]', text)
    text = re.sub(r'(?i)(?<![A-Za-z0-9_])(["\']?(?:client[_-]?id|client[_-]?secret|code[_-]?verifier|access[_-]?token|refresh[_-]?token|state)["\']?\s*[:=]\s*)[^,\s}]+',
                  r'\1[REDACTED]', text)
    text = store.redact(text)
    fragments = {part for name in store.names()
                 for part in store.resolve('secret://' + name).splitlines() if part}
    for part in sorted(fragments, key=len, reverse=True):
        text = text.replace(part, '[REDACTED]')
    return text


class BindingRefresher:
    def __init__(self, source, store, interval=30, lead=60, log=lambda line: None, stopped=lambda: False):
        self.authentication = Authentication(source, store)
        self.interval = interval
        self.lead = lead
        self.log = log
        self.retries = {}
        self.stopped = stopped

    def check(self, settings, now=None):
        now = time.monotonic() if now is None else now
        changed = False
        for name, config in settings['mcpServers'].items():
            if self.stopped():
                break
            delay, deadline = self.retries.get(name, (self.interval, 0))
            if now < deadline:
                continue
            try:
                binding = self.authentication.metadata(name)
                if not binding or binding.get('method') != 'oauth':
                    continue
                server = self.authentication.server(name)
                if (config.get('type') != 'streamable-http' or binding['resource'] != config.get('url')
                        or binding['resource'] != server['url']):
                    raise ValueError('binding resource changed')
                if binding['expires_at'] > time.time() + self.lead:
                    continue
                before = {key: self.authentication.store.resolve('secret://' + target)
                          for key, target in binding['headers'].items()}
                self.authentication.refresh(name)
                after = self.authentication.metadata(name)
                headers = {key: self.authentication.store.resolve('secret://' + target)
                           for key, target in after['headers'].items()}
                changed = changed or before != headers
                self.retries[name] = (self.interval, now + self.interval)
                self.log('[auto-refresh] OAuth binding refreshed; credentials stored\n')
            except (OSError, ValueError, KeyError, TypeError):
                self.retries[name] = (min(delay * 2, max(300, self.interval)), now + delay)
                self.log('[auto-refresh] refresh failed; credentials retained; retry in ' + str(delay) + 's\n')
        return changed
