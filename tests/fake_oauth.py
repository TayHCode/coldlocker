import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
from urllib.parse import parse_qs, urlencode, urlsplit


LOOPBACK = '127' + '.0.0.1'


class Provider(ThreadingHTTPServer):
    def __init__(self):
        super().__init__((LOOPBACK, 0), ProviderHandler)
        self.origin = 'http://' + LOOPBACK + ':' + str(self.server_port)
        self.codes = {}
        self.clients = {}
        self.values = []
        self.refreshes = set()
        self.last_grant = None
        self.registration = True
        self.challenge = False
        self.bad_issuer = False
        self.bad_resource = False
        self.pkce = True
        self.auth_method = 'none'
        self.rotate_refresh = True
        self.fail_refresh = False
        self.malformed = False
        self.required_user_agent = None
        self.scope = None
        self.root_metadata = False

    def mint(self):
        value = secrets.token_urlsafe(32)
        self.values.append(value)
        return value


class ProviderHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass

    def reply(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parts = urlsplit(self.path)
        if parts.path not in ('/authorize', '/approve') and self.server.required_user_agent and self.headers.get('User-Agent') != self.server.required_user_agent:
            return self.reply({'error': 'client_identification_required'}, 403)
        origin = self.server.origin
        if parts.path == '/mcp':
            self.send_response(401)
            if self.server.challenge:
                challenge = 'Bearer ' + 'resource_metadata="' + origin + '/metadata"'
                if self.server.scope is not None:
                    challenge += ', scope="' + self.server.scope + '"'
                self.send_header('WWW-Authenticate', challenge)
            self.end_headers()
            return
        metadata_path = '/.well-known/oauth-protected-resource' + ('' if self.server.root_metadata else '/mcp')
        if parts.path in (metadata_path, '/metadata'):
            return self.reply({'resource': origin + ('/wrong' if self.server.bad_resource else '/mcp'), 'authorization_servers': [origin], 'scopes_supported': ['tools']})
        if parts.path == '/.well-known/oauth-authorization-server':
            result = {'issuer': origin + ('/wrong' if self.server.bad_issuer else ''), 'authorization_endpoint': origin + '/authorize', 'token_endpoint': origin + '/token',
                      'code_challenge_methods_supported': ['S256'] if self.server.pkce else ['plain'], 'token_endpoint_auth_methods_supported': [self.server.auth_method]}
            if self.server.registration:
                result['registration_endpoint'] = origin + '/register'
            return self.reply(result)
        if parts.path in ('/authorize', '/approve'):
            query = parse_qs(parts.query)
            if parts.path == '/authorize':
                body = ('<!doctype html><title>Example consent</title><h1>Connect Example Tools</h1>'
                        '<p>agentlocker requests access to your tools.</p><a href="/approve?' + urlencode({key: value[0] for key, value in query.items()}).replace('&', '&amp;') + '">Approve access</a>').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            client = self.server.clients.get(query['client_id'][0])
            if not client or query['redirect_uri'][0] not in client['redirect_uris'] or query.get('code_challenge_method') != ['S256']:
                return self.reply({'error': 'invalid_request'}, 400)
            code = secrets.token_urlsafe(24)
            self.server.codes[code] = {key: value[0] for key, value in query.items()}
            self.send_response(302)
            self.send_header('Location', query['redirect_uri'][0] + '?' + urlencode({'code': code, 'state': query['state'][0]}))
            self.end_headers()
            return
        return self.reply({'error': 'not_found'}, 404)

    def do_POST(self):
        if self.server.required_user_agent and self.headers.get('User-Agent') != self.server.required_user_agent:
            return self.reply({'error': 'client_identification_required'}, 403)
        body = self.rfile.read(int(self.headers['Content-Length']))
        if self.path == '/register':
            client = json.loads(body)
            client_id = self.server.mint()
            client['client_id'] = client_id
            if self.server.auth_method != 'none':
                client['client_secret'] = self.server.mint()
            self.server.clients[client_id] = client
            return self.reply(client, 201)
        if self.path != '/token':
            return self.reply({'error': 'not_found'}, 404)
        if self.server.malformed:
            self.wfile.write((self.server.mint() + '\r\n\r\n').encode())
            return
        data = {key: value[0] for key, value in parse_qs(body.decode()).items()}
        self.server.last_grant = data['grant_type']
        client = self.server.clients.get(data.get('client_id'))
        if not client:
            return self.reply({'error': 'invalid_client'}, 400)
        if self.server.auth_method == 'client_secret_post' and data.get('client_secret') != client['client_secret']:
            return self.reply({'error': 'invalid_client'}, 400)
        if self.server.auth_method == 'client_secret_basic':
            expected = 'Basic ' + base64.b64encode((client['client_id'] + ':' + client['client_secret']).encode()).decode()
            if self.headers.get('Authorization') != expected:
                return self.reply({'error': 'invalid_client'}, 400)
        if data['grant_type'] == 'authorization_code':
            code = self.server.codes.pop(data.get('code'), None)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(data.get('code_verifier', '').encode()).digest()).decode().rstrip('=')
            if not code or any(data.get(key) != code[key] for key in ('client_id', 'redirect_uri', 'resource')) or challenge != code['code_challenge']:
                return self.reply({'error': 'invalid_grant'}, 400)
        elif data['grant_type'] == 'refresh_token':
            if self.server.fail_refresh or data.get('refresh_token') not in self.server.refreshes:
                return self.reply({'error': 'invalid_grant'}, 400)
        else:
            return self.reply({'error': 'unsupported_grant'}, 400)
        result = {'access_token': self.server.mint(), 'token_type': 'Bearer', 'expires_in': 3600}
        if data['grant_type'] == 'authorization_code' or self.server.rotate_refresh:
            refresh = self.server.mint()
            self.server.refreshes.add(refresh)
            result['refresh_token'] = refresh
        return self.reply(result)
