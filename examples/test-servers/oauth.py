import base64
from collections import Counter
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit


LOOPBACK = '.'.join(('127', '0', '0', '1'))


def challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()


class OAuthFixture:
    def __init__(self, origin, lifetime=3600):
        self.origin = origin
        self.lifetime = lifetime
        self.counts = Counter()
        self.clients = {}
        self.codes = {}
        self.access = {}
        self.refresh = {}
        self.sensitive = set()
        self.events = []
        self.lock = threading.Lock()

    def opaque(self):
        value = secrets.token_urlsafe(32)
        self.sensitive.add(value)
        return value

    def tokens(self, client):
        access, refresh = self.opaque(), self.opaque()
        self.access[access] = time.monotonic() + self.lifetime
        self.refresh[refresh] = client
        return {'access_token': access, 'refresh_token': refresh, 'token_type': 'Bearer',
                'expires_in': self.lifetime, 'scope': 'demo'}

    def request(self, method, target, headers, body):
        parsed = urlsplit(target)
        path = parsed.path
        if method == 'GET' and path.startswith('/.well-known/oauth-protected-resource'):
            self.counts['resource_discovery'] += 1
            return 200, {}, {'resource': self.origin + '/mcp', 'authorization_servers': [self.origin],
                             'scopes_supported': ['demo']}
        if method == 'GET' and path == '/.well-known/oauth-authorization-server':
            self.counts['issuer_discovery'] += 1
            return 200, {}, {'issuer': self.origin, 'authorization_endpoint': self.origin + '/authorize',
                'token_endpoint': self.origin + '/token', 'registration_endpoint': self.origin + '/register',
                'response_types_supported': ['code'], 'grant_types_supported': ['authorization_code', 'refresh_token'],
                'code_challenge_methods_supported': ['S256'], 'scopes_supported': ['demo'],
                'token_endpoint_auth_methods_supported': ['client_secret_post']}
        if method == 'POST' and path == '/register':
            metadata = json.loads(body)
            redirects = metadata.get('redirect_uris', [])
            if not redirects or any(urlsplit(uri).hostname != LOOPBACK for uri in redirects):
                return 400, {}, {'error': 'invalid_redirect_uri'}
            client, secret = self.opaque(), self.opaque()
            metadata.update(client_id=client, client_secret=secret, token_endpoint_auth_method='client_secret_post')
            self.clients[client] = metadata
            self.counts['registration'] += 1
            return 201, {}, metadata
        if method == 'GET' and path == '/authorize':
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            client = self.clients.get(query.get('client_id'))
            if (not client or query.get('redirect_uri') not in client['redirect_uris']
                    or query.get('code_challenge_method') != 'S256' or not query.get('code_challenge')
                    or query.get('response_type') != 'code' or not query.get('state')):
                return 400, {}, {'error': 'invalid_request'}
            code = self.opaque()
            self.codes[code] = query
            self.counts['authorization'] += 1
            return 302, {'Location': query['redirect_uri'] + '?' + urlencode({'code': code, 'state': query['state']})}, {}
        if method == 'POST' and path == '/token':
            query = {k: v[0] for k, v in parse_qs(body.decode()).items()}
            client_id = query.get('client_id')
            client = self.clients.get(client_id)
            if not client or not secrets.compare_digest(query.get('client_secret', ''), client['client_secret']):
                return 401, {}, {'error': 'invalid_client'}
            if query.get('grant_type') == 'authorization_code':
                code = query.get('code')
                pending = self.codes.get(code)
                if (not pending or pending['client_id'] != client_id
                        or pending['redirect_uri'] != query.get('redirect_uri')
                        or challenge(query.get('code_verifier', '')) != pending['code_challenge']):
                    return 400, {}, {'error': 'invalid_grant'}
                del self.codes[code]
                self.counts['pkce_verified'] += 1
                self.counts['code_exchange'] += 1
            elif query.get('grant_type') == 'refresh_token':
                refresh = query.get('refresh_token')
                if self.refresh.get(refresh) != client_id:
                    return 400, {}, {'error': 'invalid_grant'}
                del self.refresh[refresh]
                self.counts['refresh'] += 1
            else:
                return 400, {}, {'error': 'unsupported_grant_type'}
            return 200, {'Cache-Control': 'no-store'}, self.tokens(client_id)
        if path == '/mcp':
            credential = headers.get('Authorization', '').removeprefix('Bearer ')
            if self.access.get(credential, 0) <= time.monotonic():
                self.counts['unauthorized'] += 1
                return 401, {'WWW-Authenticate': 'Bearer ' + 'resource_metadata="' + self.origin + '/.well-known/oauth-protected-resource"'}, {'error': 'unauthorized'}
            if method == 'GET':
                return 405, {}, {}
            if method == 'DELETE':
                return 200, {}, {}
            message = json.loads(body)
            if 'id' not in message:
                return 202, {}, None
            rpc = message['method']
            if rpc == 'initialize':
                self.counts['initialize'] += 1
                result = {'protocolVersion': message['params']['protocolVersion'], 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'oauth-fixture', 'version': '1.0.0'}}
            elif rpc == 'tools/list':
                self.counts['tools_list'] += 1
                result = {'tools': [{'name': 'add', 'description': 'Add two integers behind OAuth',
                    'inputSchema': {'type': 'object', 'properties': {'a': {'type': 'integer'}, 'b': {'type': 'integer'}},
                                    'required': ['a', 'b']}}]}
            elif rpc == 'tools/call' and message['params']['name'] == 'add':
                args = message['params']['arguments']
                if any(type(args.get(k)) is not int for k in ('a', 'b')):
                    return 400, {}, {'error': 'invalid_arguments'}
                self.counts['tools_call'] += 1
                result = {'content': [{'type': 'text', 'text': str(args['a'] + args['b'])}], 'isError': False}
            elif rpc == 'ping':
                result = {}
            else:
                return 200, {}, {'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32601, 'message': 'Unknown method'}}
            return 200, {}, {'jsonrpc': '2.0', 'id': message['id'], 'result': result}
        return 404, {}, {'error': 'not_found'}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def do_DELETE(self):
        self.handle_request()

    def handle_request(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        with self.server.fixture.lock:
            try:
                status, headers, value = self.server.fixture.request(self.command, self.path, self.headers, body)
            except (ValueError, KeyError, TypeError):
                status, headers, value = 400, {}, {'error': 'invalid_request'}
            self.server.fixture.events.append({'method': self.command, 'path': urlsplit(self.path).path, 'status': status})
        data = json.dumps(value).encode() if value is not None else b''
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def server():
    httpd = ThreadingHTTPServer((LOOPBACK, 0), Handler)
    httpd.fixture = OAuthFixture(f'http://{LOOPBACK}:{httpd.server_port}')
    return httpd
