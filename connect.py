import copy
import datetime
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

from client_paths import codex_home
from importers import json_object
from portable import python_command
from store import ordinary_path

SKILLS_REACH = ('every imported skill is already readable from any connected agent through the agentlocker MCP tools '
                '(hub_list_skills, hub_get_item); link --target <a project folder> additionally places them as native skills inside that one project')


TOKEN_VARIABLE = 'AGENTLOCKER_GATEWAY_TOKEN'
AGENTS = ('claude', 'codex', 'cursor', 'opencode', 'claude_desktop', 'gemini', 'windsurf', 'vscode', 'hermes')
LABELS = {'claude': 'Claude', 'codex': 'Codex', 'cursor': 'Cursor', 'opencode': 'OpenCode',
          'claude_desktop': 'Claude Desktop', 'gemini': 'Gemini CLI', 'windsurf': 'Windsurf',
          'vscode': 'VS Code', 'hermes': 'Hermes Agent'}
PATHS = {
    'linux': ('.claude.json', '.codex/config.toml', '.cursor/mcp.json', '.config/opencode/opencode.json',
              '.config/Claude/claude_desktop_config.json', '.gemini/settings.json',
              '.codeium/windsurf/mcp_config.json', '.config/Code/User/mcp.json', '.hermes/config.yaml'),
    'darwin': ('.claude.json', '.codex/config.toml', '.cursor/mcp.json', '.config/opencode/opencode.json',
               'Library/Application Support/Claude/claude_desktop_config.json', '.gemini/settings.json',
               '.codeium/windsurf/mcp_config.json', 'Library/Application Support/Code/User/mcp.json', '.hermes/config.yaml'),
    'win32': ('.claude.json', '.codex/config.toml', '.cursor/mcp.json', '.config/opencode/opencode.json',
              'AppData/Roaming/Claude/claude_desktop_config.json', '.gemini/settings.json',
              '.codeium/windsurf/mcp_config.json', 'AppData/Roaming/Code/User/mcp.json', '.hermes/config.yaml'),
}


MINIMUM_PYTHON = (3, 10)


class TomlUnavailable(ValueError):
    pass


def toml(text):
    try:
        import tomllib
    except ImportError:
        raise TomlUnavailable('Codex TOML requires Python 3.11 or later') from None
    return tomllib.loads(text)


def detect(home, platform=None, env=None):
    home = Path(home).absolute()
    platform = platform or sys.platform
    platform = 'win32' if platform.startswith('win') else platform
    if platform not in PATHS:
        raise ValueError('unsupported platform')
    env = os.environ if env is None else env
    paths = dict(zip(AGENTS, (home / part for part in PATHS[platform])))
    if platform == 'win32' and env.get('APPDATA'):
        roaming = Path(env['APPDATA'])
        paths['claude_desktop'] = roaming / 'Claude/claude_desktop_config.json'
        paths['vscode'] = roaming / 'Code/User/mcp.json'
    paths['codex'] = codex_home(home, env) / 'config.toml'
    if env.get('CLAUDE_CONFIG_DIR'):
        paths['claude'] = Path(env['CLAUDE_CONFIG_DIR']) / '.claude.json'
    if env.get('XDG_CONFIG_HOME'):
        paths['opencode'] = Path(env['XDG_CONFIG_HOME']) / 'opencode/opencode.json'
    found = {}
    for agent, path in paths.items():
        markers = [path, path.parent]
        if agent == 'claude':
            markers = [path, Path(env['CLAUDE_CONFIG_DIR']) if env.get('CLAUDE_CONFIG_DIR') else home / '.claude']
        if agent == 'cursor':
            markers += [home / ('Library/Application Support/Cursor' if platform == 'darwin' else
                               'AppData/Roaming/Cursor' if platform == 'win32' else '.config/Cursor')]
        if agent == 'windsurf':
            markers += [home / ('Library/Application Support/Windsurf' if platform == 'darwin' else
                                'AppData/Roaming/Windsurf' if platform == 'win32' else '.codeium/windsurf')]
        if agent == 'vscode':
            markers += [path.parent]
        if any(marker.exists() for marker in markers):
            found[agent] = path.absolute()
    return found


