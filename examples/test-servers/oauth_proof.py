import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import shlex
import subprocess
import sys
import threading
import time
import urllib.request

import oauth
from http_client import exchange


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from gateway_adapter import private_directory, save, redact_log, seed
from gateway import settings
from store import FileStore

EVIDENCE = ROOT / '.scratch/evidence/gateway-oauth'
RUNTIME = ROOT / '.hub-store/gateway'
STORE = FileStore(ROOT)
SENSITIVE = set()
ARGV_CHECKS = []
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def record(name, text):
    path = EVIDENCE / name
    class ProofSecrets:
        def names(self):
            return []

        def redact(self, value):
            values = SENSITIVE | {STORE.resolve('secret://' + n) for n in ('gateway-admin', 'gateway-jwt') if STORE.has(n)}
            for secret in sorted(values, key=len, reverse=True):
                value = value.replace(secret, '[REDACTED]')
            return value
    text = redact_log(ProofSecrets(), text).replace(str(ROOT), '<WORKTREE>')
    path.write_text(text, encoding='utf-8')
    path.chmod(0o600)


def execute(argv, cwd, env=None, timeout=60):
    assert not any(secret in arg for secret in SENSITIVE for arg in argv), 'secret in requested argv'
    with subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8') as process:
        try:
            observed = Path('/proc', str(process.pid), 'cmdline').read_bytes()
            assert not any(secret.encode() in observed for secret in SENSITIVE), 'secret in live argv'
            ARGV_CHECKS.append({'program': argv[0], 'live_argv_secret_free': True})
            stdout, stderr = process.communicate(timeout=timeout)
        except BaseException:
            process.kill()
            process.communicate()
            raise
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def run(argv, name=None, env=None, cwd=ROOT, timeout=60):
    result = execute(argv, cwd, env, timeout)
    record(name or 'lifecycle.txt', '$ ' + shlex.join(argv).replace(str(ROOT), '<WORKTREE>') + '\n'
           + result.stdout + result.stderr + '\nexit=' + str(result.returncode) + '\n'
           + ((EVIDENCE / 'lifecycle.txt').read_text(encoding='utf-8') if name is None and (EVIDENCE / 'lifecycle.txt').exists() else ''))
    if name is None:
        assert result.returncode == 0, 'lifecycle failed'
    return result


def gateway(command, *args):
    return run([sys.executable, str(ROOT / 'gateway.py'), command, *args])


def wait_for(callback):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        value = callback()
        if value:
            return value
        time.sleep(.1)
    raise TimeoutError('proof deadline')


