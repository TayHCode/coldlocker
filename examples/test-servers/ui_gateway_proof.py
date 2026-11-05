import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import urllib.request

import oauth
import oauth_proof as proof

ROOT = proof.ROOT
sys.path.insert(0, str(ROOT))
from gateway import settings
from gateway_adapter import private_directory, save
from hub_ui import SetupServer
from ui_auth import entry_prefix


def api(server, route, data=None):
    request = urllib.request.Request(server.origin + '/api/' + route,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type': 'application/json', 'Origin': server.origin, 'X-CSRF-Token': server.token})
    with proof.OPENER.open(request, timeout=30) as response:
        return json.load(response)


def snapshot(label):
    for name in ('claude.txt', 'codex.txt', 'clients.json', 'client-login-boundaries.json'):
        (proof.EVIDENCE / name).rename(proof.EVIDENCE / (label + '-' + name))


def leak_scan():
    values = {v.encode() for v in proof.SENSITIVE}
    values.update(proof.STORE.resolve('secret://' + n).encode() for n in proof.STORE.names())
    paths = [p for p in ROOT.rglob('*') if p.is_file() and not p.is_symlink()
             and not {'.git', '.hub-store', '__pycache__', 'node_modules'}.intersection(p.relative_to(ROOT).parts)]
    paths += [proof.RUNTIME / 'gateway.log']
    hits = [str(p.relative_to(ROOT)) for p in paths if any(v and v in p.read_bytes() for v in values)]
    private = [proof.STORE.path, *proof.STORE.path.rglob('*'), proof.EVIDENCE, *proof.EVIDENCE.rglob('*')]
    modes = all((p.stat().st_mode & 0o777) == (0o700 if p.is_dir() else 0o600) for p in private if not p.is_symlink())
    proof.record('leak-scan.json', json.dumps({'hits': hits, 'files_checked': len(paths),
        'values_checked': len(values), 'private_modes_correct': modes, 'argv_checks': proof.ARGV_CHECKS,
        'excluded': ['Store', 'runtime settings', 'runtime package', 'Git metadata', 'bytecode']}, indent=2))
    assert not hits and modes, 'leak scan or private mode failure'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--clients', action='store_true')
    parser.add_argument('--screenshot', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    proof.EVIDENCE = private_directory(ROOT / '.scratch/evidence/int')
    assert not (proof.RUNTIME / 'mcp_settings.json').exists(), 'use a fresh disposable repo copy'
    manifest_path = ROOT / 'manifest.json'
    original = manifest_path.read_bytes()
    manifest = json.loads(original)
    manifest['mcp_servers'] = []
    save(manifest_path, manifest)
    httpd = oauth.server()
    fixture = httpd.fixture
    proof.SENSITIVE = fixture.sensitive
    ui = SetupServer(ROOT)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (httpd, ui)]
    for thread in threads:
        thread.start()
    ui.stage_import('mcp', {'name': 'oauth-demo', 'description': 'Protected demo tools', 'transport': 'http', 'url': fixture.origin + '/mcp'})
    manifest_before = manifest_path.read_bytes()
    definition = (ROOT / 'mcp/servers/oauth-demo.json').read_bytes()
    with socket.socket() as available:
        available.bind((oauth.LOOPBACK, 0))
        port = available.getsockname()[1]
    url = f'http://{oauth.LOOPBACK}:{port}/mcp'
    try:
        result = api(ui, 'auth/oauth', {'name': 'oauth-demo'})
        flow = next(iter(ui.authentication.pending.values()))
        proof.SENSITIVE.add(flow['verifier'])
        with proof.OPENER.open(result['url'], timeout=30) as response:
            assert response.status == 200 and response.url.startswith(ui.origin + '/oauth/callback')
        proof.record('wizard-api.txt', 'POST /api/auth/oauth: HTTP 200\nGET consent URL -> UI /oauth/callback: HTTP 200\nPKCE exchange succeeded; no gateway OAuth authorization\nexit=0\n')
        old = ui.authentication.read('oauth-demo', 'access'), ui.authentication.read('oauth-demo', 'refresh')
        binding = ui.authentication.metadata('oauth-demo')
        binding['expires_at'] = time.time() - 1
        ui.authentication.write_metadata('oauth-demo', binding)
        fixture.access = {key: 0 for key in fixture.access}
        proof.gateway('prepare')
        assert ui.authentication.read('oauth-demo', 'access') != old[0]
        assert ui.authentication.read('oauth-demo', 'refresh') != old[1]
        current = settings(proof.RUNTIME)
        current['mcpServers']['oauth-demo']['owner'] = 'admin'
        save(proof.RUNTIME / 'mcp_settings.json', current)
        proof.gateway('start', '--port', str(port))
        tool = proof.probe(url, 'oauth-before-restart')
        if args.clients:
            proof.clients(url, tool, fixture)
            snapshot('oauth-before-restart')
        proof.gateway('stop')
        proof.gateway('start', '--port', str(port))
        proof.probe(url, 'oauth-after-restart')
        if args.clients:
            proof.clients(url, tool, fixture)
            snapshot('oauth-after-restart')
        binding = ui.authentication.metadata('oauth-demo')
        binding['expires_at'] = time.time() - 1
        ui.authentication.write_metadata('oauth-demo', binding)
        fixture.access = {key: 0 for key in fixture.access}
        proof.gateway('refresh')
        proof.probe(url, 'oauth-after-live-refresh')
        assert manifest_path.read_bytes() == manifest_before
        assert (ROOT / 'mcp/servers/oauth-demo.json').read_bytes() == definition
        proof.record('store-proof.json', json.dumps({'access_rotated': True, 'refresh_rotated': True,
            'manifest_unchanged': True, 'definition_unchanged': True, 'restart_reused_store': True,
            'explicit_refresh_restarted_gateway': True, 'counts': dict(fixture.counts)}, indent=2))
        if args.screenshot:
            save(proof.EVIDENCE / 'ports.json', {'ui': ui.server_port})
            print('Screenshot ready', flush=True)
            deadline = time.monotonic() + 240
            while not (proof.EVIDENCE / 'screenshot.done').exists() and time.monotonic() < deadline:
                time.sleep(.2)
            assert (proof.EVIDENCE / 'step-6-gateway.png').exists(), 'screenshot missing'
        value = fixture.opaque()
        fixture.access[value] = time.monotonic() + 3600
        result = api(ui, 'auth/bearer', {'name': 'oauth-demo', 'header': 'Authorization', 'scheme': 'bearer', 'value': value})
        assert result['status'] == 'connected'
        proof.gateway('stop')
        proof.gateway('start', '--port', str(port))
        tool = proof.probe(url, 'bearer-before-restart')
        if args.clients:
            proof.clients(url, tool, fixture)
            snapshot('bearer')
        proof.gateway('stop')
        proof.gateway('start', '--port', str(port))
        proof.probe(url, 'bearer-after-restart')
        assert fixture.counts['registration'] == fixture.counts['authorization'] == fixture.counts['pkce_verified'] == 1
        assert fixture.counts['refresh'] == 2
        proof.record('upstream-counters.json', json.dumps(dict(fixture.counts), indent=2))
        proof.record('upstream-http.json', json.dumps(fixture.events, indent=2))
        proof.record('wizard-status.json', json.dumps(api(ui, 'status'), indent=2))
    finally:
        proof.gateway('stop')
        for server, thread in zip((httpd, ui), threads):
            server.shutdown()
            thread.join()
            server.server_close()
        proof.record('mcphub.log', (proof.RUNTIME / 'gateway.log').read_text(encoding='utf-8') if (proof.RUNTIME / 'gateway.log').exists() else '')
        leak_scan()
    print('UI gateway proof complete; evidence in .scratch/evidence/int', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('UI gateway proof failed: ' + type(error).__name__ + '; inspect private evidence', file=sys.stderr)
        raise SystemExit(1)
