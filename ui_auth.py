import base64
import hashlib
from http.client import HTTPException
import json
import re
import secrets
import threading
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import hub
from checks import validators
from mcp_endpoint import reject_legacy_credential, repo_file
from store import reference_name


class DiscoveryMissing(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def endpoint(value):
    validators.require(isinstance(value, str), 'URL required')
    parts = urlsplit(value)
    validators.require(parts.scheme == 'https' or (parts.scheme == 'http' and parts.hostname == '127' + '.0.0.1'), 'secure endpoint required')
    validators.require(parts.hostname and not parts.username and not parts.password and not parts.fragment, 'invalid endpoint')
    return value


def request_json(url, data=None, headers=None):
    request = Request(endpoint(url), data=data, headers={'User-Agent': 'agentlocker/3', **(headers or {})})
    try:
        with build_opener(NoRedirect).open(request, timeout=10) as response:
            validators.require(response.headers.get_content_type() == 'application/json', 'JSON required')
            body = response.read(1024 * 1024 + 1)
    except HTTPError as error:
        missing = error.code == 404
        error.close()
        raise (DiscoveryMissing if missing else ValueError)('provider request failed') from None
    except HTTPException:
        raise ValueError('invalid provider response') from None
    validators.require(len(body) <= 1024 * 1024, 'response too large')
    result = json.loads(body)
    validators.require(isinstance(result, dict), 'object required')
    return result


def resource_challenge(resource):
    challenge = ''
    try:
        with build_opener(NoRedirect).open(Request(endpoint(resource), headers={'User-Agent': 'agentlocker/3'}), timeout=10) as response:
            challenge = response.headers.get('WWW-Authenticate', '')
    except HTTPError as error:
        try:
            validators.require(error.code in (401, 403, 404, 405, 406), 'resource probe failed')
            challenge = error.headers.get('WWW-Authenticate', '')
        finally:
            error.close()
    except HTTPException:
        raise ValueError('invalid resource response') from None
    match = re.search(r'\bresource_metadata="([^"\r\n]+)"', challenge, re.I)
    scope = re.search(r'\bscope="([^"\r\n]*)"', challenge, re.I)
    return (endpoint(match[1]) if match else None, scope[1] if scope else None)


def authorization_metadata(issuer):
    parts = urlsplit(issuer)
    validators.require(not parts.query, 'invalid issuer')
    paths = ['/.well-known/oauth-authorization-server' + parts.path,
             '/.well-known/openid-configuration' + parts.path,
             parts.path.rstrip('/') + '/.well-known/openid-configuration']
    for path in dict.fromkeys(paths):
        try:
            return request_json(urlunsplit((parts.scheme, parts.netloc, path, '', '')))
        except DiscoveryMissing:
            pass
    raise DiscoveryMissing('authorization metadata unavailable')


def entry_prefix(name):
    return 'mcp-' + hashlib.sha256(name.encode()).hexdigest()[:24]


class Authentication:
    def __init__(self, root, store):
        self.root = root
        self.store = store
        self.pending = {}
        self.results = {}
        self.current = {}
        self.lock = threading.Lock()

    def _discard(self, name):
        self.current.pop(name, None)
        self.pending = {key: flow for key, flow in self.pending.items() if flow['name'] != name}

    def _finish(self, flow, status):
        name = flow['name']
        if self.current.get(name) is flow:
            del self.current[name]
            self.results[name] = {'status': status, 'at': time.time()}

    def _expire(self, name=None):
        for key, flow in list(self.pending.items()):
            if (name is None or flow['name'] == name) and flow['deadline'] <= time.monotonic():
                del self.pending[key]
                self._finish(flow, 'expired')

    def definition(self, name):
        manifest = hub.read_manifest(self.root)
        entry = next((item for item in manifest['mcp_servers'] if item['name'] == name), None)
        validators.require(entry is not None, 'unknown server')
        return validators.mcp(reject_legacy_credential(hub.read_json(repo_file(self.root, entry['path']))))

    def server(self, name):
        server = self.definition(name)
        validators.require(server['transport'] == 'http', 'HTTP server required')
        endpoint(server['url'])
        return server

    def read(self, name, suffix):
        return self.store.resolve('secret://' + entry_prefix(name) + '-' + suffix)

    def metadata(self, name):
        key = entry_prefix(name) + '-binding'
        return json.loads(self.store.resolve('secret://' + key)) if self.store.has(key) else {}

    def write_metadata(self, name, value):
        self.store.put(entry_prefix(name) + '-binding', json.dumps(value))

    def headers(self, server):
        return {key: reference_name(value) for key, value in server.get('headers', {}).items()
                if value.startswith('secret://')}

    def status(self, server):
        name = server['name']
        refs = self.headers(server)
        binding = self.metadata(name)
        if binding and binding.get('resource') == server['url']:
            ready = all(self.store.has(ref) for ref in {**binding['headers'], **refs}.values())
            expired = binding.get('expires_at', float('inf')) <= time.time()
            state = 'expired' if expired else 'connected' if ready else 'needs-auth'
        elif binding:
            state = 'needs-auth'
        else:
            state = 'connected' if refs and all(self.store.has(ref) for ref in refs.values()) else 'needs-auth'
        return {'name': name, 'status': state, 'headers': list(refs) or ['Authorization'],
                'refreshable': binding.get('resource') == server['url'] and binding.get('method') == 'oauth' and self.store.has(entry_prefix(name) + '-refresh')}

    def check_value(self, value):
        validators.require(isinstance(value, str) and value and len(value) <= 16384 and not any(c in value for c in '\r\n\x00'), 'invalid credential')

    def argument_references(self, args):
        found = {}
        for index, arg in enumerate(args):
            if not isinstance(arg, str):
                continue
            flag, equals, tail = arg.partition('=')
            if flag.startswith('-') and equals and tail.startswith('secret://'):
                key, value = flag, tail
            elif arg.startswith('secret://'):
                before = args[index - 1] if index else ''
                key = before if isinstance(before, str) and before.startswith('-') and '=' not in before else f'arg {index}'
                value = arg
            else:
                continue
            found[key if key not in found else f'arg {index}'] = reference_name(value)
        return found

    def stdio_references(self, server):
        found = {key: reference_name(value) for field in ('env', 'headers')
                 for key, value in server.get(field, {}).items() if value.startswith('secret://')}
        return {**self.argument_references(server.get('args', [])), **found}

    def typed(self, name):
        key = entry_prefix(name) + '-typed'
        return json.loads(self.store.resolve('secret://' + key)) if self.store.has(key) else []

    def secret(self, name, key, value):
        server = self.definition(name)
        validators.require(server['transport'] == 'stdio', 'stdio server required')
        refs = self.stdio_references(server)
        validators.require(key in refs, 'unknown variable')
        self.check_value(value)
        self.store.put(refs[key], value)
        self.store.put(entry_prefix(name) + '-typed', json.dumps(sorted({*self.typed(name), refs[key]})))
        return {'status': 'saved'}

    def remove_secret(self, name, key):
        server = self.definition(name)
        validators.require(server['transport'] == 'stdio', 'stdio server required')
        refs = self.stdio_references(server)
        validators.require(key in refs, 'unknown variable')
        self.store.delete(refs[key])
        return {'status': 'removed'}

    def bearer(self, name, header, value, scheme):
        server = self.server(name)
        refs = self.headers(server)
        validators.require(header in (refs or {'Authorization': None}), 'unknown header')
        self.check_value(value)
        validators.require(scheme in ('bearer', 'raw'), 'invalid scheme')
        target = refs.get(header) or entry_prefix(name) + '-header'
        with self.lock:
            self.store.put(target, ('Bearer ' if scheme == 'bearer' else '') + value)
            self.write_metadata(name, {'method': 'bearer', 'resource': server['url'], 'headers': {**refs, header: target}})
            self._discard(name)
            self.results.pop(name, None)
            return {'status': self.status(server)['status']}

    def discover(self, resource):
        parts = urlsplit(resource)
        origin = urlunsplit((parts.scheme, parts.netloc, '', '', ''))
        metadata_url, scope = resource_challenge(resource)
        metadata_url = metadata_url or origin + '/.well-known/oauth-protected-resource' + parts.path
        try:
            resource_metadata = request_json(metadata_url)
        except DiscoveryMissing:
            resource_metadata = request_json(origin + '/.well-known/oauth-protected-resource')
        validators.require(resource_metadata.get('resource') == resource, 'resource mismatch')
        issuers = resource_metadata.get('authorization_servers')
        validators.require(isinstance(issuers, list) and issuers, 'authorization server required')
        issuer = endpoint(issuers[0])
        metadata = authorization_metadata(issuer)
        validators.require(metadata.get('issuer') == issuer, 'issuer mismatch')
        validators.require('S256' in metadata.get('code_challenge_methods_supported', []), 'PKCE required')
        for key in ('authorization_endpoint', 'token_endpoint'):
            endpoint(metadata[key])
        if scope is not None:
            resource_metadata['scopes_supported'] = scope.split(' ') if scope else []
        return resource_metadata, metadata

    def supports_oauth(self, name):
        url = self.server(name)['url']
        try:
            self.discover(url)
        except (ValueError, KeyError, TypeError, OSError):
            return False
        return True

    def start(self, name, redirect_uri, client_id=''):
        server = self.server(name)
        resource, metadata = self.discover(server['url'])
        scopes = resource.get('scopes_supported', [])
        validators.require(isinstance(scopes, list) and all(isinstance(scope, str) and re.fullmatch(r'[\x21\x23-\x5b\x5d-\x7e]+', scope) for scope in scopes), 'invalid scopes')
        scope = ' '.join(scopes)
        auth_method = 'none'
        client_secret = ''
        validators.require(isinstance(client_id, str), 'invalid client identifier')
        if not client_id and metadata.get('registration_endpoint'):
            methods = metadata.get('token_endpoint_auth_methods_supported', ['client_secret_basic'])
            auth_method = 'none' if 'none' in methods else 'client_secret_basic' if 'client_secret_basic' in methods else 'client_secret_post'
            validators.require(auth_method in methods, 'unsupported client authentication')
            client = request_json(metadata['registration_endpoint'], json.dumps({'client_name': 'agentlocker',
                'redirect_uris': [redirect_uri], 'grant_types': ['authorization_code', 'refresh_token'],
                'response_types': ['code'], 'token_endpoint_auth_method': auth_method}).encode(), {'Content-Type': 'application/json'})
            client_id = client['client_id']
            client_secret = client.get('client_secret', '')
        validators.require(isinstance(client_id, str) and client_id and len(client_id) <= 16384, 'client registration required')
        validators.require(auth_method in metadata.get('token_endpoint_auth_methods_supported', ['client_secret_basic']), 'client authentication unavailable')
        validators.require(auth_method == 'none' or isinstance(client_secret, str) and client_secret, 'client secret required')
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        flow = {'name': name, 'resource': server['url'], 'redirect_uri': redirect_uri, 'verifier': verifier,
                'client_id': client_id, 'client_secret': client_secret, 'auth_method': auth_method,
                'token_endpoint': metadata['token_endpoint'], 'scope': scope, 'deadline': time.monotonic() + 600}
        query = {'response_type': 'code', 'client_id': client_id, 'redirect_uri': redirect_uri, 'state': state,
                 'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),
                 'code_challenge_method': 'S256', 'resource': server['url']}
        if scope:
            query['scope'] = scope
        parts = urlsplit(metadata['authorization_endpoint'])
        validators.require(not parts.query, 'authorization endpoint query unsupported')
        with self.lock:
            self._expire()
            validators.require(sum(flow['name'] != name for flow in self.pending.values()) < 32, 'too many authorizations')
            self._discard(name)
            self.pending[state] = flow
            self.current[name] = flow
            self.results.pop(name, None)
        return {'url': metadata['authorization_endpoint'] + '?' + urlencode(query)}

    def exchange(self, flow, grant):
        data = {**grant, 'client_id': flow['client_id'], 'resource': flow['resource']}
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        if flow['auth_method'] == 'client_secret_basic':
            credentials = urlencode({'v': flow['client_id']})[2:] + ':' + urlencode({'v': flow['client_secret']})[2:]
            headers['Authorization'] = 'Basic ' + base64.b64encode(credentials.encode()).decode()
        elif flow['auth_method'] == 'client_secret_post':
            data['client_secret'] = flow['client_secret']
        return request_json(flow['token_endpoint'], urlencode(data).encode(), headers)

    def save(self, flow, tokens):
        name = flow['name']
        server = self.server(name)
        validators.require(server['url'] == flow['resource'], 'resource changed')
        access = tokens['access_token']
        validators.require(isinstance(access, str) and access and not any(c in access for c in '\r\n\x00'), 'invalid access token')
        validators.require(tokens.get('token_type', '').lower() == 'bearer', 'unsupported token type')
        expires = tokens.get('expires_in', 3600)
        validators.require(type(expires) in (int, float) and 0 < expires <= 31536000, 'invalid expiry')
        refs = self.headers(server)
        header = next((key for key in refs if key.lower() == 'authorization'), 'Authorization')
        target = refs.get(header) or entry_prefix(name) + '-header'
        prefix = entry_prefix(name)
        refresh = tokens.get('refresh_token')
        validators.require(refresh is None or isinstance(refresh, str) and refresh, 'invalid refresh token')
        self.store.put(prefix + '-access', access)
        self.store.put(target, 'Bearer ' + access)
        if refresh:
            self.store.put(prefix + '-refresh', refresh)
        elif 'verifier' in flow:
            self.store.delete(prefix + '-refresh')
        for suffix in ('client_id', 'client_secret'):
            if flow[suffix]:
                self.store.put(prefix + '-' + suffix.replace('_', '-'), flow[suffix])
        self.write_metadata(name, {'method': 'oauth', 'headers': {**refs, header: target}, 'resource': flow['resource'],
            'token_endpoint': flow['token_endpoint'], 'auth_method': flow['auth_method'], 'expires_at': time.time() + expires})

    def callback(self, query):
        values = parse_qs(query, keep_blank_values=True)
        validators.require(all(len(value) == 1 for value in values.values()), 'duplicate callback parameter')
        state = values.get('state', [''])[0]
        with self.lock:
            flow = self.pending.pop(state, None)
            validators.require(flow is not None, 'invalid state')
            if flow['deadline'] <= time.monotonic():
                self._finish(flow, 'expired')
                raise ValueError('authorization expired')
            if 'error' in values:
                self._finish(flow, 'denied')
                raise ValueError('authorization denied')
        try:
            validators.require(values.get('code', [''])[0], 'authorization code required')
            tokens = self.exchange(flow, {'grant_type': 'authorization_code', 'code': values['code'][0],
                                         'redirect_uri': flow['redirect_uri'], 'code_verifier': flow['verifier']})
            with self.lock:
                validators.require(self.current.get(flow['name']) is flow, 'authorization superseded')
                self.save(flow, tokens)
                self._finish(flow, 'saved')
        except (OSError, ValueError, TypeError, KeyError):
            with self.lock:
                self._finish(flow, 'failed')
            raise

    def outcome(self, name):
        with self.lock:
            self._expire(name)
            return self.results.get(name)

    def refresh(self, name):
        server = self.server(name)
        binding = self.metadata(name)
        validators.require(binding.get('method') == 'oauth' and binding['resource'] == server['url'], 'OAuth connection required')
        flow = {**binding, 'name': name, 'client_id': self.read(name, 'client-id'),
                'client_secret': self.read(name, 'client-secret') if binding['auth_method'] != 'none' else ''}
        tokens = self.exchange(flow, {'grant_type': 'refresh_token', 'refresh_token': self.read(name, 'refresh')})
        self.save(flow, tokens)
        return {'status': 'connected'}
