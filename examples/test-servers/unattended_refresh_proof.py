import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback

import oauth
import oauth_proof as proof
from ui_gateway_proof import api

import gateway
from gateway import state, settings
from gateway_adapter import private_directory, save
from hub_ui import SetupServer
from store import FileStore


def main():
    os.umask(0o077)
    original = proof.ROOT
    proof.EVIDENCE = private_directory(original / '.scratch/evidence/l5')
    sandbox = Path(tempfile.mkdtemp(prefix='l5-proof-', dir=private_directory(original / '.scratch')))
    files = subprocess.run(['git', 'ls-files', '-z'], cwd=original, capture_output=True, check=True).stdout
    for name in files.decode().split('\0'):
        if name:
            target = sandbox / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original / name, target)
    supervisor = (sandbox / 'gateway.py').read_text(encoding='utf-8')
    for old, new in (('REFRESH_INTERVAL = 30', 'REFRESH_INTERVAL = 1'), ('REFRESH_LEAD = 60', 'REFRESH_LEAD = 0')):
        assert old in supervisor
        supervisor = supervisor.replace(old, new)
    (sandbox / 'gateway.py').write_text(supervisor, encoding='utf-8')
    runtime_source = original / '.scratch/mcphub-runtime'
    shutil.copytree(runtime_source, sandbox / '.scratch/mcphub-runtime', symlinks=True)
    gateway.ROOT = sandbox
    proof.ROOT = sandbox
    proof.RUNTIME = sandbox / '.hub-store/gateway'
    proof.STORE = FileStore(sandbox)
    save(sandbox / 'manifest.json', {'schema_version': 1, 'name': 'agentlocker', 'license': 'MIT',
                                   'skills': [], 'rules': [], 'routines': [], 'mcp_servers': []})
    httpd = oauth.server()
    fixture = httpd.fixture
    fixture.lifetime = 12
    proof.SENSITIVE = fixture.sensitive
    ui = SetupServer(sandbox)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (httpd, ui)]
    for thread in threads:
        thread.start()
    with socket.socket() as available:
        available.bind((oauth.LOOPBACK, 0))
        port = available.getsockname()[1]
    url = f'http://{oauth.LOOPBACK}:{port}/mcp'
    try:
        ui.stage_import('mcp', {'name': 'oauth-demo', 'description': 'Protected demo tools',
                               'transport': 'http', 'url': fixture.origin + '/mcp'})
        result = api(ui, 'auth/oauth', {'name': 'oauth-demo'})
        fixture.sensitive.add(next(iter(ui.authentication.pending.values()))['verifier'])
        with proof.OPENER.open(result['url'], timeout=30) as response:
            assert response.status == 200
        proof.record('wizard-api.txt', 'POST /api/auth/oauth: 200\nFake consent and wizard callback: 200\nexit=0\n')
        proof.gateway('prepare')
        current = settings(proof.RUNTIME)
        current['mcpServers']['oauth-demo']['owner'] = 'admin'
        save(proof.RUNTIME / 'mcp_settings.json', current)
        proof.gateway('start', '--port', str(port), '--auto-refresh')
        initial = state(proof.RUNTIME)
        expiry = ui.authentication.metadata('oauth-demo')['expires_at']
        old_access = ui.authentication.read('oauth-demo', 'access')
        old_refresh = ui.authentication.read('oauth-demo', 'refresh')
        proof.probe(url, 'before-expiry')
        duplicate = proof.run([sys.executable, str(sandbox / 'gateway.py'), 'start', '--port', str(port), '--auto-refresh'], 'duplicate-start.txt')
        assert duplicate.returncode == 1
        duplicate = proof.run([sys.executable, str(sandbox / 'gateway.py'), '_run', '--port', str(port), '--auto-refresh'], 'duplicate-supervisor.txt')
        assert duplicate.returncode == 1
        proof.wait_for(lambda: time.time() >= expiry and fixture.counts['refresh'] >= 3
                       and state(proof.RUNTIME)['child'] != initial['child'])
        fixture.lifetime = 3600
        proof.wait_for(lambda: ui.authentication.metadata('oauth-demo')['expires_at'] > time.time() + 60)
        def callable_after_refresh():
            try:
                proof.probe(url, 'after-expiry')
                return True
            except (OSError, ValueError, KeyError, StopIteration):
                return False
        proof.wait_for(callable_after_refresh)
        current = state(proof.RUNTIME)
        assert current['pid'] == initial['pid'] and current['port'] == port
        access = ui.authentication.read('oauth-demo', 'access')
        assert access != old_access and ui.authentication.read('oauth-demo', 'refresh') != old_refresh
        header = ui.authentication.metadata('oauth-demo')['headers']['Authorization']
        assert proof.STORE.resolve('secret://' + header) == 'Bearer ' + access
        assert fixture.counts['registration'] == fixture.counts['authorization'] == fixture.counts['pkce_verified'] == 1
        proof.record('store-proof.json', json.dumps({'access_rotated': True, 'refresh_rotated': True,
            'header_matches_store_access': True, 'initial_token_expired': time.time() >= expiry,
            'same_supervisor': True, 'same_port': True, 'node_replaced': True,
            'no_user_action_after_start': True, 'counts': dict(fixture.counts)}, indent=2))
    except Exception as error:
        proof.record('failure.txt', type(error).__name__ + '\n' + ''.join(traceback.format_tb(error.__traceback__)).replace(str(original), '<WORKTREE>'))
        raise
    finally:
        proof.gateway('stop')
        assert state(proof.RUNTIME) is None
        for server, thread in zip((httpd, ui), threads):
            server.shutdown()
            thread.join()
            server.server_close()
        log = (proof.RUNTIME / 'gateway.log').read_text(encoding='utf-8')
        proof.record('refresh.log', ''.join(line + '\n' for line in log.splitlines() if '[auto-refresh]' in line))
        proof.record('mcphub.log', log)
        values = {v.encode() for v in fixture.sensitive}
        values.update(proof.STORE.resolve('secret://' + n).encode() for n in proof.STORE.names())
        paths = [original / name for name in files.decode().split('\0') if name]
        paths += list(proof.EVIDENCE.rglob('*')) + [proof.RUNTIME / 'gateway.log']
        hits = [p.name for p in paths if p.is_file() and any(v and v in p.read_bytes() for v in values)]
        argv_checked = 0
        for path in Path('/proc').glob('[0-9]*/cmdline'):
            try:
                data = path.read_bytes()
            except OSError:
                continue
            argv_checked += 1
            if any(v and v in data for v in values):
                hits.append('process-argv')
        private = [p for base in (proof.STORE.path, proof.EVIDENCE) for p in [base, *base.rglob('*')]]
        for path in private:
            path.chmod(0o700 if path.is_dir() else 0o600)
        modes = all(p.stat().st_mode & 0o777 == (0o700 if p.is_dir() else 0o600) for p in private)
        proof.record('leak-scan.json', json.dumps({'hits': hits, 'files_checked': len(paths),
            'secret_values_checked': len(values), 'argv_checked': argv_checked, 'private_modes_correct': modes}))
        assert not hits and modes
        proof.record('sandbox.txt', str(sandbox.relative_to(original)) + '\nGateway stopped; sandbox retained privately.\n')
    print('Unattended proof complete; evidence in .scratch/evidence/l5')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Unattended proof failed: ' + type(error).__name__ + '; inspect private evidence', file=sys.stderr)
        raise SystemExit(1)