def quote_path(path):
    if sys.platform.startswith('win'):
        return f'"{path}"'
    return shlex.quote(str(path))


def undo_command(backup, path):
    if sys.platform.startswith('win'):
        return f'cmd /c copy /Y {quote_path(backup)} {quote_path(path)}'
    return 'cp ' + quote_path(backup) + ' ' + quote_path(path)


def interpreter():
    if not sys.platform.startswith('win'):
        found = shutil.which('python3')
        if found and os.path.isabs(found):
            code = 'import sys; sys.exit(sys.version_info < %r)' % (MINIMUM_PYTHON,)
            try:
                if subprocess.run([found, '-c', code], capture_output=True, timeout=30).returncode == 0:
                    return found
            except (OSError, subprocess.SubprocessError):
                pass
    return sys.executable


def shown_root(root):
    root = Path(root)
    candidates = [os.environ.get('PWD'), os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else None]
    for candidate in candidates:
        if candidate and os.path.isabs(candidate):
            try:
                if Path(candidate).samefile(root):
                    return Path(os.path.abspath(candidate))
            except OSError:
                pass
    return Path(os.path.abspath(root))


def entries(root, agent, gateway=None, absolute=True):
    root = Path(os.path.abspath(root)) if absolute else Path(root)
    endpoint = str(root / 'mcp_endpoint.py')
    python = interpreter() if absolute or sys.platform.startswith('win') else 'python3'
    result = {'agentlocker': {'command': python, 'args': [endpoint]}}
    if agent == 'opencode':
        result['agentlocker'] = {'type': 'local', 'command': [python, endpoint]}
    if agent == 'vscode':
        result['agentlocker']['type'] = 'stdio'
    if gateway and gateway.get('status') == 'running' and agent != 'claude_desktop':
        snippets = gateway.get('snippets', {})
        gateway_url = gateway.get('url') or json.loads(snippets['Claude'])['mcpServers']['agentlocker-gateway']['url']
        if agent == 'codex':
            result.update(toml(snippets['Codex'])['mcp_servers'])
        elif agent in ('gemini', 'vscode', 'windsurf', 'hermes'):
            remote = {'gemini': {'httpUrl': gateway_url}, 'vscode': {'type': 'http', 'url': gateway_url},
                      'windsurf': {'serverUrl': gateway_url}, 'hermes': {'url': gateway_url}}[agent]
            if gateway.get('tokenRequired'):
                reference = '${env:' + TOKEN_VARIABLE + '}' if agent in ('vscode', 'windsurf') else '${' + TOKEN_VARIABLE + '}'
                remote['headers'] = {'Authorization': 'Bearer ' + reference}
            result['agentlocker-gateway'] = remote
        else:
            source = 'Cursor' if agent == 'opencode' else LABELS[agent]
            remote = json.loads(snippets[source])['mcpServers']['agentlocker-gateway']
            if agent == 'opencode':
                remote = {**remote, 'type': 'remote'}
                if 'headers' in remote:
                    remote['headers'] = {k: re.sub(r'\$\{(?:env:)?([^}]+)\}', r'{env:\1}', v)
                                         for k, v in remote['headers'].items()}
            result['agentlocker-gateway'] = remote
        remote = result['agentlocker-gateway']
        parsed = urlsplit(remote.get('url') or remote.get('httpUrl') or remote.get('serverUrl', ''))
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('unsafe gateway URL')
    return result


def render_toml(servers):
    lines = []
    for name, entry in servers.items():
        lines.append('[mcp_servers.' + name + ']')
        for key, value in entry.items():
            if not isinstance(value, dict):
                lines.append(key + ' = ' + json.dumps(value, ensure_ascii=False))
        for key, value in entry.items():
            if isinstance(value, dict):
                lines.append('[mcp_servers.' + name + '.' + key + ']')
                lines.extend(json.dumps(k) + ' = ' + json.dumps(v) for k, v in value.items())
        lines.append('')
    return '\n'.join(lines)


