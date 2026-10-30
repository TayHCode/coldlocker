import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import oauth
from http_client import exchange, select_tool
from gateway_adapter import private_directory, save

CAPTURED = []


class ProofFailure(Exception):
    def __init__(self, stage, **details):
        super().__init__(stage)
        self.details = details


def require(value, stage):
    if not value:
        raise ProofFailure(stage)


def isolated_environment(home):
    allowed = {'PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
               'NODE_EXTRA_CA_CERTS', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY',
               'http_proxy', 'https_proxy', 'no_proxy'}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home / 'config'),
               XDG_DATA_HOME=str(home / 'data'), XDG_STATE_HOME=str(home / 'state'),
               XDG_CACHE_HOME=str(home / 'cache'), NO_PROXY=oauth.LOOPBACK,
               no_proxy=oauth.LOOPBACK, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    return env


def captured(argv, *, cwd, env, timeout):
    with subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding='utf-8', start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=25)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate(timeout=5)
            raise
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def copy_source(source, target):
    result = subprocess.run(['git', 'ls-files', '-z'], cwd=source, capture_output=True, check=True)
    names = set(result.stdout.decode().split('\0')) - {''}
    names.add('examples/test-servers/acceptance_proof.py')
    digest = hashlib.sha256()
    for name in sorted(names):
        path = source / name
        require(path.is_file() and not path.is_symlink(), 'ordinary source files')
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        digest.update(name.encode() + b'\0' + hashlib.sha256(destination.read_bytes()).digest())
    return digest.hexdigest()


def available_port():
    with socket.socket() as listener:
        listener.bind((oauth.LOOPBACK, 0))
        return listener.getsockname()[1]


def closed(port):
    with socket.socket() as probe:
        return probe.connect_ex((oauth.LOOPBACK, port)) != 0


def command(root, *args, expected=0, timeout=60, data=None):
    result = subprocess.run([sys.executable, *args], cwd=root, input=data,
                            capture_output=True, text=True, encoding='utf-8', timeout=timeout)
    CAPTURED.extend((result.stdout, result.stderr))
    require(result.returncode == expected, 'command: ' + ' '.join(args[:2]))
    return result


def catalog(root):
    names = ['hub_list_' + kind for kind in
             ('skills', 'rules', 'mcp_servers', 'routines', 'plugins', 'connections')]
    messages = [{'id': 1, 'method': 'initialize', 'params': {}},
                {'method': 'notifications/initialized'}, {'id': 2, 'method': 'tools/list'}]
    messages += [{'id': index + 3, 'method': 'tools/call',
                  'params': {'name': name, 'arguments': {}}} for index, name in enumerate(names)]
    result = command(root, 'mcp_endpoint.py', data=''.join(
        json.dumps({'jsonrpc': '2.0', **message}) + '\n' for message in messages))
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    require(len(responses) == 8 and not any('error' in item for item in responses), 'catalog RPC')
    require(set(names) <= {tool['name'] for tool in responses[1]['result']['tools']}, 'catalog discovery')
    entries = json.loads(responses[4]['result']['content'][0]['text'])
    require(any(entry['name'] == 'oauth-demo' for entry in entries), 'import in catalog')
    return result.stdout


def api(server, route, data):
    request = urllib.request.Request(server.origin + '/api/' + route,
        data=json.dumps(data).encode(), headers={'Content-Type': 'application/json',
            'Origin': server.origin, 'X-CSRF-Token': server.token})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=30) as response:
        return json.load(response)


def tool_call(url, name, session):
    return exchange(url, {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                         'params': {'name': name, 'arguments': {'a': 2, 'b': 3}}}, session)[0]


def denied_call(url, name, session):
    from urllib.error import HTTPError
    try:
        return tool_call(url, name, session)
    except HTTPError as error:
        try:
            require(error.code in (401, 403, 502, 503), 'unexpected denial HTTP status')
            return {'error': 'HTTP denial'}
        finally:
            error.close()


