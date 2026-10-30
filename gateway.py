from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

from gateway_adapter import BindingRefresher, LOOPBACK, SKIPPED_NEXT_STEP, VERSION, drop_unset, environment, launch_settings, prepare, private_directory, redact_log, save, skipped, sync, ui_bindings
from portable import lock as acquire, python_command
from store import FileStore, QuietParser, ordinary_path
import executor_connector


ROOT = Path(__file__).resolve().parent
PORT = 43871
RUNTIME_PINS = {'@modelcontextprotocol/client': '2.3.0', 'hono': '4.13.11', '@hono/node-server': '2.1.3'}
UPGRADE_MINIMUMS = {'@modelcontextprotocol/client': (2, 0, 0), 'hono': (4, 12, 4), '@hono/node-server': (1, 19, 9)}
REFRESH_INTERVAL = 30
REFRESH_LEAD = 60
TOKEN_SECRET = 'gateway-token'
TOKEN_VARIABLE = 'AGENTLOCKER_GATEWAY_TOKEN'
TOKEN_KEY = 'agentlocker-gateway'
BOOTSTRAP = """import http from 'node:http';
import net from 'node:net';
import {pathToFileURL} from 'node:url';
const bind = process.env.AGENTLOCKER_BIND;
const port = process.env.PORT;
const authorities = new Set(['127.0.0.1', 'localhost', '[::1]', bind.includes(':') ? '[' + bind + ']' : bind]
  .map(host => (host + ':' + port).toLowerCase()));
const permitted = req => {
  const host = req.headers.host;
  if (typeof host !== 'string' || !authorities.has(host.toLowerCase())) return false;
  const origin = req.headers.origin;
  return origin === undefined || (typeof origin === 'string' && origin.toLowerCase().startsWith('http://') && authorities.has(origin.slice(7).toLowerCase()));
};
const refusal = 'HTTP/1.1 403 Forbidden\\r\\nConnection: close\\r\\nContent-Length: 0\\r\\n\\r\\n';
const emit = http.Server.prototype.emit;
http.Server.prototype.emit = function(event, req, target, ...rest) {
  if (['request', 'checkContinue', 'checkExpectation'].includes(event) && !permitted(req)) {
    target.writeHead(403, {'Content-Type': 'text/plain', 'Connection': 'close'});
    target.end('Forbidden: Host or Origin is not an allowed loopback authority\\n');
    return true;
  }
  if (['upgrade', 'connect'].includes(event) && !permitted(req)) {
    target.end(refusal);
    return true;
  }
  return emit.call(this, event, req, target, ...rest);
};
const listen = net.Server.prototype.listen;
net.Server.prototype.listen = function(port, ...args) {
  if (typeof port !== 'number' && typeof port !== 'string') throw Error('Unsupported listener');
  return listen.call(this, Number(port), process.env.AGENTLOCKER_BIND, ...args.filter(x => typeof x === 'function'));
};
await import(pathToFileURL(process.env.AGENTLOCKER_ENTRY).href);
"""


class SetupError(ValueError):
    pass


def install_command():
    return python_command() + ' gateway.py install'


def require_supported():
    if sys.platform not in ('linux', 'darwin'):
        raise SetupError('the gateway runs on Linux and macOS only; on this system skip it and use the stdio route '
                         f'({python_command()} hub.py connect without the gateway)')


def prerequisite(name):
    if not shutil.which(name):
        raise SetupError(f'{name} is required; install it before retrying')


def check_node():
    prerequisite('node')
    result = subprocess.run(['node', '--version'], check=True, capture_output=True, text=True, encoding='utf-8')
    try:
        major = int(result.stdout.strip().removeprefix('v').split('.')[0])
    except ValueError:
        raise SetupError('cannot determine Node version; Node 20+ is required') from None
    if major < 20:
        raise SetupError('Node 20+ is required; upgrade Node before retrying')


def runtime_package(prefix=None):
    prefix = ROOT / '.scratch/mcphub-runtime' if prefix is None else prefix
    return prefix / 'node_modules/@samanhappy/mcphub'


