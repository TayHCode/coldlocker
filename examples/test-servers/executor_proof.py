import contextlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import executor_connector
import executor_sources
import gateway
from hub_ui import gateway_connection
from store import FileStore


def main():
    evidence = Path(sys.argv[1]).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    runtime = ROOT / '.scratch/executor-runtime'
    home = Path(tempfile.mkdtemp(prefix='executor-proof-live-', dir=ROOT / '.scratch'))
    for name in ('home', 'config', 'data', 'cache', 'locker'):
        (home / name).mkdir(mode=0o700)
    locker = home / 'locker'
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    env = {key: value for key, value in os.environ.items() if key in (
        'PATH', 'LANG', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'NODE_EXTRA_CA_CERTS')}
    env.update(HOME=str(home / 'home'), XDG_CONFIG_HOME=str(home / 'config'),
               XDG_DATA_HOME=str(home / 'data'), XDG_CACHE_HOME=str(home / 'cache'),
               DO_NOT_TRACK='1', EXECUTOR_DISABLE_ANALYTICS='1', BROWSER='/bin/true')
    binary = runtime / 'node_modules/.bin/executor'
    version = subprocess.run([str(binary), '--version'], env=env, capture_output=True, text=True, check=True, encoding='utf-8')
    print('Executor version:', version.stdout.strip())
    print('Foreground runtime: numeric loopback, ephemeral port, isolated HOME/XDG, no service install')
    process = subprocess.Popen([str(binary), 'web', '--foreground', '--hostname', '127.0.0.1', '--port', str(port)],
                               cwd=home, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    lines = []
    reader = threading.Thread(target=lambda: lines.extend(process.stdout), daemon=True)
    reader.start()
    store = FileStore(locker)
    token = None
    try:
        auth = home / 'home/.executor/server-control/auth.json'
        for _ in range(200):
            if process.poll() is not None:
                raise ValueError('Executor exited during startup')
            if auth.exists():
                token = json.loads(auth.read_text(encoding='utf-8'))['token']
                break
            time.sleep(.1)
        if not token:
            raise ValueError('Executor endpoint auth not ready')
        store.put('executor-endpoint', 'Bearer ' + token)
        (locker / 'config.json').write_text(json.dumps({'gateway': {'backend': 'executor', 'executor': {
            'url': 'http://127.0.0.1:' + str(port) + '/mcp',
            'headers': {'Authorization': 'secret://executor-endpoint'}}}}), encoding='utf-8')
        for _ in range(100):
            if executor_connector.status(locker)['status'] == 'running':
                break
            time.sleep(.1)
        else:
            raise ValueError('Executor MCP not ready')
        listeners = subprocess.run(['ss', '-H', '-ltnp', 'sport = :' + str(port)], capture_output=True, text=True, check=True, encoding='utf-8').stdout
        assert listeners and all(line.split()[3] == '127.0.0.1:' + str(port) for line in listeners.splitlines())
        print('Listener ownership/bind:', listeners.strip())
        (locker / 'manifest.json').write_bytes((ROOT / 'manifest.json').read_bytes())
        (locker / 'mcp/servers').mkdir(parents=True)
        (locker / 'mcp/servers/example-server.json').write_bytes((ROOT / 'mcp/servers/example-server.json').read_bytes())
        (locker / 'examples').mkdir()
        (locker / 'examples/example-server.py').write_bytes((ROOT / 'examples/example-server.py').read_bytes())
        executor_sources.registration(locker, 'example-server', True)
        executor_sources.registration(locker, 'example-server', True)
        print('Registration repeated successfully; existing integration and connection preserved')
        gateway.ROOT = locker
        for command in ('status', 'url', 'start', 'stop'):
            sys.argv = ['gateway.py', command]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = gateway.main()
            print('gateway.py', command, 'exit:', code, output.getvalue().strip())
            assert code == 0
        connection = gateway_connection(locker)
        assert connection['url'] == executor_connector.configuration(locker)['url']
        print('Wizard selected endpoint matches configured backend')
        with executor_connector.Session(locker) as client:
            print('MCP client tools/list:', json.dumps([tool['name'] for tool in client.tools()]))
            query = client.exchange('tools/call', {'name': 'execute', 'arguments': {
                'code': 'return await tools.search({query: "add", namespace: "locker-example-server", limit: 5});'}})
            matches = query['structuredContent']['result']['items']
            tool = next(item['path'] for item in matches if item['path'].endswith('.add'))
            print('Discovered live source tool:', tool)
            response = client.exchange('tools/call', {'name': 'execute', 'arguments': {
                'code': 'return await tools[' + json.dumps(tool) + ']({a: 2, b: 3});'}})
            state = response['structuredContent']
            if state['status'] == 'waiting_for_interaction':
                interaction = state['interaction']
                assert interaction['address'].endswith('.add') and interaction['args'] == {'a': 2, 'b': 3}
                response = client.exchange('tools/call', {'name': 'resume', 'arguments': {
                    'executionId': state['executionId'], 'action': 'accept', 'content': '{}'}})
            safe = store.redact(json.dumps(response))
            if token in safe:
                raise ValueError('credential in tool response suppressed')
            print('MCP client tools/call:', safe)
            value = response['structuredContent']['result']
            assert value['ok'] is True
            assert value['data']['content'][0]['text'] == '5'
            print('ASSERT source add(2, 3) = 5: PASS')
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        reader.join(timeout=5)
        process.stdout.close()
        text = b''.join(lines).decode(errors='replace')
        if token:
            text = text.replace(token, '[REDACTED]')
        (evidence / 'executor-runtime.log').write_text(store.redact(text), encoding='utf-8')
        listeners = subprocess.run(['ss', '-H', '-ltnp', 'sport = :' + str(port)], capture_output=True, text=True, check=True, encoding='utf-8').stdout
        assert not listeners
        remaining = subprocess.run(['ps', '-eo', 'pid,args'], capture_output=True, text=True, check=True, encoding='utf-8').stdout
        assert not any(str(home) in line and 'executor' in line for line in remaining.splitlines())
        print('Cleanup: foreground process reaped; no listener or Executor process for proof home remains')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
