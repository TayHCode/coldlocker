import argparse
import json
import urllib.request


def exchange(url, request, session=None):
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
               'MCP-Protocol-Version': '2024-11-05'}
    if session:
        headers['Mcp-Session-Id'] = session
    message = urllib.request.Request(url, json.dumps(request).encode(), headers)
    with urllib.request.urlopen(message, timeout=15) as response:
        data = response.read().decode()
        session = response.headers.get('Mcp-Session-Id', session)
    if not data:
        return None, session
    if data.startswith('event:') or data.startswith('data:'):
        data = next(line[5:].strip() for line in data.splitlines() if line.startswith('data:'))
    return json.loads(data), session


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('--tool', help='require this exact tool, useful when several upstreams expose add')
    args = parser.parse_args()
    url = args.url
    response, session = exchange(url, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
        'params': {'protocolVersion': '2024-11-05', 'capabilities': {},
                   'clientInfo': {'name': 'agentlocker-proof', 'version': '1.0.0'}}})
    print('initialize:', json.dumps(response))
    exchange(url, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, session)
    response, session = exchange(url, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}, session)
    print('tools/list:', json.dumps(response))
    tool = select_tool(response['result']['tools'], args.tool)
    response, session = exchange(url, {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
        'params': {'name': tool, 'arguments': {'a': 2, 'b': 3}}}, session)
    print('tools/call:', json.dumps(response))
    assert not response['result'].get('isError')
    assert response['result']['content'][0]['text'] == '5'


def select_tool(tools, requested=None):
    for tool in tools:
        if tool['name'] == requested if requested is not None else tool['name'].endswith('add'):
            return tool['name']
    raise ValueError('requested addition tool is not listed')


if __name__ == '__main__':
    main()