def render(root, agent, gateway=None, absolute=True):
    servers = entries(root, agent, gateway, absolute=absolute)
    if agent == 'hermes':
        lines = ['mcp_servers:']
        for name, entry in servers.items():
            lines.append(f'  {name}:')
            if 'command' in entry:
                lines.extend([f'    command: {json.dumps(entry["command"])}',
                              f'    args: {json.dumps(entry["args"])}'])
            else:
                lines.append(f'    url: {json.dumps(entry["url"])}')
                if 'headers' in entry:
                    lines.append('    headers:')
                    lines.extend(f'      {json.dumps(k)}: {json.dumps(v)}' for k, v in entry['headers'].items())
        return '\n'.join(lines)
    if agent == 'codex':
        return render_toml(servers)
    key = 'mcp' if agent == 'opencode' else 'servers' if agent == 'vscode' else 'mcpServers'
    return json.dumps({key: servers}, indent=2)


def connect_snippets(root):
    return {LABELS[agent]: render(root, agent, absolute=False) for agent in ('claude', 'codex', 'cursor')}


def gateway_connection(root, state_reader=None):
    python = python_command()
    hint = f'{python} gateway.py prepare && {python} gateway.py start'
    try:
        import executor_connector
        if executor_connector.configuration(root)['backend'] == 'executor':
            return executor_connector.connection(root)
        from gateway import state
        from gateway_adapter import LOOPBACK
        current = (state_reader or state)(root / '.hub-store/gateway')
        port = current['port'] if current else None
        if type(port) is not int or not 1024 <= port <= 65535:
            return {'status': 'stopped', 'command': hint, 'snippets': {}}
    except (ImportError, OSError, ValueError, KeyError, TypeError):
        return {'status': 'stopped', 'command': hint, 'snippets': {}}
    url = f'http://{LOOPBACK}:{port}/mcp'
    claude = {'type': 'http', 'url': url}
    cursor = {'url': url}
    codex = '[mcp_servers.agentlocker-gateway]\nurl = ' + json.dumps(url) + '\n'
    if current.get('require_token'):
        claude['headers'] = {'Authorization': 'Bearer ${' + TOKEN_VARIABLE + '}'}
        cursor['headers'] = {'Authorization': 'Bearer ${env:' + TOKEN_VARIABLE + '}'}
        codex += 'bearer_token_env_var = ' + json.dumps(TOKEN_VARIABLE) + '\n'
    return {'status': 'running', 'url': url, 'command': hint, 'tokenRequired': bool(current.get('require_token')), 'snippets': {
        'Claude': json.dumps({'mcpServers': {'agentlocker-gateway': claude}}, indent=2),
        'Cursor': json.dumps({'mcpServers': {'agentlocker-gateway': cursor}}, indent=2),
        'Codex': codex}}


def ordinary(path):
    ordinary_path(path)
    for item in (path, *path.parents):
        if item.exists() and not (item.is_file() if item == path else item.is_dir()):
            raise ValueError('refusing non-ordinary destination')


def table_headers(text):
    multiline = None
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if multiline is None and stripped.startswith('['):
            header = stripped.split('#', 1)[0].strip()
            try:
                value = toml(header + '\n__locker_probe = 0\n')
                path = []
                while isinstance(value, dict) and len(value) == 1:
                    key, value = next(iter(value.items()))
                    path.append(key)
                if path and path[-1] == '__locker_probe':
                    yield offset, path[:-1]
                else:
                    yield offset, []
            except ValueError:
                yield offset, []
        i = 0
        while i < len(line):
            if multiline:
                if line.startswith(multiline, i):
                    i += 3
                    multiline = None
                elif multiline == '"""' and line[i] == '\\':
                    i += 2
                else:
                    i += 1
            elif line[i] == '#':
                break
            elif line.startswith(('"""', "'''"), i):
                multiline = line[i:i + 3]
                i += 3
            elif line[i] in ('"', "'"):
                quote = line[i]
                i += 1
                while i < len(line):
                    if line[i] == '\\' and quote == '"':
                        i += 2
                    elif line[i] == quote:
                        i += 1
                        break
                    else:
                        i += 1
            else:
                i += 1
        offset += len(line)


