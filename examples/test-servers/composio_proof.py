import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from composio_connector import connect
from composio_fake import FakeComposio, LOOPBACK
from gateway_adapter import environment, redact_log
from store import FileStore
from http_client import exchange


def proof(runtime, evidence):
    scratch = ROOT / '.scratch'
    scratch.mkdir(exist_ok=True)
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='composio-proof-', dir=scratch) as temporary:
        root = Path(temporary)
        for source in ROOT.glob('*.py'):
            shutil.copyfile(source, root / source.name)
        shutil.copytree(ROOT / 'checks', root / 'checks')
        installation = root / '.scratch/mcphub-runtime'
        shutil.copytree(runtime, installation)
        home = root / '.scratch/home'
        home.mkdir()
        (root / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'name': 'agentlocker',
            'license': 'MIT', 'skills': [], 'rules': [], 'routines': [], 'mcp_servers': []}), encoding='utf-8')
        store = FileStore(root)
        canary = 'canary-' + secrets.token_hex(24)
        store.put('composio-api-key', canary)
        env = environment({}, store)
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'),
                   XDG_CACHE_HOME=str(home / 'cache'), XDG_DATA_HOME=str(home / 'data'))
        transcript = []

        def run(command, *args):
            argv = [sys.executable, str(root / 'gateway.py'), command, *args]
            result = subprocess.run(argv, cwd=root, env=env, text=True, capture_output=True, timeout=50, encoding='utf-8')
            assert canary not in result.stdout + result.stderr, 'credential in command output'
            transcript.append('$ python3 gateway.py ' + ' '.join([command, *args]) + '\n'
                              + result.stdout + result.stderr + 'exit=' + str(result.returncode))
            assert result.returncode == 0, 'gateway command failed'

        with FakeComposio(canary) as fake:
            output = io.StringIO()
            with redirect_stdout(output):
                connect(root, 'proof', fake.url)
            assert canary not in output.getvalue()
            transcript.append(output.getvalue())
            run('install')
            run('prepare')
            path = root / '.hub-store/gateway/mcp_settings.json'
            settings = json.loads(path.read_text(encoding='utf-8'))
            assert canary not in path.read_text(encoding='utf-8'), 'credential in settings'
            settings['mcpServers']['composio-proof']['owner'] = 'admin'
            path.write_text(json.dumps(settings), encoding='utf-8')
            with socket.socket() as sock:
                sock.bind((LOOPBACK, 0))
                port = sock.getsockname()[1]
            try:
                run('start', '--port', str(port))
                url = 'http://' + LOOPBACK + ':' + str(port) + '/mcp'
                initialized, session = exchange(url, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                    'params': {'protocolVersion': '2024-11-05', 'capabilities': {},
                               'clientInfo': {'name': 'composio-proof', 'version': '1.0.0'}}})
                assert 'result' in initialized
                exchange(url, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, session)
                listed, session = exchange(url, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}, session)
                tool = next(t['name'] for t in listed['result']['tools'] if t['name'].endswith('add'))
                called, _ = exchange(url, {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                    'params': {'name': tool, 'arguments': {'a': 2, 'b': 3}}}, session)
                assert not called['result'].get('isError')
                assert called['result']['content'][0]['text'] == '5'
                assert all(m in fake.server.methods for m in ('initialize', 'tools/list', 'tools/call'))
                transcript.append('Real MCPHub 1.1.7: initialize, tools/list, tools/call add(2, 3) = 5; authenticated upstream requests verified.')
            finally:
                run('stop')
                log = (root / '.hub-store/gateway/gateway.log').read_text(encoding='utf-8')
                assert canary not in log, 'credential in gateway log'
                (evidence / 'mcphub.log').write_text(redact_log(store, log).replace(str(root), '<DISPOSABLE>'), encoding='utf-8')
                text = '\n'.join(transcript)
                assert canary not in text
                (evidence / 'gateway-proof.log').write_text(redact_log(store, text).replace(str(root), '<DISPOSABLE>') + '\n', encoding='utf-8')
        for filename in subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True,
                                       check=True).stdout.split(b'\0'):
            if filename:
                assert canary.encode() not in (ROOT / os.fsdecode(filename)).read_bytes()
        print('PASS: real MCPHub -> header-protected local fake; list and call; no credential in settings, output, gateway log or tracked files.')
        print('Live Composio: NOT RUN; requires project API key and query-free hosted MCP URL.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    proof(args.runtime, args.evidence)


if __name__ == '__main__':
    main()