def probe(url, label):
    transcript = []
    def rpc(method, params=None, session=None, identity=1):
        request = {'jsonrpc': '2.0', 'method': method}
        if identity is not None:
            request['id'] = identity
        if params is not None:
            request['params'] = params
        result, session = exchange(url, request, session)
        transcript.append(method + ': ' + json.dumps(result))
        return result, session
    _, session = rpc('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                                   'clientInfo': {'name': label, 'version': '1.0.0'}})
    rpc('notifications/initialized', session=session, identity=None)
    result, session = rpc('tools/list', session=session)
    tool = next(t['name'] for t in result['result']['tools'] if t['name'].endswith('add'))
    result, _ = rpc('tools/call', {'name': tool, 'arguments': {'a': 2, 'b': 3}}, session)
    assert result['result']['content'][0]['text'] == '5' and not result['result'].get('isError')
    record(label + '.txt', '\n'.join(transcript) + '\nexit=0\n')
    return tool


def clients(url, tool, fixture):
    product_home = Path.home()
    client_root = private_directory(ROOT / '.scratch/oauth-clients')
    claude_home = private_directory(client_root / 'claude-home')
    config = private_directory(client_root / 'claude-config')
    mcp = client_root / 'mcp.json'
    save(mcp, {'mcpServers': {'agentlocker': {'type': 'http', 'url': url}}})
    login = product_home / '.claude/.credentials.json'
    before = hashlib.sha256(login.read_bytes()).digest() if login.exists() else None
    outcome = {}
    if before:
        credential = json.loads(login.read_text(encoding='utf-8'))['claudeAiOauth']
        if credential.get('expiresAt', 0) <= time.time() * 1000:
            record('claude.txt', 'Operator access token expired; no client started, no refresh attempted.\n')
            outcome['claude'] = 'expired'
        else:
            SENSITIVE.add(credential['accessToken'])
            before_calls = fixture.counts['tools_call']
            env = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'SSL_CERT_FILE', 'SSL_CERT_DIR')}
            env.update(HOME=str(claude_home), CLAUDE_CONFIG_DIR=str(config),
                       CLAUDE_CODE_OAUTH_TOKEN=credential['accessToken'])
            args = ['claude', '-p', 'Call the agentlocker ' + tool + ' tool with a=2 b=3. Report the result.',
                    '--model', 'claude-haiku-5-5', '--mcp-config', str(mcp), '--strict-mcp-config',
                    '--tools', '', '--allowedTools', 'mcp__agentlocker__' + tool,
                    '--setting-sources', '', '--no-session-persistence', '--output-format', 'stream-json', '--verbose']
            result = execute(args, client_root, env, timeout=120)
            raw = (result.stdout + result.stderr).replace(credential['accessToken'], '[REDACTED]')
            record('claude.txt', '$ ' + shlex.join(args) + '\n' + raw + '\nexit=' + str(result.returncode))
            outcome['claude'] = result.returncode
            assert result.returncode == 0 and fixture.counts['tools_call'] > before_calls, 'Claude did not call upstream'
            assert hashlib.sha256(login.read_bytes()).digest() == before
    else:
        record('claude.txt', 'Operator login file unavailable; no client started.\n')
        outcome['claude'] = 'missing'
    codex_home = Path(os.environ.get('CODEX_HOME', product_home / '.codex'))
    config_path = codex_home / 'config.toml'
    config_before = config_path.read_bytes() if config_path.exists() else None
    args = ['codex', 'exec', '--ignore-user-config', '--ignore-rules', '--ephemeral', '--sandbox', 'read-only',
            '--skip-git-repo-check', '--json', '-c', 'mcp_servers.agentlocker.url=' + json.dumps(url),
            '-c', 'model_reasoning_effort="low"',
            '-c', 'mcp_servers.agentlocker.tools.' + tool + '.approval_mode="approve"',
            'Use only the agentlocker MCP tools. Call ' + tool + ' with a=2 b=3 and report its result.']
    before_calls = fixture.counts['tools_call']
    result = run(args, 'codex.txt', cwd=client_root, timeout=120)
    outcome['codex'] = result.returncode
    assert result.returncode == 0 and fixture.counts['tools_call'] > before_calls, 'Codex did not call upstream'
    assert (config_path.read_bytes() if config_path.exists() else None) == config_before
    record('client-login-boundaries.json', json.dumps({'claude_login_unchanged': hashlib.sha256(login.read_bytes()).digest() == before if before else None, 'codex_config_unchanged': (config_path.read_bytes() if config_path.exists() else None) == config_before, 'auth_files_copied': False, 'codex_ephemeral': True}, indent=2))
    record('clients.json', json.dumps(outcome, indent=2) + '\n')
    assert outcome == {'claude': 0, 'codex': 0}, 'both CLI clients required'
    return outcome