def merge_toml(text, servers):
    before = toml(text)
    expected = copy.deepcopy(before)
    group = expected.setdefault('mcp_servers', {})
    if not isinstance(group, dict):
        raise ValueError('invalid MCP collection')
    if all(group.get(k) == v for k, v in servers.items()):
        return text
    group.update(servers)
    headers = list(table_headers(text)) + [(len(text), [])]
    chunks = []
    position = 0
    for (start, path), (end, _) in zip(headers, headers[1:]):
        if len(path) >= 2 and path[0] == 'mcp_servers' and path[1] in servers:
            chunks.append(text[position:start])
            chunks.extend(line for line in text[start:end].splitlines(keepends=True) if line.lstrip().startswith('#'))
            position = end
    chunks.append(text[position:])
    newline = '\r\n' if '\r\n' in text else '\n'
    result = ''.join(chunks)
    result += (newline if result and not result.endswith('\n') else '') + newline
    result += render_toml(servers).replace('\n', newline)
    if toml(result) != expected:
        raise ValueError('unsupported TOML layout; existing configuration preserved')
    return result


def skip_space(text, index):
    while text[index] in ' \t\r\n':
        index += 1
    return index


def json_members(text, start):
    decoder = json.JSONDecoder()
    found = []
    index = skip_space(text, start + 1)
    while text[index] != '}':
        key, position = decoder.raw_decode(text, index)
        value = skip_space(text, skip_space(text, position) + 1)
        _, end = decoder.raw_decode(text, value)
        found.append((key, index, value, end))
        index = skip_space(text, end)
        if text[index] == ',':
            index = skip_space(text, index + 1)
    return found, index


def line_indent(text, index):
    line = text[text.rfind('\n', 0, index) + 1:index]
    return line[:len(line) - len(line.lstrip(' \t'))]


def splice_members(text, start, updates, unit, newline, inline=None):
    found, close = json_members(text, start)
    existing = {key: (value, end) for key, _, value, end in found}
    compact = unit is None or bool(found) and '\n' not in text[start:close]
    if inline is None:
        inline = compact or bool(found) and '\n' not in text[found[0][2]:found[0][3]]
    base = line_indent(text, start)
    child = line_indent(text, found[-1][1]) if found and not compact else base + (unit or '')

    def dump(value, indent, flat):
        if flat:
            return json.dumps(value, ensure_ascii=False)
        return json.dumps(value, indent=unit, ensure_ascii=False).replace('\n', newline + indent)

    edits = []
    added = []
    current = json.loads(text[start:close + 1])
    for key, value in updates.items():
        if key in existing:
            if current[key] != value:
                begin, end = existing[key]
                member = next(item[1] for item in found if item[0] == key)
                edits.append((begin, end, dump(value, line_indent(text, member), '\n' not in text[begin:end])))
        else:
            added.append(json.dumps(key, ensure_ascii=False) + ': ' + dump(value, child, inline))
    if added:
        if compact:
            body = ', '.join(added)
            edits.append((found[-1][3], found[-1][3], ', ' + body) if found else (start + 1, close, body))
        else:
            body = (',' + newline).join(child + item for item in added)
            if found:
                edits.append((found[-1][3], found[-1][3], ',' + newline + body))
            else:
                edits.append((start + 1, close, newline + body + newline + base))
    for begin, end, replacement in sorted(edits, reverse=True):
        text = text[:begin] + replacement + text[end:]
    return text


def merge_json(text, agent, servers):
    value = json_object(text) if text.strip() else {}
    if not isinstance(value, dict):
        raise ValueError('configuration must be an object')
    key = 'mcp' if agent == 'opencode' else 'servers' if agent == 'vscode' else 'mcpServers'
    group = value.setdefault(key, {})
    if not isinstance(group, dict):
        raise ValueError('invalid MCP collection')
    if all(group.get(k) == v for k, v in servers.items()):
        return text
    if not text.strip():
        return json.dumps({key: {**group, **servers}}, indent=2) + '\n'
    match = re.search(r'\n([ \t]+)"', text)
    unit = match[1] if match else None
    newline = '\r\n' if '\r\n' in text else '\n'
    root = text.index('{')
    members, _ = json_members(text, root)
    for name, _, begin, _ in members:
        if name == key:
            return splice_members(text, begin, servers, unit, newline)
    return splice_members(text, root, {key: servers}, unit, newline, inline=False)


def jsonc_only(path, agent):
    return agent == 'opencode' and not path.exists() and path.with_suffix('.jsonc').exists()