def check_mcphub(prefix=None):
    package = ordinary_path(runtime_package(prefix))
    if not (package / 'package.json').is_file():
        raise SetupError('runtime missing; run ' + install_command())
    if json.loads(ordinary_path(package / 'package.json').read_text(encoding='utf-8')).get('version') != VERSION:
        raise SetupError('runtime version refused; expected MCPHub ' + VERSION)
    if not ordinary_path(package / 'dist/index.js').is_file():
        raise SetupError('runtime incomplete; reinstall the pinned MCPHub package')


def runtime_package_versions(prefix=None):
    prefix = ROOT / '.scratch/mcphub-runtime' if prefix is None else prefix
    versions = {}
    for name in RUNTIME_PINS:
        paths = [prefix / 'node_modules' / name / 'package.json',
                 *runtime_package(prefix).glob('**/node_modules/' + name + '/package.json')]
        versions[name] = []
        for path in paths:
            path = ordinary_path(path)
            try:
                version = json.loads(path.read_text(encoding='utf-8')).get('version')
            except (OSError, ValueError, TypeError, AttributeError):
                raise SetupError('runtime package missing or unreadable: ' + name +
                                 '; preserve the runtime before installing the tracked pins') from None
            if not isinstance(version, str):
                raise SetupError('runtime package version is invalid: ' + name)
            versions[name].append(version)
    return versions


def check_runtime(prefix=None):
    check_mcphub(prefix)
    for name, versions in runtime_package_versions(prefix).items():
        if any(version != RUNTIME_PINS[name] for version in versions):
            raise SetupError('runtime package refused; expected ' + name + ' ' + RUNTIME_PINS[name] +
                             '; run ' + install_command() + ' to upgrade the pinned runtime')


def upgradeable(name, version):
    if version == RUNTIME_PINS[name]:
        return True
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        return False
    return UPGRADE_MINIMUMS[name] <= tuple(map(int, version.split('.'))) < tuple(map(int, RUNTIME_PINS[name].split('.')))


@contextmanager
def installation_lock(exclusive=True):
    path = ordinary_path(private_directory(ROOT / '.scratch') / 'mcphub-install.lock')
    with open(path, 'a', encoding='utf-8') as lock:
        os.chmod(path, 0o600)
        try:
            acquire(lock, exclusive=exclusive, wait=False)
        except BlockingIOError:
            raise SetupError('gateway runtime is busy; stop the gateway before installing or retry after installation') from None
        yield


def install_runtime():
    check_node()
    prerequisite('npm')
    prefix = ordinary_path(ROOT / '.scratch/mcphub-runtime')
    if prefix.exists():
        if not prefix.is_dir():
            raise SetupError('runtime path is not a directory; preserve it before installing the tracked pins')
        if ordinary_path(runtime_package() / 'package.json').exists():
            check_mcphub()
            versions = runtime_package_versions()
            if all(version == RUNTIME_PINS[name] for name, values in versions.items() for version in values):
                return
            if any(not upgradeable(name, version) for name, values in versions.items() for version in values):
                raise SetupError('unfamiliar runtime package version; preserve the runtime before installing the tracked pins')
    if state(ROOT / '.hub-store/gateway'):
        raise SetupError('stop the gateway before upgrading its runtime')
    previous = os.umask(0o077)
    try:
        scratch = private_directory(ROOT / '.scratch')
        staged = ordinary_path(scratch / ('mcphub-install-' + secrets.token_hex(12)))
        staged.mkdir(mode=0o700)
        env = environment({}, FileStore(ROOT))
        env['HOME'] = str(private_directory(staged / 'home'))
        env['npm_config_cache'] = str(private_directory(scratch / 'npm-cache'))
        for name in ('package.json', 'package-lock.json'):
            shutil.copyfile(ROOT / 'gateway' / name, staged / name)
        subprocess.run(['npm', 'ci', '--ignore-scripts', '--prefix', str(staged)], env=env, check=True)
        check_runtime(staged)
        if state(ROOT / '.hub-store/gateway'):
            raise SetupError('stop the gateway before upgrading its runtime')
        backup = None
        if prefix.exists():
            backup = ordinary_path(scratch / ('mcphub-runtime.backup-' + secrets.token_hex(12)))
            if backup.exists():
                raise SetupError('runtime backup destination already exists; retry installation')
            prefix.rename(backup)
        try:
            staged.rename(prefix)
        except OSError:
            if backup is not None and not prefix.exists():
                backup.rename(prefix)
            raise
    finally:
        os.umask(previous)