def client(url, label):
    result, session = exchange(url, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
        'params': {'protocolVersion': '2024-11-05', 'capabilities': {},
                   'clientInfo': {'name': label, 'version': '1.0.0'}}})
    require('result' in result, 'client initialize')
    exchange(url, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, session)
    result, session = exchange(url, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}, session)
    name = select_tool(result['result']['tools'], 'oauth-demo-add')
    result = tool_call(url, name, session)
    require(not result['result'].get('isError') and result['result']['content'][0]['text'] == '5', 'harmless tool result')
    return name, session


def shared_clients(root, url, fixture):
    before = fixture.counts['tools_call']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: command(root, 'examples/test-servers/http_client.py', url,
                                                  '--tool', 'oauth-demo-add'), range(2)))
    require(all('tools/call:' in result.stdout for result in results), 'two independent protocol clients')
    require(fixture.counts['tools_call'] == before + 2,
            'both calls reached protected upstream: delta=' + str(fixture.counts['tools_call'] - before))


def fixture_proof(root):
    import gateway
    from hub_ui import SetupServer
    from store import FileStore

    require(root == ROOT and root.name == 'checkout with spaces' and
            (root.parent / 'fixture-only').is_file() and
            Path.home().resolve() == root.parent / 'home', 'disposable checkout guard')
    summary = {'status': 'running', 'runtime': 'MCPHub 1.1.7', 'checks': []}
    store = FileStore(root)
    runtime = root / '.hub-store/gateway'
    httpd = oauth.server()
    fixture = httpd.fixture
    ui = SetupServer(root, scan_home=Path.home())
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (httpd, ui)]
    port = available_port()
    url = f'http://{oauth.LOOPBACK}:{port}/mcp'
    owned = []
    snapshots = []
    failure = None
    for thread in threads:
        thread.start()

    def lifecycle(action, *args, expected=0):
        result = command(root, 'gateway.py', action, *args, expected=expected)
        if action == 'prepare' and expected == 0:
            current = gateway.settings(runtime)
            current['mcpServers']['oauth-demo']['owner'] = 'admin'
            save(runtime / 'mcp_settings.json', current)
        if action == 'start' and expected == 0:
            current = gateway.state(runtime)
            require(current and current['port'] == port, 'owned gateway state')
            owned.extend([current['pid'], current['child']])
        return result

    def restart():
        lifecycle('stop')
        require(closed(port), 'restart released port')
        lifecycle('start', '--port', str(port))

    try:
        ui.stage_import('mcp', {'name': 'oauth-demo', 'description': 'Protected local fixture tools',
                               'transport': 'http', 'url': fixture.origin + '/mcp'})
        snapshots.append(catalog(root))
        require(fixture.counts['initialize'] == 0, 'listing never executes upstream')
        summary['checks'].append('import and six read-only catalog listings')
        definition = root / '.hub-store/catalog/mcp/servers/oauth-demo.json'
        indexed = {path: path.read_bytes() for path in
                   (root / 'manifest.json', root / '.hub-store/catalog/manifest.json', definition)}
        result = api(ui, 'auth/oauth', {'name': 'oauth-demo'})
        fixture.sensitive.add(next(iter(ui.authentication.pending.values()))['verifier'])
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(result['url'], timeout=30) as response:
            require(response.status == 200 and response.url.startswith(ui.origin + '/oauth/callback'), 'PKCE callback')
        old = tuple(ui.authentication.read('oauth-demo', kind) for kind in ('access', 'refresh'))
        lifecycle('prepare')
        lifecycle('start', '--port', str(port))
        verified = api(ui, 'auth/check', {'server': 'oauth-demo'})
        require(verified['status'] == 'verified', 'live OAuth verification: ' + verified['status'])
        name, session = client(url, 'fixture-client')
        shared_clients(root, url, fixture)
        summary['checks'].append('PKCE OAuth and two independent MCP protocol clients sharing auth')
        lifecycle('status')
        lifecycle('sync')
        restart()
        client(url, 'after-restart')
        require(tuple(ui.authentication.read('oauth-demo', kind) for kind in ('access', 'refresh')) == old,
                'restart reused credentials')
        lifecycle('stop')
        (runtime / 'mcp_settings.json').unlink()
        lifecycle('prepare')
        lifecycle('start', '--port', str(port))
        name, session = client(url, 'after-settings-rebuild')
        summary['checks'].append('persistence across restart and settings rebuild from Store')

        binding = ui.authentication.metadata('oauth-demo')
        binding['expires_at'] = time.time() - 1
        ui.authentication.write_metadata('oauth-demo', binding)
        with fixture.lock:
            fixture.access = dict.fromkeys(fixture.access, 0)
        before_denied = fixture.counts['unauthorized']
        denied = denied_call(url, name, session)
        require('error' in denied or denied.get('result', {}).get('isError'), 'expired access denied')
        require(fixture.counts['unauthorized'] > before_denied, 'expired request reached upstream')
        original_request = fixture.request
        unavailable = {'refresh': True, 'mcp': False}

        def request(method, target, headers, body):
            path = target.partition('?')[0]
            if (path == '/token' and unavailable['refresh']) or (path == '/mcp' and unavailable['mcp']):
                fixture.counts['injected_failure'] += 1
                return 503, {}, {'error': 'temporarily_unavailable'}
            return original_request(method, target, headers, body)

        fixture.request = request
        settings_before = (runtime / 'mcp_settings.json').read_bytes()
        lifecycle('refresh', expected=1)
        require(fixture.counts['injected_failure'] == 1 and closed(port), 'failed refresh reached token endpoint and stopped gateway')
        require(tuple(ui.authentication.read('oauth-demo', kind) for kind in ('access', 'refresh')) == old,
                'failed refresh preserved credentials')
        require((runtime / 'mcp_settings.json').read_bytes() == settings_before, 'failed refresh preserved settings')
        unavailable['refresh'] = False
        lifecycle('refresh')
        if not gateway.state(runtime):
            lifecycle('start', '--port', str(port))
        current = gateway.state(runtime)
        owned.extend([current['pid'], current['child']])
        fresh = tuple(ui.authentication.read('oauth-demo', kind) for kind in ('access', 'refresh'))
        require(all(a != b for a, b in zip(old, fresh)) and old[1] not in fixture.refresh, 'refresh rotation')
        name, session = client(url, 'after-refresh')
        shared_clients(root, url, fixture)
        summary['checks'].append('expired access denied; failed refresh preserves state; access and refresh rotate')
        unavailable['mcp'] = True
        denied = denied_call(url, name, session)
        require('error' in denied or denied.get('result', {}).get('isError'), 'upstream outage surfaced')
        require(fixture.counts['injected_failure'] == 2, 'outage request reached upstream')
        unavailable['mcp'] = False
        restart()
        client(url, 'reconnected')
        summary['checks'].append('upstream failure and explicit stop/start reconnect')

        lifecycle('stop')
        lifecycle('start', '--port', str(port), '--auto-refresh')
        previous_child = gateway.state(runtime)['child']
        binding = ui.authentication.metadata('oauth-demo')
        binding['expires_at'] = time.time() - 1
        ui.authentication.write_metadata('oauth-demo', binding)
        with fixture.lock:
            fixture.access = dict.fromkeys(fixture.access, 0)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            current = gateway.state(runtime)
            if current and current['child'] != previous_child and fixture.counts['refresh'] == 2:
                owned.extend([current['pid'], current['child']])
                break
            time.sleep(.2)
        else:
            raise ProofFailure('unmodified supervisor auto-refresh deadline')
        require(all(ui.authentication.read('oauth-demo', kind) != value for kind, value in zip(('access', 'refresh'), fresh)),
                'supervised refresh rotated both credentials')
        deadline = time.monotonic() + 15
        while True:
            try:
                client(url, 'after-supervised-refresh')
                break
            except (OSError, ValueError, KeyError, StopIteration):
                require(time.monotonic() < deadline, 'supervised refresh reconnect deadline')
                time.sleep(.2)
        summary['checks'].append('unmodified 30-second supervisor refreshes and reconnects on the same port')

        lifecycle('stop')
        value = fixture.opaque()
        with fixture.lock:
            fixture.access = {value: time.monotonic() + 3600}
        require(api(ui, 'auth/bearer', {'name': 'oauth-demo', 'header': 'Authorization',
                'scheme': 'bearer', 'value': value})['status'] == 'connected', 'verification of bearer auth')
        lifecycle('prepare')
        lifecycle('start', '--port', str(port))
        client(url, 'bearer')
        shared_clients(root, url, fixture)
        restart()
        client(url, 'bearer-restart')
        require(fixture.counts['authorization'] == fixture.counts['registration'] == fixture.counts['pkce_verified'] == 1,
                'only one authorization grant')
        require(fixture.counts['refresh'] == 2, 'explicit and supervised refresh each succeed once')
        require(all(path.read_bytes() == content for path, content in indexed.items()), 'catalog unchanged by auth')
        snapshots.append(catalog(root))
        summary['checks'].append('bearer auth shared by two protocol clients and reused after restart')
        summary['checks'].append('auth leaves manifests and imported definition unchanged')
    except Exception as error:
        failure = error if isinstance(error, ProofFailure) else ProofFailure(type(error).__name__)
    finally:
        try:
            lifecycle('stop')
        finally:
            for server, thread in zip((httpd, ui), threads):
                server.shutdown()
                thread.join(timeout=10)
                server.server_close()
        require(all(closed(number) for number in (port, httpd.server_port, ui.server_port)), 'cleanup ports closed')
        require(not gateway.state(runtime), 'cleanup gateway state absent')
        statuses = [gateway.ps(pid, 'stat') for pid in set(owned)]
        require(all(status is None or status.startswith('Z') for status in statuses), 'cleanup owned processes stopped')
        values = {value.encode() for value in fixture.sensitive | {ui.token}}
        values.update(store.resolve('secret://' + name).encode() for name in ('gateway-admin', 'gateway-jwt') if store.has(name))
        public = [root / 'manifest.json', *list((root / '.hub-store/catalog').rglob('*.json')),
                  *list((root / '.hub-store').glob('calls*')), runtime / 'gateway.log', runtime / 'bootstrap.mjs']
        require(not any(value in path.read_bytes() for path in public if path.is_file() for value in values), 'no secret in catalog or diagnostics')
        require(not any(value in snapshot.encode() for snapshot in [*snapshots, *CAPTURED] for value in values),
                'catalog and command output secret free')
        private = root / '.hub-store'
        require(all(path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
                    for path in [private, *private.rglob('*')] if not path.is_symlink()), 'private storage permissions')
        summary['checks'].append('secret-free catalog and diagnostics; private storage permissions')
        summary['checks'].append('gateway, setup and upstream stopped; all fixture ports closed')
    if failure:
        raise failure
    summary.update(status='passed', upstream_counts=dict(fixture.counts),
                   product_agent_proof=False, real_provider_proof=False,
                   limits=['two processes using the same raw MCP HTTP client', 'fake OAuth upstream plus bearer auth',
                           'loopback fixture requires private admin-owner policy', 'explicit restart for outage reconnect',
                           'one native platform per run'])
    return summary