def opencode_pair(path, agent):
    alternative = path.with_suffix('.jsonc')
    return alternative if agent == 'opencode' and path.exists() and alternative.exists() else None


def scratch_part(root):
    root = Path(root).resolve()
    scratch = next((part for part in root.parts if part in ('.scratch', '.worktrees')), None)
    if scratch:
        return scratch
    try:
        if (root / '.git').is_file():
            return '.git'
    except OSError:
        pass
    return None


def syntax_error(path, agent):
    try:
        ordinary(path)
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        text = path.read_bytes().decode('utf-8')
        if agent == 'codex':
            toml(text)
        elif text.strip():
            json_object(text)
    except TomlUnavailable:
        raise
    except ValueError as error:
        return str(error)
    except OSError:
        return None
    return None


def warn(message, output):
    output.flush()
    print(message, file=sys.stderr)


def manual_toml(root, gateway):
    text = render(root, 'codex')
    if gateway.get('status') == 'running':
        text += '\n' + gateway['snippets']['Codex']
    return text


def display(path, home):
    try:
        return '~/' + path.relative_to(home).as_posix()
    except ValueError:
        return str(path)


def plan(path, agent, servers):
    ordinary(path)
    if jsonc_only(path, agent):
        raise ValueError('OpenCode JSONC requires manual merge; existing configuration preserved')
    before = path.read_bytes() if path.exists() else None
    text = before.decode('utf-8') if before is not None else ''
    after = merge_toml(text, servers) if agent == 'codex' else merge_json(text, agent, servers)
    return path, before, after.encode('utf-8')


def file_snapshot(path):
    try:
        with path.open('rb') as stream:
            identity = os.fstat(stream.fileno())
            return stream.read(), (identity.st_dev, identity.st_ino)
    except FileNotFoundError:
        return None, None


def save(change):
    path, before, after = change
    ordinary(path)
    current, identity = file_snapshot(path)
    if current != before:
        raise ValueError('destination changed since preview')
    if before == after:
        return None
    backup = None
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        if before is not None:
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(after)
            stream.flush()
            os.fsync(stream.fileno())
        ordinary(path)
        latest, latest_identity = file_snapshot(path)
        if latest != current or latest_identity != identity:
            raise ValueError('destination changed while preparing update')
        if before is not None:
            timestamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            backup = path.with_name(path.name + '.' + timestamp + '.bak')
            descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(before)
        ordinary(path)
        latest, latest_identity = file_snapshot(path)
        if latest != current or latest_identity != identity:
            if backup is not None:
                backup.unlink()
                backup = None
            raise ValueError('destination changed while preparing update')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return backup