def install():
    require_supported()
    with installation_lock():
        install_runtime()


def ps(pid, field):
    try:
        result = subprocess.run(['ps', '-ww', '-o', field + '=', '-p', str(pid)], capture_output=True, text=True,
                                env=dict(os.environ, LC_ALL='C'), encoding='utf-8')
    except OSError:
        return None
    return result.stdout.strip() or None


def identity(pid):
    if sys.platform != 'linux':
        return ps(pid, 'lstart')
    try:
        text = Path('/proc').joinpath(str(pid), 'stat').read_text(encoding='utf-8')
        return text.rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def is_supervisor(pid):
    if sys.platform != 'linux':
        interpreter, found, rest = (ps(pid, 'args') or '').partition(f" {ROOT / 'gateway.py'} _run")
        return bool(found) and rest[:1] in ('', ' ') and re.fullmatch(r'python[\w.]*', Path(interpreter).name, re.I) is not None
    try:
        argv = Path('/proc', str(pid), 'cmdline').read_bytes().decode().split('\0')
    except OSError:
        return False
    return argv[1:3] == [str(ROOT / 'gateway.py'), '_run'] and re.fullmatch(r'python[\w.]*', Path(argv[0]).name, re.I) is not None


def listeners(port):
    if sys.platform != 'linux':
        output = subprocess.run(['lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN', '-Fpn'],
                                capture_output=True, text=True, encoding='utf-8').stdout
        found, pid = [], None
        for line in output.splitlines():
            if line.startswith('p'):
                pid = int(line[1:])
            elif line.startswith('n') and pid is not None:
                found.append((line[1:], (pid,)))
        return found
    output = subprocess.run(['ss', '-H', '-ltnp', 'sport = :' + str(port)],
                            check=True, capture_output=True, text=True, encoding='utf-8').stdout
    return [(line.split()[3], tuple(int(pid) for pid in re.findall(r'pid=(\d+),', line)))
            for line in output.splitlines()]


def state(runtime):
    path = ordinary_path(runtime / 'pid.json')
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding='utf-8'))
    pid = value['pid']
    if type(pid) is not int or pid <= 1:
        raise ValueError('invalid process identity')
    current = identity(pid)
    if not current or current != value['identity']:
        return None
    return value if is_supervisor(pid) else None


def settings(runtime):
    return json.loads(ordinary_path(runtime / 'mcp_settings.json').read_text(encoding='utf-8'))


def child_environment(runtime_settings, store, runtime, port, package, dashboard=False):
    env = environment(runtime_settings, store)
    env.update(PORT=str(port), DISABLE_WEB='false' if dashboard else 'true', NODE_ENV='production',
               MCPHUB_SETTING_PATH=str(runtime / 'mcp_settings.json'),
               AGENTLOCKER_BIND=LOOPBACK, AGENTLOCKER_ENTRY=str(package / 'dist/index.js'),
               HOME=str(private_directory(runtime / 'home')))
    env['ADMIN_PASSWORD'] = store.resolve('secret://gateway-admin')
    env['JWT_SECRET'] = store.resolve('secret://gateway-jwt')
    return env


def token_value(store):
    if not store.has(TOKEN_SECRET):
        store.put(TOKEN_SECRET, secrets.token_hex(32))
    return store.resolve('secret://' + TOKEN_SECRET)