def scan():
    values = {value.encode() for value in SENSITIVE}
    values.update(STORE.resolve('secret://' + n).encode() for n in ('gateway-admin', 'gateway-jwt') if STORE.has(n))
    paths = [ROOT / p for p in subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard'], cwd=ROOT, text=True, encoding='utf-8').splitlines()]
    paths += list(EVIDENCE.rglob('*')) + list((ROOT / '.scratch/oauth-clients').rglob('*'))
    paths += [RUNTIME / 'gateway.log', RUNTIME / 'bootstrap.mjs']
    hits = [str(p.relative_to(ROOT)) for p in paths if p.is_file() and not p.is_symlink()
            and any(v and v in p.read_bytes() for v in values)]
    argv_count = 0
    for path in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            data = path.read_bytes()
        except (OSError, PermissionError):
            continue
        argv_count += 1
        if any(v and v in data for v in values):
            hits.append('process-argv')
    modes = {str(p.relative_to(ROOT)): oct(p.stat().st_mode & 0o777)
             for base in (ROOT / '.hub-store', EVIDENCE) for p in [base, *base.rglob('*')] if not p.is_symlink()}
    assert all(mode == ('0o700' if (ROOT / p).is_dir() else '0o600') for p, mode in modes.items())
    record('leak-scan.json', json.dumps({'fixed_string_grep_hits': hits, 'secret_values_checked': len(values),
        'files_checked': sum(p.is_file() for p in paths), 'argv_checked': argv_count, 'live_process_checks': ARGV_CHECKS,
        'excluded_sensitive_files': ['Store', 'runtime OAuth settings and preserved settings backups'], 'modes': modes}, indent=2))
    assert not hits, 'secret leak detected (paths only in leak-scan.json)'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--clients', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    private_directory(EVIDENCE)
    httpd = oauth.server()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    fixture = httpd.fixture
    global SENSITIVE
    SENSITIVE = fixture.sensitive
    with socket.socket() as available:
        available.bind((oauth.LOOPBACK, 0))
        port = available.getsockname()[1]
    url = f'http://{oauth.LOOPBACK}:{port}'
    source = private_directory(ROOT / '.scratch/oauth-hub')
    definition_dir = private_directory(private_directory(source / 'mcp') / 'servers')
    definition = definition_dir / 'oauth-demo.json'
    save(definition, {'name': 'oauth-demo', 'description': 'Loopback fake OAuth fixture',
                      'transport': 'http', 'url': fixture.origin + '/mcp'})
    digest = hashlib.sha256(definition.read_bytes()).hexdigest()
    save(source / 'manifest.json', {'schema_version': 1, 'name': 'agentlocker', 'license': 'MIT',
        'skills': [], 'rules': [], 'routines': [], 'mcp_servers': [{'name': 'oauth-demo',
        'path': 'mcp/servers/oauth-demo.json', 'owner': 'agentlocker',
        'provenance': {'source_id': 'src-' + digest[:12], 'origin': 'authored'}, 'sha256': digest}]})
    if (RUNTIME / 'mcp_settings.json').exists():
        assert set(settings(RUNTIME)['mcpServers']) <= {'oauth-demo'}, 'unfamiliar runtime; use a dedicated worktree'
    log = RUNTIME / 'gateway.log'
    if log.exists():
        log.rename(EVIDENCE / ('previous-' + str(time.time_ns()) + '.log'))
    try:
        gateway('prepare', '--source', str(source))
        initial = settings(RUNTIME)
        initial['mcpServers']['oauth-demo'].update(owner='admin', oauth={
            'enabled': True, 'allowInsecureTokenEndpoint': True, 'redirectUri': url + '/oauth/callback',
            'scopes': ['demo'], 'dynamicRegistration': {'metadata': {'token_endpoint_auth_method': 'client_secret_post'}}})
        initial['systemConfig']['install'] = {'baseUrl': url}
        save(RUNTIME / 'mcp_settings.json', initial)
        gateway('start', '--port', str(port))
        run(['ss', '-H', '-ltnp', 'sport = :' + str(port)], 'listeners.txt')
        def pending():
            return settings(RUNTIME)['mcpServers']['oauth-demo'].get('oauth', {}).get('pendingAuthorization', {}).get('authorizationUrl')
        authorization = wait_for(pending)
        verifier = settings(RUNTIME)['mcpServers']['oauth-demo']['oauth']['pendingAuthorization']['codeVerifier']
        fixture.sensitive.add(verifier)
        with OPENER.open(authorization, timeout=30) as response:
            assert response.status == 200 and '/oauth/callback' in response.url
            record('browser-step.txt', 'stdlib urllib opener followed fake authorization redirect to MCPHub /oauth/callback\nHTTP status=' + str(response.status) + '\nexit=0\n')
        gateway('sync')
        expected = settings(RUNTIME)['mcpServers']['oauth-demo']['oauth']
        assert all(expected.get(k) for k in ('accessToken', 'refreshToken', 'clientId', 'clientSecret'))
        assert seed({'mcpServers': {'oauth-demo': {}}}, STORE)['mcpServers']['oauth-demo']['oauth'] == expected
        tool = probe(url + '/mcp', 'before-restart')
        gateway('stop')
        (RUNTIME / 'mcp_settings.json').rename(RUNTIME / ('settings-before-prepare-' + str(time.time_ns()) + '.json'))
        gateway('prepare', '--source', str(source))
        prepared = settings(RUNTIME)
        restored = prepared['mcpServers']['oauth-demo']['oauth']
        assert restored == expected
        prepared['mcpServers']['oauth-demo']['owner'] = 'admin'
        save(RUNTIME / 'mcp_settings.json', prepared)
        fixture.access = {k: 0 for k in fixture.access}
        gateway('start', '--port', str(port))
        probe(url + '/mcp', 'after-refresh')
        gateway('sync')
        refreshed = settings(RUNTIME)['mcpServers']['oauth-demo']['oauth']
        assert refreshed['accessToken'] != expected['accessToken']
        assert refreshed['refreshToken'] != expected['refreshToken']
        assert seed({'mcpServers': {'oauth-demo': {}}}, STORE)['mcpServers']['oauth-demo']['oauth'] == refreshed
        record('store-proof.json', json.dumps({'initial_sync_equal': True, 'settings_removed_then_restored': True,
            'refresh_access_rotated': True, 'refresh_refresh_rotated': True, 'refreshed_store_equal': True}))
        if args.clients:
            before = dict(fixture.counts)
            clients(url + '/mcp', tool, fixture)
            record('client-counters.json', json.dumps({'before': before, 'after': dict(fixture.counts)}, indent=2))
        assert fixture.counts['authorization'] == fixture.counts['registration'] == fixture.counts['pkce_verified'] == 1
        assert fixture.counts['refresh'] >= 1
        record('upstream-counters.json', json.dumps(dict(fixture.counts), indent=2))
        record('upstream-http.json', json.dumps(fixture.events, indent=2))
    finally:
        try:
            gateway('stop')
        finally:
            httpd.shutdown()
        httpd.server_close()
        record('mcphub.log', (RUNTIME / 'gateway.log').read_text(encoding='utf-8'))
        scan()
    print('OAuth proof complete; evidence in .scratch/evidence/gateway-oauth')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('OAuth proof failed: ' + type(error).__name__ + '; inspect private evidence', file=sys.stderr)
        raise SystemExit(1)