def run(args, root, env=None, stdin=None, output=None):
    stdin = sys.stdin if stdin is None else stdin
    output = sys.stdout if output is None else output
    import remote_connect
    if getattr(args, 'remote', False):
        return remote_connect.run(args, output, root)
    if getattr(args, 'url', None) is not None:
        raise ValueError('--url requires --remote')
    if set(args.agent or ()) - set((*AGENTS, 'grok')):
        raise ValueError('hosted clients require --remote')
    home = Path(args.home).absolute() if args.home else Path.home()
    environment = dict(os.environ if env is None else env)
    if args.home and env is None:
        for key in ('CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'XDG_CONFIG_HOME', 'APPDATA'):
            environment.pop(key, None)
    found = detect(home, args.platform, environment)
    requested = set(args.agent or AGENTS)
    gateway = gateway_connection(Path(root))
    checkout = shown_root(root)
    scratch = scratch_part(root)
    if scratch:
        location = 'uses Git directory indirection (.git file)' if scratch == '.git' else f'is under {scratch}'
        print(f'WARNING: {checkout} {location}; deleting it breaks every connected client. Clone agentlocker somewhere permanent and rerun.', file=output)
    detected = 0
    pending = []
    skipped = False
    for agent, path in found.items():
        if agent not in requested:
            continue
        missing = agent in ('cursor', 'opencode') and not (path.exists() or path.is_symlink()) and not jsonc_only(path, agent)
        detected += not missing
        try:
            if agent in ('hermes', 'windsurf'):
                rendered = render(checkout, agent, gateway)
                print(f'{LABELS[agent]} — {path}', file=output)
                print(rendered, file=output)
                note = ('YAML cannot be merged safely without a YAML parser; merge this top-level mcp_servers block manually.'
                        if agent == 'hermes' else
                        'Windsurf is print-only because its current official documentation redirects to Devin Cascade documentation and does not verify the Windsurf user path; merge manually.')
                print(f'{LABELS[agent]} is print-only: {note}', file=output)
                if args.write:
                    warn(f'{agent}: --write is unsupported for this client; {display(path, home)} left unchanged', output)
                    skipped = True
                continue
            problem = syntax_error(path, agent)
            if problem is not None:
                kind = 'TOML' if agent == 'codex' else 'JSON'
                warn(f'{agent}: {display(path, home)} is not valid {kind} ({problem}); fix it and rerun', output)
                skipped = True
                continue
            rendered = render(checkout, agent, gateway)
            if missing:
                print(f'{LABELS[agent]} — not detected; to add it, create {path} with:', file=output)
                print(rendered, file=output)
                continue
            print(f'{LABELS[agent]} — {path}', file=output)
            print(rendered, file=output)
            if agent in ('claude', 'codex', 'opencode'):
                print('Skills: ' + SKILLS_REACH + '. Run: ' + python_command() + ' ' + quote_path(checkout / 'hub.py') + ' link --target <a project folder>', file=output)
            pair = opencode_pair(path, agent)
            if args.write and pair:
                warn(f'OpenCode: both {path} and {pair} exist and the loaded one is not documented; refusing to write either. Merge manually or remove one.', output)
                skipped = True
            elif args.write and jsonc_only(path, agent):
                print('OpenCode: manual merge (jsonc) - paste this snippet into ' + str(path.with_suffix('.jsonc')), file=output)
            elif args.write:
                change = plan(path, agent, entries(checkout, agent, gateway))
                if change[1] == change[2]:
                    print('already connected', file=output)
                else:
                    print('@@ ' + agent + ': add/replace only ' + ', '.join(entries(checkout, agent, gateway)) + ' (timestamped .bak of an existing file before atomic replace)', file=output)
                    pending.append((agent, change))
        except TomlUnavailable as error:
            print(f'{LABELS[agent]} — add by hand to {path}:', file=output)
            print(manual_toml(checkout, gateway), file=output)
            warn(f'{agent}: {error}; {display(path, home)} left unchanged, add the block printed above by hand', output)
            skipped = True
    if not detected:
        print('No matching configured agents detected.', file=output)
    print('Grok Bot: follow docs/grok-bot-bootstrap.md; enable the hosted skill per bot.', file=output)
    if pending and not args.yes:
        if not stdin.isatty():
            print('Refusing --write without --yes or interactive confirmation.', file=output)
            return 1
        print('Write these backed-up connections? [y/N] ', end='', file=output, flush=True)
        if stdin.readline().strip().lower() not in ('y', 'yes'):
            print('Cancelled; no settings changed.', file=output)
            return 1
    for agent, change in pending:
        try:
            backup = save(change)
        except (OSError, ValueError) as error:
            warn(f'{agent}: could not write {display(change[0], home)} ({getattr(error, "strerror", None) or error})', output)
            skipped = True
            continue
        print('Connected: ' + str(change[0]), file=output)
        if backup:
            print('Backup: ' + str(backup), file=output)
            print('Undo: ' + undo_command(backup, change[0]), file=output)
    return 1 if skipped else 0


def cli(args, root):
    return run(args, root)


def register(commands, root):
    command = commands.add_parser('connect', help='Print connections for detected agents; --write opts into backed-up config merges.')
    command.add_argument('--home')
    command.add_argument('--platform', choices=tuple(PATHS))
    from remote_connect import AGENTS as REMOTE_AGENTS
    command.add_argument('--agent', choices=tuple(dict.fromkeys((*AGENTS, *REMOTE_AGENTS))), nargs='+', action='extend')
    command.add_argument('--remote', action='store_true', help='Print remote OAuth catalog instructions; never reads or writes client settings.')
    command.add_argument('--url', help='Override public_url for --remote with your HTTPS catalog /mcp URL.')
    command.add_argument('--write', action='store_true')
    command.add_argument('--yes', action='store_true')
    command.set_defaults(action=lambda args: cli(args, root))