def apply_token(runtime, store, required):
    current = settings(runtime)
    keys = [key for key in current.get('bearerKeys', []) if key.get('id') != TOKEN_KEY]
    owned = len(keys) != len(current.get('bearerKeys', []))
    routing = current.setdefault('systemConfig', {}).setdefault('routing', {})
    if required:
        keys.append({'id': TOKEN_KEY, 'name': TOKEN_KEY, 'token': token_value(store),
                     'enabled': True, 'kind': 'system', 'accessType': 'all',
                     'allowedGroups': [], 'allowedServers': []})
        routing['enableBearerAuth'] = True
    elif owned:
        routing['enableBearerAuth'] = False
    current['bearerKeys'] = keys
    save(runtime / 'mcp_settings.json', current)


def client_headers(root, running):
    if not running or not running.get('require_token'):
        return {}
    return {'Authorization': 'Bearer ' + token_value(FileStore(root))}


def write_token_file(root, destination):
    token = token_value(FileStore(root))
    path = ordinary_path(destination)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        handle.write(TOKEN_VARIABLE + '=' + token + '\n')


def supervise(port, auto_refresh=False, dashboard=False, require_token=False):
    os.umask(0o077)
    runtime = ROOT / '.hub-store/gateway'
    with open(ordinary_path(runtime / 'supervisor.lock'), 'a', encoding='utf-8') as lock:
        os.chmod(runtime / 'supervisor.lock', 0o600)
        acquire(lock, wait=False)
        run_supervisor(runtime, port, auto_refresh, dashboard, require_token)


