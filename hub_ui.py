import contextlib
import io
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
from types import SimpleNamespace

import hub
import importers
import scanner
import connect
import executor_connector
import verify
import os
from checks import checker, validators
from gateway_adapter import SKIPPED_NEXT_STEP, skipped
from mcp_endpoint import LegacyCredential, legacy_next_step, reject_legacy_credential, repo_file
from store import FileStore, ordinary_path
from ui_auth import Authentication
from connect import connect_snippets, gateway_connection as shared_gateway_connection


ASSETS = Path(__file__).resolve().parent / 'ui'


def auth_status(root, store):
    authentication = Authentication(root, store)
    manifest = hub.read_manifest(root)
    result = []
    for entry in manifest['mcp_servers']:
        definition = hub.read_json(repo_file(root, entry['path']))
        try:
            reject_legacy_credential(definition)
        except LegacyCredential as error:
            result.append({'name': entry['name'], 'status': 'Check failed', 'state': 'check_failed', 'label': 'Check failed',
                           'description': 'stdio', 'detail': str(error), 'error': f'{error}. Run {legacy_next_step()}',
                           'next_step': legacy_next_step(), 'legacy_credential': True})
            continue
        server = validators.mcp(definition)
        description = server['transport'] + (' · ' + urlsplit(server['url']).netloc if server.get('url') else '')
        if server['transport'] == 'http':
            result.append({**verify.enrich(root, server, authentication.status(server)), 'description': description})
            continue
        keys = authentication.stdio_references(server)
        refs = list(keys.values())
        origin = entry['provenance']['origin']
        typed = authentication.typed(entry['name'])
        copied = origin.startswith('scan-') and any(ref.startswith('scan-') and ref not in typed for ref in refs)
        result.append(verify.enrich(root, server, {'name': entry['name'], 'status': 'Credentials stored' if refs and all(store.has(ref) for ref in refs)
                       else 'Credentials needed' if refs else 'No credentials declared', 'description': description,
                       'secrets': list(keys), 'copied_from': connect.LABELS.get(origin[5:], origin[5:]) if copied else None}))
    return result


def state(runtime):
    from gateway import state as read
    return read(runtime)


def serving(names, tools, separator):
    ordered = sorted(names, key=len, reverse=True)
    return {next((name for name in ordered if tool.startswith(name + separator)), None) for tool in tools} - {None}


SILENT = 'Lists no tools through the gateway'
SILENT_NEXT_STEP = 'Press Check now on Sign in, then restart the gateway.'


def up_servers(names, tools, separator):
    live = serving(names, tools, separator)
    return [name for name in names if name in live]


def down_servers(root, names, tools, separator):
    try:
        entries = {entry['name']: entry for entry in auth_status(root, FileStore(root))}
    except (OSError, ValueError, KeyError, TypeError):
        entries = {}
    def reason(entry):
        if entry.get('state') == 'needs_signin':
            return 'Needs sign-in'
        return entry.get('error') or SILENT
    def row(name):
        why = reason(entries.get(name, {}))
        return {'name': name, 'reason': why, **({'next_step': SILENT_NEXT_STEP} if why == SILENT else {})}
    return [row(name) for name in names if name not in serving(names, tools, separator)]


