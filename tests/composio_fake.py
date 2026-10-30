import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


LOOPBACK = '.'.join(('127', '0', '0', '1'))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_error(405)

    def do_DELETE(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        if self.headers.get('x-api-key') != self.server.key:
            self.server.denied += 1
            self.send_response(401)
            self.end_headers()
            return
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path == '/api/v3.1/custom/toolkits/upsert':
            self.server.registrations.append(request)
            status = self.server.api_status
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            if status == 302:
                self.send_header('Location', self.server.redirect_url)
            self.end_headers()
            self.wfile.write(json.dumps(self.server.api_response).encode())
            return
        method = request['method']
        self.server.methods.append(method)
        if 'id' not in request:
            self.send_response(202)
            self.end_headers()
            return
        if method == 'initialize':
            result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'composio-fixture', 'version': '1.0.0'}}
        elif method == 'tools/list':
            result = {'tools': [{'name': 'add', 'description': 'Add two numbers', 'inputSchema': {
                'type': 'object', 'properties': {'a': {'type': 'number'}, 'b': {'type': 'number'}},
                'required': ['a', 'b']}}]}
        elif method == 'tools/call':
            args = request['params']['arguments']
            result = {'content': [{'type': 'text', 'text': str(args['a'] + args['b'])}]}
        else:
            result = {}
        body = json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FakeComposio:
    def __init__(self, key):
        self.server = ThreadingHTTPServer((LOOPBACK, 0), Handler)
        self.server.key = key
        self.server.methods = []
        self.server.denied = 0
        self.server.registrations = []
        self.server.api_status = 200
        self.server.api_response = {'slug': 'CUSTOM_AGENTLOCKER'}
        self.server.redirect_url = '/redirected'
        self.api_url = ('http://' + LOOPBACK + ':' + str(self.server.server_port)
                        + '/api/v3.1/custom/toolkits/upsert')
        self.url = 'http://' + LOOPBACK + ':' + str(self.server.server_port) + '/mcp'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
