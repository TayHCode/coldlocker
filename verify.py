import hashlib
from http.client import HTTPException
import json
import os
import tempfile
import time

import executor_connector
from checks import validators
from mcp_endpoint import LegacyCredential, legacy_next_step, reject_legacy_credential, repo_file
from portable import restrict
from store import FileStore, ordinary_path, reference_name
from ui_auth import Authentication, entry_prefix


START = "The gateway isn't running."


def definition(root, name):
    manifest = validators.layered(root, validators.manifest(json.loads(repo_file(root, 'manifest.json').read_text(encoding='utf-8'))))
    entry = next((e for e in manifest['mcp_servers'] if e['name'] == name), None)
    if entry is None:
        raise ValueError('unknown server')
    return validators.mcp(reject_legacy_credential(json.loads(repo_file(root, entry['path']).read_text(encoding='utf-8'))))


def identity(root, name):
    store = FileStore(root)
    server = definition(root, name)
    refs = {reference_name(v) for field in ('env', 'headers') for v in server.get(field, {}).values()
            if v.startswith('secret://')}
    refs.update(Authentication(root, store).argument_references(server.get('args', [])).values())
    prefix = entry_prefix(name)
    refs.update(key for key in store.names() if key.startswith(prefix + '-'))
    values = {key: hashlib.sha256(store.resolve('secret://' + key).encode()).hexdigest()
              if store.has(key) else None for key in refs}
    config = executor_connector.configuration(root)
    for ref in config.get('headers', {}).values():
        key = reference_name(ref)
        values[key] = hashlib.sha256(store.resolve(ref).encode()).hexdigest() if store.has(key) else None
    return hashlib.sha256(json.dumps([server, values, config], sort_keys=True).encode()).hexdigest()


def gateway_fingerprints(root, settings):
    from gateway_render import placeholder, render
    expected = render(root)['mcpServers']
    store = FileStore(root)
    authentication = Authentication(root, store)
    fingerprints = {}
    for name, config in expected.items():
        binding = authentication.metadata(name)
        if binding:
            headers = dict(config.get('headers', {}))
            for header, target in binding.get('headers', {}).items():
                headers = {key: value for key, value in headers.items() if key.lower() != header.lower()}
                headers[header] = placeholder('secret://' + target)
            config['headers'] = headers
        actual = settings['mcpServers'].get(name, {})
        fields = ('type', 'command', 'args', 'url', 'env', 'headers')
        if all(config.get(field, {} if field in ('env', 'headers') else None) ==
               actual.get(field, {} if field in ('env', 'headers') else None) for field in fields):
            fingerprints[name] = identity(root, name)
    return fingerprints


def records(root):
    path = ordinary_path(root / '.hub-store/verify.json')
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def current(root, name):
    record = records(root).get(name)
    if not record or record.get('hash') != identity(root, name):
        return None
    if executor_connector.configuration(root)['backend'] == 'mcphub':
        import gateway
        if not gateway.state(root / '.hub-store/gateway'):
            return {**record, 'ok': False, 'verified_at': None, 'status': 'unreachable', 'error': START}
    return record


def save(root, name, result):
    from calllog import directory
    path = directory(root)
    destination = ordinary_path(path / 'verify.json')
    values = records(root)
    values[name] = result
    descriptor, temporary = tempfile.mkstemp(dir=path, prefix='.verify-')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            restrict(stream, temporary)
            json.dump(values, stream, indent=2)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def check(root, name):
    try:
        fingerprint = identity(root, name)
    except LegacyCredential as error:
        return {'verified_at': None, 'checked_at': time.time(), 'tools': 0, 'ok': False, 'status': 'check_failed',
                'error': f'{error}. Run {legacy_next_step()}', 'next_step': legacy_next_step()}
    config = executor_connector.configuration(root)
    result = {'verified_at': None, 'checked_at': time.time(), 'tools': 0, 'ok': False,
              'error': '', 'status': 'check_failed', 'hash': fingerprint}
    gateway_headers = None
    try:
        if config['backend'] == 'mcphub':
            import gateway
            running = gateway.state(root / '.hub-store/gateway')
            if not running:
                result.update(status='unreachable', error=START)
                save(root, name, result)
                return {key: value for key, value in result.items() if key != 'hash'}
            if running.get('verification_hashes', {}).get(name) != fingerprint:
                result.update(error='Gateway configuration changed; prepare and restart the gateway')
                save(root, name, result)
                return {key: value for key, value in result.items() if key != 'hash'}
            config = {'backend': 'executor', 'url': 'http://' + gateway.LOOPBACK + ':' + str(running['port']) + '/mcp', 'headers': {}}
            gateway_headers = gateway.client_headers(root, running)
        with executor_connector.Session(root, config=config, headers=gateway_headers) as session:
            tools = session.tools()
        manifest = validators.layered(root, json.loads(repo_file(root, 'manifest.json').read_text(encoding='utf-8')))
        names = sorted((entry['name'] for entry in manifest['mcp_servers']), key=len, reverse=True)
        separator = '-'
        if executor_connector.configuration(root)['backend'] == 'mcphub':
            import gateway
            separator = gateway.settings(root / '.hub-store/gateway').get('systemConfig', {}).get('nameSeparator') or '-'
        count = sum(next((owner for owner in names if tool['name'].startswith(owner + separator)), None) == name for tool in tools)
        result.update(tools=count, ok=count > 0, verified_at=time.time() if count else None,
                      status='verified' if count else 'no_tools',
                      error='' if count else 'Checked, listed 0 tools')
    except (OSError, HTTPException):
        result.update(status='unreachable', error=START)
    except (ValueError, KeyError, TypeError):
        result.update(status='check_failed', error='Live tool check failed')
    if fingerprint != identity(root, name):
        result.update(ok=False, verified_at=None, status='check_failed', error='Server changed; check again')
    save(root, name, result)
    return {key: value for key, value in result.items() if key != 'hash'}


def enrich(root, server, old):
    name = server['name']
    store = FileStore(root)
    refs = {reference_name(v) for field in ('env', 'headers') for v in server.get(field, {}).values()
            if v.startswith('secret://')}
    authentication = Authentication(root, store)
    refs.update(authentication.argument_references(server.get('args', [])).values())
    binding = authentication.metadata(name)
    refs.update(binding.get('headers', {}).values())
    stored = bool(refs) and all(store.has(ref) for ref in refs)
    saved_at = max((store._file(ref).stat().st_mtime for ref in refs if store.has(ref)), default=None)
    state = 'saved' if stored else 'needs_signin' if refs or server['transport'] == 'http' else 'no_credentials'
    if old['status'] in ('expired', 'needs-auth') and binding:
        state = 'needs_signin'
    record = current(root, name)
    if record:
        state = 'verified' if record['ok'] else 'check_failed'
    label = {'saved': 'Saved', 'needs_signin': 'Needs sign-in', 'no_credentials': 'No credentials needed',
             'verified': 'Verified', 'check_failed': 'Check failed'}[state]
    return {**old, 'state': state, 'saved_at': saved_at, 'check': record['status'] if record else None,
            'checked_at': record.get('checked_at') if record else None,
            'verified_at': record['verified_at'] if record else None, 'tools': record['tools'] if record else None,
            'label': label, 'detail': ('Listed ' + str(record['tools']) + ' tools') if record and record['ok']
            else record['error'] if record else 'Not checked yet',
            'error': record['error'] if record else ''}