def gateway_connection(root):
    result = shared_gateway_connection(root, state_reader=state)
    if result['status'] != 'running':
        if result.get('backend') != 'executor':
            import gateway
            result.update(startable=gateway.startable(), install=gateway.install_command())
        return result
    import gateway
    result['stoppable'] = result.get('backend') != 'executor' and root.resolve() == gateway.ROOT.resolve()
    if not (root / 'manifest.json').is_file():
        return result
    names = [entry['name'] for entry in hub.read_manifest(root)['mcp_servers']]
    try:
        left_out = skipped(root, FileStore(root))
    except (OSError, ValueError, KeyError, TypeError):
        left_out = {}
    result['skipped'] = [{'name': name, 'missing': refs, 'next_step': SKIPPED_NEXT_STEP}
                         for name, refs in left_out.items()]
    names = [name for name in names if name not in left_out]
    tools = result.get('tools')
    if tools is not None:
        result.update(total=len(names), served=len(serving(names, tools, '-')), up=up_servers(names, tools, '-'))
        result['down'] = down_servers(root, names, tools, '-')
    runtime = root / '.hub-store/gateway'
    if result.get('backend') != 'executor' and (runtime / 'pid.json').is_file():
        result['started_at'] = (runtime / 'pid.json').stat().st_mtime
        from executor_connector import Session
        try:
            from gateway import client_headers
            with Session(root, config={'backend': 'executor', 'url': result['url'], 'headers': {}},
                         headers=client_headers(root, state(runtime))) as session:
                listed = session.tools()
            from gateway import settings
            separator = settings(runtime).get('systemConfig', {}).get('nameSeparator') or '-'
            result.update(total=len(names), served=len(serving(names, [tool['name'] for tool in listed], separator)),
                          up=up_servers(names, [tool['name'] for tool in listed], separator),
                          down=down_servers(root, names, [tool['name'] for tool in listed], separator))
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return result


