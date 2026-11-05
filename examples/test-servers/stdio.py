import json
import os
import sys


def dispatch(request):
    method = request.get('method')
    if 'id' not in request:
        return None
    response = {'jsonrpc': '2.0', 'id': request['id']}
    if method == 'initialize':
        result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'demo', 'version': '1.0.0'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'add', 'description': 'Add two numbers', 'inputSchema': {
            'type': 'object', 'properties': {'a': {'type': 'number'}, 'b': {'type': 'number'}},
            'required': ['a', 'b'], 'additionalProperties': False}}]}
    elif method == 'tools/call':
        params = request.get('params', {})
        args = params.get('arguments', {})
        if params.get('name') != 'add' or set(args) != {'a', 'b'} or any(type(v) not in (int, float) for v in args.values()):
            return {**response, 'error': {'code': -32602, 'message': 'Invalid tool arguments'}}
        result = {'content': [{'type': 'text', 'text': str(args['a'] + args['b'])}], 'isError': False}
    elif method == 'ping':
        result = {}
    else:
        return {**response, 'error': {'code': -32601, 'message': 'Method not found'}}
    return {**response, 'result': result}


if __name__ == '__main__':
    if os.environ.get('DEMO_LOG_CANARY'):
        print(os.environ['DEMO_LOG_CANARY'], file=sys.stderr, flush=True)
    for line in sys.stdin:
        try:
            response = dispatch(json.loads(line))
        except (ValueError, TypeError, KeyError):
            response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Invalid request'}}
        if response is not None:
            print(json.dumps(response), flush=True)