@installation_lock(exclusive=False)
def run_supervisor(runtime, port, auto_refresh, dashboard=False, require_token=False):
    store = FileStore(ROOT)
    check_node()
    check_runtime()
    package = runtime_package()
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    bootstrap = ordinary_path(runtime / 'bootstrap.mjs')
    bootstrap.write_text(BOOTSTRAP, encoding='utf-8')
    bootstrap.chmod(0o600)
    for name in ('gateway-admin', 'gateway-jwt'):
        if not store.has(name):
            store.put(name, secrets.token_hex(32))
    apply_token(runtime, store, require_token)
    log_lock = threading.Lock()
    known = set()
    with open(ordinary_path(runtime / 'gateway.log'), 'a', encoding='utf-8') as log:
        os.chmod(runtime / 'gateway.log', 0o600)

        def remember():
            with log_lock:
                known.update(part for name in store.names()
                             for part in store.resolve('secret://' + name).splitlines() if part)

        def diagnostic(line):
            with log_lock:
                try:
                    try:
                        import calllog
                        calllog.gateway_request(ROOT, line)
                    except (OSError, ValueError, KeyError, TypeError, AttributeError):
                        pass
                    line = redact_log(store, line)
                    for value in sorted(known, key=len, reverse=True):
                        line = line.replace(value, '[REDACTED]')
                    log.write(line)
                except (OSError, ValueError, KeyError, TypeError):
                    log.write('[gateway diagnostic suppressed]\n')
                log.flush()

        def output(child):
            for raw in child.stdout:
                try:
                    sync(settings(runtime), store)
                    diagnostic(raw.decode(errors='replace'))
                except (OSError, ValueError, KeyError, TypeError):
                    diagnostic('[gateway diagnostic suppressed]\n')

        def launch():
            from verify import gateway_fingerprints
            source_root = Path(json.loads(ordinary_path(runtime / 'source.json').read_text(encoding='utf-8'))['path'])
            fingerprints = gateway_fingerprints(ROOT, settings(runtime)) if source_root == ROOT else {}
            env = child_environment(settings(runtime), store, runtime, port, package, dashboard)
            remember()
            child = subprocess.Popen(['node', str(bootstrap)], cwd=runtime, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            reader = threading.Thread(target=output, args=(child,), daemon=True)
            reader.start()
            save(runtime / 'pid.json', {'verification_hashes': fingerprints, 'pid': os.getpid(), 'identity': identity(os.getpid()),
                                       'child': child.pid, 'port': port, 'auto_refresh': auto_refresh,
                                       'dashboard': dashboard, 'require_token': require_token})
            return child, reader

        def reap(child, reader):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
            child.wait()
            reader.join(timeout=5)
            child.stdout.close()

        refresher = None
        if auto_refresh:
            source = Path(json.loads(ordinary_path(runtime / 'source.json').read_text(encoding='utf-8'))['path'])
            refresher = BindingRefresher(source, store, REFRESH_INTERVAL, REFRESH_LEAD, diagnostic, stopped.is_set)
        process, reader = launch()
        try:
            while not stopped.wait(REFRESH_INTERVAL if auto_refresh else .2):
                if process.poll() is not None:
                    break
                try:
                    remember()
                    if refresher and refresher.check(settings(runtime)):
                        remember()
                        diagnostic('[auto-refresh] header changed; restarting gateway on same port\n')
                        reap(process, reader)
                        if stopped.is_set():
                            break
                        process, reader = launch()
                except (OSError, ValueError, KeyError, TypeError):
                    diagnostic('[auto-refresh] check failed; retry on next interval\n')
            reap(process, reader)
            sync(settings(runtime), store)
        finally:
            reap(process, reader)
            (runtime / 'pid.json').unlink(missing_ok=True)


def stop(runtime):
    current = state(runtime)
    if not current:
        return
    os.kill(current['pid'], signal.SIGTERM)
    for _ in range(200):
        if not state(runtime):
            if listeners(current['port']):
                raise SetupError(f"gateway stopped but port {current['port']} is still listening")
            return
        time.sleep(.1)
    raise ValueError('stop timed out')


def require_free(port):
    with socket.socket() as probe:
        if os.name != 'nt':
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((LOOPBACK, port))
        except OSError:
            raise SetupError(f'Gateway port unavailable: {port} is busy. Leave its listener alone and use another port '
                             f'with: {python_command()} gateway.py start --port <port>') from None


def start(runtime, port, auto_refresh=False, dashboard=False, require_token=False):
    require_supported()
    with installation_lock(exclusive=False):
        return start_runtime(runtime, port, auto_refresh, dashboard, require_token)


def start_runtime(runtime, port, auto_refresh=False, dashboard=False, require_token=False):
    if state(runtime):
        raise ValueError('already running')
    if not (runtime / 'mcp_settings.json').exists():
        raise SetupError(f'run {python_command()} gateway.py prepare first')
    check_node()
    prerequisite('ss' if sys.platform == 'linux' else 'lsof')
    check_runtime()
    require_free(port)
    source = ordinary_path(runtime / 'source.json')
    if source.exists():
        store = FileStore(ROOT)
        source_root = Path(json.loads(source.read_text(encoding='utf-8'))['path'])
        updated = drop_unset(ui_bindings(settings(runtime), source_root, store), store)
        updated = launch_settings(updated, source_root)
        save(runtime / 'mcp_settings.json', updated)
    arguments = [sys.executable, str(ROOT / 'gateway.py'), '_run', '--port', str(port)]
    if auto_refresh:
        arguments.append('--auto-refresh')
    if dashboard:
        arguments.append('--dashboard')
    if require_token:
        arguments.append('--require-token')
    process = subprocess.Popen(arguments,
                               cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        for _ in range(150):
            if process.poll() is not None:
                raise ValueError('startup failed')
            if state(runtime):
                try:
                    with opener.open(f'http://{LOOPBACK}:{port}/health', timeout=.3) as response:
                        if response.status == 200:
                            current = state(runtime)
                            found = listeners(port)
                            if current and found and all(
                                    address == f'{LOOPBACK}:{port}' and current['child'] in pids
                                    for address, pids in found):
                                return
                except OSError:
                    pass
            time.sleep(.1)
        raise ValueError('startup timed out')
    except BaseException:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=12)
        raise


def endpoint(port):
    return f'http://{LOOPBACK}:{port}/mcp'


def servers_line():
    import hub_ui
    result = hub_ui.gateway_connection(ROOT)
    if 'total' not in result:
        return 'servers: unknown'
    line = f"servers: {result['served']}/{result['total']} connected"
    if result['down']:
        line += '; failed: ' + ', '.join(item['name'] for item in result['down'])
    if result.get('skipped'):
        line += f"; skipped: {len(result['skipped'])}"
    return line


def skipped_lines():
    try:
        found = skipped(ROOT, FileStore(ROOT))
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return [f"skipped: {name} needs {', '.join('secret://' + ref for ref in refs)}; {SKIPPED_NEXT_STEP}"
            for name, refs in found.items()]


def startable():
    try:
        check_runtime()
    except SetupError:
        return False
    return True


@contextmanager
def lifecycle():
    runtime = private_directory(private_directory(ROOT / '.hub-store') / 'gateway')
    with open(ordinary_path(runtime / 'lifecycle.lock'), 'a', encoding='utf-8') as lock:
        os.chmod(runtime / 'lifecycle.lock', 0o600)
        acquire(lock)
        yield runtime


def launch():
    require_supported()
    check_runtime()
    with lifecycle() as runtime:
        if state(runtime):
            return
        require_free(PORT)
        prepare(ROOT, ROOT)
        start(runtime, PORT)


def enabled(source):
    get = source.get if isinstance(source, dict) else lambda name: getattr(source, name)
    return {name: True for name in ('dashboard', 'require_token') if get(name)}


def main():
    parser = QuietParser(description='Select a local gateway; Executor lifecycle stays user-managed')
    parser.add_argument('command', choices=('install', 'prepare', 'start', 'stop', 'status', 'sync', 'refresh', 'url', 'register-executor', 'token', '_run'))
    parser.add_argument('--server')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--port', type=int, default=PORT)
    parser.add_argument('--auto-refresh', action='store_true')
    parser.add_argument('--dashboard', action='store_true')
    parser.add_argument('--require-token', action='store_true')
    parser.add_argument('--env-file', type=Path)
    args = parser.parse_args()
    try:
        if not 1024 <= args.port <= 65535:
            raise ValueError('invalid port')
        if args.command == 'register-executor':
            from executor_sources import registration
            return registration(ROOT, args.server, args.apply)
        backend = executor_connector.configuration(ROOT)['backend']
        if backend == 'executor':
            return executor_connector.command(ROOT, args.command)
        require_supported()
        if args.command == 'url':
            current = state(ROOT / '.hub-store/gateway')
            if not current:
                raise ValueError('gateway is stopped')
            print(endpoint(current['port']))
            return 0
        if args.command == 'token':
            if not args.env_file:
                raise ValueError('--env-file is required')
            try:
                write_token_file(ROOT, args.env_file.resolve())
            except FileExistsError:
                raise SetupError('env file already exists; choose a new path') from None
            print('token written to ' + str(args.env_file.resolve()))
            return 0
        if args.command == '_run':
            supervise(args.port, args.auto_refresh, **enabled(args))
            return 0
        if args.command == 'install':
            install()
            print('gateway install complete; MCPHub ' + VERSION)
            return 0
        with lifecycle() as runtime:
            if args.command == 'prepare':
                if state(runtime):
                    raise ValueError('stop before prepare')
                prepare(ROOT, args.source.resolve())
            elif args.command == 'start':
                start(runtime, args.port, args.auto_refresh, **enabled(args))
            elif args.command == 'stop':
                stop(runtime)
            elif args.command == 'refresh':
                current = state(runtime)
                source = Path(json.loads(ordinary_path(runtime / 'source.json').read_text(encoding='utf-8'))['path'])
                if current:
                    stop(runtime)
                store = FileStore(ROOT)
                save(runtime / 'mcp_settings.json', drop_unset(ui_bindings(settings(runtime), source, store), store))
                if current:
                    start(runtime, current['port'], current.get('auto_refresh', False), **enabled(current))
            elif args.command == 'sync':
                print('OAuth servers synchronized:', sync(settings(runtime), FileStore(ROOT)))
            else:
                current = state(runtime)
                print('running' if current else 'stopped')
                if current:
                    print('url: ' + endpoint(current['port']))
        if args.command == 'status':
            if current:
                print(servers_line())
                for line in skipped_lines():
                    print(line)
            return 0 if current else 1
        print('gateway ' + args.command + ' complete')
        if args.command == 'start':
            print(servers_line())
            for line in skipped_lines():
                print(line)
        return 0
    except SetupError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('gateway operation failed; inspect protected diagnostics', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