class SetupServer(HTTPServer):
    def __init__(self, root, port=0, host='127.0.0.1', scan_home=None, scan_platform=None, scan_projects=()):
        validators.require(host == '127.0.0.1', 'only numeric loopback is allowed')
        validators.require(type(port) is int and 0 <= port <= 65535, 'invalid port')
        self.root = Path(root).resolve()
        self.scan_home = Path(scan_home) if scan_home is not None else Path.home()
        self.scan_platform = scan_platform
        self.scan_projects = tuple(Path(p) for p in scan_projects)
        self.scan_env = dict(os.environ) if scan_home is None else {}
        self.scan_runner = None if scan_home is None else lambda command: ''
        self.store = FileStore(self.root)
        validators.require(self.store.path == self.root / '.hub-store', 'UI requires the private default store')
        self.authentication = Authentication(self.root, self.store)
        self.token = secrets.token_urlsafe(32)
        self.jobs = {}
        super().__init__((host, port), Handler)
        self.origin = 'http://127.0.0.1:' + str(self.server_port)

    def preview(self, kind, content):
        self.jobs = {key: job for key, job in self.jobs.items() if time.monotonic() - job['time'] < 900}
        validators.require(len(self.jobs) < 32, 'too many previews; restart setup')
        ticket = secrets.token_urlsafe(24)
        self.jobs[ticket] = {'kind': kind, 'content': content, 'time': time.monotonic()}
        return ticket

    def redact(self, text):
        for job in self.jobs.values():
            if job['kind'] == 'mcp':
                for value in job['content'][1].values():
                    text = text.replace(value, '[REDACTED]')
        return self.store.redact(text)

    def routine_details(self, manifest):
        details = {}
        for entry in manifest.get('routines', []):
            if entry['path'].startswith(validators.LOCAL + '/'):
                try:
                    routine = importers.json_object(importers.selected_path(self.root, entry['path']).read_text(encoding='utf-8'))
                    details[entry['id']] = {field: routine[field] for field in ('runner', 'schedule', 'timezone')}
                except (OSError, ValueError, KeyError):
                    pass
        return details

    def status(self):
        manifest = hub.read_manifest(self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            checked = checker.run(self.root)
        inventory = {kind: {'imported': [entry[key] for entry in manifest.get(kind, []) if entry['path'].startswith(validators.LOCAL + '/')],
                            'examples': sum(not entry['path'].startswith(validators.LOCAL + '/') for entry in manifest.get(kind, []))}
                     for kind, key in validators.COLLECTIONS.items()}
        inventory['routines']['details'] = self.routine_details(manifest)
        authentication = auth_status(self.root, self.store)
        for item in authentication:
            outcome = self.authentication.outcome(item['name'])
            if outcome:
                item['oauth_result'] = outcome
        return {'counts': {kind: len(manifest.get(kind, [])) for kind in validators.COLLECTIONS}, 'inventory': inventory,
                'check': 'Passed' if checked == 0 else 'Needs attention — run python3 hub.py check',
                'auth': authentication, 'snippets': connect_snippets(self.root),
                'gateway': gateway_connection(self.root)}

    def reviewable(self, kind, items, prompt_text=None):
        if kind == 'skill':
            return [(None, scanner.texts(item)) for item in items]
        if kind == 'routine':
            return [(None, [('prompt.md', prompt_text)] if prompt_text else []) for item in items]
        return [(item, []) for item in items]

    def stage_import(self, kind, content, credentials=None, prompt_text=None, approve=False):
        items = content if isinstance(content, list) else [content]
        manifest = hub.read_manifest(self.root)
        collection = {'mcp': 'mcp_servers', 'routine': 'routines', 'skill': 'skills'}[kind]
        key = validators.COLLECTIONS[collection]
        existing = {item[key] for item in manifest[collection]}
        names = [item.name if kind == 'skill' else item[key] for item in items]
        validators.require(len(names) == len(set(names)) and not existing.intersection(names), 'an item already exists')
        for name in names:
            relative = {'skill': f'skills/{name}', 'routine': f'routines/{name}', 'mcp': f'mcp/servers/{name}.json'}[kind]
            destination = self.root / validators.LOCAL / relative
            hub.ordinary_parents(destination.parent)
            validators.require(not destination.exists() and not destination.is_symlink(), 'destination exists')
        for payload, files in self.reviewable(kind, items, prompt_text):
            hub.screen(kind, payload, files, approve, self.root)
        scratch = self.root / '.scratch'
        hub.ordinary_parents(scratch)
        scratch.mkdir(mode=0o700, exist_ok=True)
        scratch.chmod(0o700)
        with tempfile.TemporaryDirectory(prefix='setup-', dir=scratch) as directory:
            for item in items:
                if kind == 'skill':
                    path = item
                else:
                    path = Path(directory) / ('routine.json' if kind == 'routine' else 'server.json')
                    hub.write_json(path, item)
                    path.chmod(0o600)
                    if kind == 'routine':
                        prompt = Path(directory) / item['prompt']
                        prompt.write_bytes((prompt_text if prompt_text is not None else 'Imported routine definition. Review and supply the prompt before use.\n').encode('utf-8'))
                        prompt.chmod(0o600)
                args = SimpleNamespace(command='import-' + kind, path=str(path), owner='agentlocker', origin='local-import', name=None, approve_risky=approve)
                with contextlib.redirect_stdout(io.StringIO()):
                    hub.import_item(args, root=self.root)
        for name, value in (credentials or {}).items():
            self.store.put(name, value)
        return {'imported': len(items)}

    def scan(self):
        inventory = scanner.Inventory(self.scan_home, self.scan_projects, self.scan_platform, self.scan_env, self.scan_runner, self.root)
        report = inventory.discover()
        report['searched'] = list(dict.fromkeys(path for agent in scanner.registry.SEARCHED for path in inventory.searched(agent)))
        return report

    def api(self, route, data):
        if route == '/api/scan':
            report = self.scan()
            self.jobs = {key: job for key, job in self.jobs.items() if job['kind'] != 'scan'}
            return {**report, 'ticket': self.preview('scan', report)}
        if route == '/api/scan/import':
            job = self.jobs[data['ticket']]
            validators.require(job['kind'] == 'scan' and time.monotonic() - job['time'] < 900, 'scan expired')
            ids = data['ids']
            validators.require(isinstance(ids, list) and ids and all(isinstance(i, str) for i in ids), 'select items')
            approved = data.get('approved', [])
            validators.require(isinstance(approved, list) and all(isinstance(i, str) for i in approved) and set(approved) <= set(ids), 'invalid approval')
            items = job['content']['items']
            allowed = {i['id'] for i in items if i['importable'] and i['status'] in ('new', 'connect')}
            allowed |= {i['id'] for i in items if i['status'] == 'needs_review' and i['id'] in approved and not any(f['blocking'] for f in i['review'])}
            validators.require(set(ids) <= allowed, 'invalid selection')
            result = scanner.apply(self.root, job['content'], ids, self.store, self.scan_home,
                                   self.scan_projects, self.scan_platform, self.scan_env, self.scan_runner, approved)
            result['rejected'] = sorted(set(ids) - set(result['imported']))
            result['counts']['rejected'] = len(result['rejected'])
            del self.jobs[data['ticket']]
            return result
        if route == '/api/connect':
            gateway = gateway_connection(self.root)
            found = dict(zip(connect.AGENTS, (self.scan_home / part for part in connect.PATHS[self.scan_platform or sys.platform])))
            found.update(connect.detect(self.scan_home, self.scan_platform, self.scan_env))
            agents = [{'agent': agent, 'label': connect.LABELS[agent], 'path': str(path),
                       'display_path': '~/' + path.relative_to(self.scan_home).as_posix() if path.is_relative_to(self.scan_home) else str(path),
                       'snippet': connect.render(self.root, agent, gateway),
                       'catalog_snippet': connect.render(self.root, agent)} for agent, path in found.items()]
            return {'agents': agents, 'gateway': gateway}
        if route == '/api/gateway/start':
            import gateway
            if executor_connector.configuration(self.root)['backend'] == 'executor':
                raise hub.Refused('Executor is user-managed; start it separately with executor web --foreground')
            try:
                gateway.launch()
            except gateway.SetupError as error:
                raise hub.Refused(str(error)) from None
            except (OSError, ValueError, subprocess.SubprocessError):
                raise hub.Refused('The gateway did not start. Run python3 gateway.py start to see why.') from None
            return {'gateway': gateway_connection(self.root)}
        if route == '/api/gateway/stop':
            import gateway
            validators.require(self.root == gateway.ROOT.resolve(), 'Setup can only stop its own locker gateway')
            validators.require(executor_connector.configuration(self.root)['backend'] != 'executor', 'Executor is user-managed; stop it separately')
            try:
                with gateway.lifecycle() as runtime:
                    gateway.stop(runtime)
            except (OSError, ValueError, subprocess.SubprocessError):
                raise hub.Refused('Gateway stop could not be confirmed. Check python3 gateway.py status before retrying.') from None
            return {'gateway': gateway_connection(self.root)}
        if route == '/api/auth/check':
            return verify.check(self.root, data['server'])
        if route == '/api/auth/bearer':
            return self.authentication.bearer(data['name'], data['header'], data['value'], data['scheme'])
        if route == '/api/auth/secret':
            return self.authentication.secret(data['name'], data['key'], data['value'])
        if route == '/api/auth/secret/remove':
            return self.authentication.remove_secret(data['name'], data['key'])
        if route == '/api/auth/oauth':
            return self.authentication.start(data['name'], self.origin + '/oauth/callback', data.get('client_id', ''))
        if route == '/api/auth/discover':
            return {'oauth': self.authentication.supports_oauth(data['name'])}
        if route == '/api/auth/refresh':
            return self.authentication.refresh(data['name'])
        if route == '/api/skills/preview':
            root = importers.selected_path(data['root'])
            found = importers.discover_skills(root)
            for item in found:
                item['review'] = hub.review('skill', None, scanner.texts(importers.selected_path(root, item['path'])), self.root)
            ticket = self.preview('skills', (root, found))
            return {'ticket': ticket, 'items': found}
        if route == '/api/mcp/preview':
            filename = Path(data['path']).name
            if filename == 'config.tom':
                raise hub.Refused('Could not read config.tom. Did you mean config.toml? Choose that filename or pick another supported configuration. Nothing was imported.')
            validators.require(filename in importers.CONFIG_NAMES, 'Choose a supported MCP configuration filename')
            try:
                text = importers.read_file(data['root'], data['path'], importers.CONFIG_NAMES)
                servers, credentials = importers.parse_mcp(text, filename)
            except OSError:
                raise hub.Refused('Could not read ' + filename + '. Check the containing folder, filename and permissions, or choose another file. Nothing was imported.') from None
            except ValueError:
                raise hub.Refused('Could not parse ' + filename + '. Check its JSON or TOML syntax and supported server fields, then preview again. Nothing was imported.') from None
            ticket = self.preview('mcp', (servers, credentials))
            return {'ticket': ticket, 'items': [{'name': server['name'], 'transport': server['transport'],
                     'credentials': '•••• (stored on import)' if server.get('env') or server.get('headers') else 'None declared',
                     'review': hub.review('mcp', server, [], self.root)} for server in servers]}
        if route == '/api/routines/preview':
            prompt_text = None
            if data.get('path'):
                path = Path(data['path'])
                validators.require(path.suffix == '.json', 'JSON file required')
                text = importers.read_file(data['root'], data['path'], {path.name})
            else:
                text = data['text']
            source_enabled = importers.json_object(text).get('enabled')
            routine = importers.parse_routine(text)
            if data.get('path'):
                prompt_path = Path(data['path']).parent / routine['prompt']
                if (Path(data['root']) / prompt_path).exists():
                    prompt_text = importers.read_file(data['root'], prompt_path, {routine['prompt']})
                    validators.safe_fragment(prompt_text)
            ticket = self.preview('routine', (routine, prompt_text))
            review = hub.review('routine', None, self.reviewable('routine', [routine], prompt_text)[0][1], self.root)
            return {'ticket': ticket, 'items': [{'name': routine['id'], 'status': ('Enabled in file' if source_enabled is True else 'Disabled in file' if source_enabled is False else 'Definition') + ' → imports paused',
                                               'enabled_in_file': source_enabled if type(source_enabled) is bool else None, 'runner': routine['runner'], 'review': review}]}
        if route == '/api/import':
            ticket = data['ticket']
            job = self.jobs[ticket]
            validators.require(time.monotonic() - job['time'] < 900, 'preview expired')
            kind, content = job['kind'], job['content']
            validators.require(kind in ('skills', 'mcp', 'routine'), 'invalid import ticket')
            approve = data.get('approved', False)
            validators.require(isinstance(approve, bool), 'invalid approval')
            if kind == 'skills':
                root, found = content
                selected = data['selected']
                validators.require(isinstance(selected, list) and selected and all(isinstance(item, str) for item in selected), 'select skills')
                allowed = {item['path'] for item in found}
                validators.require(set(selected) <= allowed, 'unknown selection')
                items = [importers.skill_package(importers.selected_path(root, relative)) for relative in selected]
                result = self.stage_import('skill', items, approve=approve)
            elif kind == 'mcp':
                result = self.stage_import('mcp', content[0], content[1], approve=approve)
            else:
                result = self.stage_import('routine', content[0], prompt_text=content[1], approve=approve)
            del self.jobs[ticket]
            return result
        raise ValueError('unknown operation')


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def allowed(self, callback=False):
        authority = self.server.origin.removeprefix('http://')
        return (self.headers.get_all('Host') == [authority]
                and (callback or not self.headers.get_all('Origin') or self.headers.get_all('Origin') == [self.server.origin])
                and (callback or self.headers.get('Sec-Fetch-Site') not in ('cross-site', 'same-site')))

    def reply(self, status, value, content_type='application/json', redact=True):
        body = json.dumps(value) if content_type == 'application/json' else value
        if content_type == 'application/json':
            body = (self.server.redact(body) if redact else body).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type + ('; charset=utf-8' if content_type.startswith('text/') or content_type in ('application/json', 'image/svg+xml') else ''))
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        callback = urlsplit(self.path).path == '/oauth/callback'
        if not self.allowed(callback):
            return self.reply(403, {'error': 'Local setup requests only'})
        try:
            if callback:
                self.server.authentication.callback(urlsplit(self.path).query)
                return self.reply(200, b'<!doctype html><title>Credentials saved</title><h1>Credentials saved, not verified yet</h1><p>Return to the setup tab. You can close this tab.</p>', 'text/html')
            if self.path == '/favicon.ico':
                return self.reply(200, ordinary_path(ASSETS / 'favicon.ico').read_bytes(), 'image/x-icon')
            if self.path == '/api/session':
                return self.reply(200, {'token': self.server.token})
            if urlsplit(self.path).path == '/api/calls':
                query = parse_qs(urlsplit(self.path).query)
                import calllog
                return self.reply(200, calllog.read(self.server.root, query.get('since', ['0'])[0]))
            if self.path == '/api/status':
                return self.reply(200, self.server.status())
            assets = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
            assets['/favicon.svg'] = ('favicon.svg', 'image/svg+xml')
            for screen in ('welcome', 'scan', 'signin', 'connect'):
                assets['/' + screen + '.js'] = (screen + '.js', 'text/javascript')
                assets['/' + screen + '.css'] = (screen + '.css', 'text/css')
            assets['/api.js'] = ('api.js', 'text/javascript')
            for font in ('inter-latin-wght-normal', 'geist-mono-latin-400-normal', 'geist-mono-latin-500-normal',
                         'ibm-plex-sans-latin-400-normal', 'ibm-plex-sans-latin-500-normal',
                         'ibm-plex-sans-latin-600-normal', 'ibm-plex-mono-latin-400-normal'):
                assets['/fonts/' + font + '.woff2'] = ('fonts/' + font + '.woff2', 'font/woff2')
            if self.path not in assets:
                return self.reply(404, {'error': 'Not found'})
            filename, mime = assets[self.path]
            return self.reply(200, ordinary_path(ASSETS / filename).read_bytes(), mime)
        except (ImportError, OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            return self.reply(400, {'error': 'Sign-in could not finish. Return to setup to retry or use a token. No new credentials were saved.' if callback else 'Unable to read locker status. Run python3 hub.py check.'})

    def do_POST(self):
        if not self.allowed() or self.headers.get_all('X-CSRF-Token') != [self.server.token]:
            return self.reply(403, {'error': 'Invalid setup session. Reload this page.'})
        if self.path == '/api/connect/write':
            return self.reply(404, {'error': 'Not found'})
        try:
            validators.require(self.path.startswith('/api/'), 'API route required')
            validators.require(not self.headers.get('Transfer-Encoding'), 'unsupported encoding')
            validators.require(len(self.headers.get_all('Content-Length', [])) == 1, 'length required')
            length = int(self.headers['Content-Length'])
            if length > importers.LIMIT:
                return self.reply(413, {'error': 'File or request exceeds 1 MB'})
            validators.require(length > 0 and self.headers.get('Content-Type') == 'application/json', 'JSON required')
            body = self.rfile.read(length)
            validators.require(len(body) == length, 'incomplete body')
            data = importers.json_object(body.decode('utf-8'))
            validators.require(isinstance(data, dict), 'object required')
            result = self.server.api(self.path, data)
            return self.reply(200, result, redact=self.path != '/api/auth/oauth')
        except hub.Refused as refusal:
            return self.reply(400, {'error': str(refusal)})
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            return self.reply(400, {'error': 'Operation could not be completed. Check the selected input, folder and filename, then try again.'})


def serve(root, port=0, scan_home=None, scan_platform=None, scan_projects=()):
    with SetupServer(root, port, scan_home=scan_home, scan_platform=scan_platform, scan_projects=scan_projects) as server:
        print('Open agentlocker setup: ' + server.origin + '/', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