def run(args):
    os.umask(0o077)
    if args.fixture:
        return fixture_proof(ROOT)
    require(sys.platform in ('linux', 'darwin'), 'gateway acceptance requires Linux or macOS')
    base = Path(tempfile.mkdtemp(prefix='agentlocker-acceptance-')).resolve()
    root = base / 'checkout with spaces'
    env = isolated_environment(base / 'home')
    failure = None
    try:
        (base / 'fixture-only').touch(mode=0o600)
        private_directory(root)
        source_digest = copy_source(ROOT, root)
        private_directory(base / 'home')
        runtime = ROOT / '.scratch/mcphub-runtime'
        if not args.install_runtime:
            require((runtime / 'node_modules/@samanhappy/mcphub/package.json').is_file(),
                    'runtime missing; run gateway.py install or use --install-runtime')
            private_directory(root / '.scratch')
            destination = root / '.scratch/mcphub-runtime'
            private_directory(destination)
            for name in ('package.json', 'package-lock.json'):
                shutil.copyfile(runtime / name, destination / name)
            shutil.copytree(runtime / 'node_modules', destination / 'node_modules', symlinks=True)
        else:
            installed = captured([sys.executable, 'gateway.py', 'install'], cwd=root, env=env, timeout=600)
            require(installed.returncode == 0, 'pinned runtime installation')
        result = captured([sys.executable, 'examples/test-servers/acceptance_proof.py', '--fixture'],
                          cwd=root, env=env, timeout=360)
        try:
            summary = json.loads(result.stdout)
        except ValueError:
            raise ProofFailure('fixture produced no valid summary; exit=' + str(result.returncode)) from None
        require(result.returncode == 0, 'fixture: ' + summary.get('stage', 'unknown failure'))
        summary['source_head'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        summary['source_files_sha256'] = source_digest
        summary['runtime_install'] = 'npm ci --ignore-scripts' if args.install_runtime else 'copied installed pinned runtime'
    except BaseException as error:
        failure = error
    finally:
        try:
            cleanup_fixture(root, env)
        except Exception as error:
            stage = str(failure) if isinstance(failure, ProofFailure) else type(failure).__name__ if failure else 'parent cleanup'
            raise ProofFailure(stage, retained_fixture=str(base),
                               cleanup_stage=str(error) if isinstance(error, ProofFailure) else type(error).__name__) from None
        shutil.rmtree(base)
    if failure:
        raise failure
    require(not base.exists(), 'temporary fixture removed')
    summary['checks'].append('temporary checkout and fixture home removed')
    return summary


def cleanup_fixture(root, env):
    runtime = root / '.hub-store/gateway'
    pid = runtime / 'pid.json'
    port = None
    try:
        port = json.loads(pid.read_text(encoding='utf-8')).get('port')
    except (OSError, ValueError):
        pass
    result = None
    try:
        if runtime.is_dir():
            result = subprocess.run([sys.executable, 'gateway.py', 'stop'], cwd=root, env=env,
                                    capture_output=True, timeout=60)
    finally:
        stop_fixture_processes(root)
    require((result is None or result.returncode == 0) and
            (port is None or closed(port)), 'parent cleanup; fixture retained on failure')


def fixture_processes(root):
    import gateway
    result = subprocess.run(['ps', '-axww', '-o', 'pid=,stat=,args='], capture_output=True, text=True, timeout=10)
    require(result.returncode == 0, 'cannot verify fixture process cleanup')
    python = '(?:' + '|'.join(re.escape(value) for value in
                            {sys.executable, str(Path(sys.executable).resolve()), 'python3'}) + ')'
    scripts = [re.escape(str(root / name)) for name in ('gateway.py', 'examples/example-server.py')]
    pattern = re.compile(r'^(?:' + python + ' (?:' + scripts[0] + r' _run(?: |$)|' + scripts[1] + r'$)|'
                         r'(?:node|' + re.escape(shutil.which('node') or 'node') + ') ' +
                         re.escape(str(root / '.hub-store/gateway/bootstrap.mjs')) + r'$)')
    found = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and not parts[1].startswith('Z') and pattern.match(parts[2]):
            pid = int(parts[0])
            found[pid] = (gateway.identity(pid), parts[2])
    return found


def stop_fixture_processes(root):
    for action in (signal.SIGTERM, signal.SIGKILL):
        found = fixture_processes(root)
        if not found:
            return
        for pid, identity in found.items():
            if fixture_processes(root).get(pid) == identity:
                try:
                    os.kill(pid, action)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and fixture_processes(root):
            time.sleep(.1)
    require(not fixture_processes(root), 'owned fixture processes survived; fixture retained on failure')


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    parser = argparse.ArgumentParser(description='Disposable real-MCPHub OAuth and bearer auth acceptance; no product logins used')
    parser.add_argument('--install-runtime', action='store_true', help='install pinned npm runtime inside fixture (needs registry access)')
    parser.add_argument('--fixture', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        result = run(args)
    except Exception as error:
        result = {'status': 'failed', 'stage': str(error) if isinstance(error, ProofFailure) else type(error).__name__}
        if isinstance(error, ProofFailure):
            result.update(error.details)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
