import hashlib
import json
import math
import os
import re
import time

from portable import lock as acquire, restrict
from store import FileStore, ordinary_path


MAX_BYTES = 1024 * 1024
LIMIT = 20
SETUP_CHECK = 'setup check'
META_KEY = 'agentlocker'
SESSION_LIMIT = 256
SESSION_PATTERN = re.compile(r'sessionId: (\S+) and group: ')
SESSION_CREATED = re.compile(r'\[SESSION NEW\] Creating new session with ID: (\S+)')


def directory(root):
    path = ordinary_path(root / '.hub-store')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def append(root, client, method, tool, duration_ms, source='stdio'):
    store = FileStore(root)
    def identifier(value, fallback):
        return store.redact(value[:128]) if isinstance(value, str) and re.fullmatch(r'[\w .:/-]{1,128}', value) else fallback
    record = {'ts': time.time(), 'client': identifier(client, 'unknown'),
              'method': identifier(method, 'unknown'), 'duration_ms': max(0, round(duration_ms, 3)) if duration_ms is not None else None,
              'source': source}
    if tool is not None:
        record['tool'] = identifier(tool, 'unknown')
    path = directory(root)
    lock_path = ordinary_path(path / 'calls.lock')
    with open(lock_path, 'a', encoding='utf-8') as lock:
        restrict(lock, lock_path)
        acquire(lock)
        log = ordinary_path(path / 'calls.jsonl')
        previous = ordinary_path(path / 'calls.jsonl.1')
        first_path = ordinary_path(path / 'calls-first.json')
        first = json.loads(first_path.read_text(encoding='utf-8')) if first_path.exists() else {}
        if record['client'] not in first and record['client'] != SETUP_CHECK:
            first[record['client']] = record
            with open(first_path, 'w', opener=lambda p, f: os.open(p, f, 0o600), encoding='utf-8') as stream:
                restrict(stream, first_path)
                json.dump(first, stream)
        if log.exists() and log.stat().st_size >= MAX_BYTES:
            os.replace(log, previous)
        with open(log, 'a', opener=lambda p, f: os.open(p, f, 0o600), encoding='utf-8') as stream:
            restrict(stream, log)
            stream.write(json.dumps(record) + '\n')


def read(root, since=0):
    since = float(since)
    if not math.isfinite(since) or since < 0:
        raise ValueError('invalid call cursor')
    path = ordinary_path(root / '.hub-store')
    lock_path = ordinary_path(path / 'calls.lock')
    if lock_path.exists():
        with open(lock_path, encoding='utf-8') as lock:
            acquire(lock, exclusive=False)
            return _read(path, since)
    return _read(path, since)


def _read(path, since):
    calls = []
    for name in ('calls.jsonl.1', 'calls.jsonl'):
        log = ordinary_path(path / name)
        if log.exists():
            for line in log.read_text(encoding='utf-8').splitlines():
                try:
                    record = json.loads(line)
                    if record['ts'] > since:
                        calls.append(record)
                except (ValueError, KeyError, TypeError):
                    pass
    first_path = ordinary_path(path / 'calls-first.json')
    first = json.loads(first_path.read_text(encoding='utf-8')) if first_path.exists() else {}
    sources = sorted({'stdio', *(record.get('source', 'stdio') for record in calls),
                      *(record.get('source', 'stdio') for record in first.values())})
    real = [record for record in calls if record['client'] != SETUP_CHECK]
    older = max(0, len(real) - LIMIT)
    return {'calls': real[older:], 'older': older, 'setup_checks': len(calls) - len(real), 'sources': sources,
            'first_call': first, 'waiting': not bool(first)}


def session_state(root, change):
    path = directory(root)
    lock_path = ordinary_path(path / 'calls.lock')
    state_path = ordinary_path(path / 'calls-sessions.json')
    with open(lock_path, 'a', encoding='utf-8') as lock:
        restrict(lock, lock_path)
        acquire(lock)
        try:
            state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
        except ValueError:
            state = {}
        state = state if isinstance(state, dict) else {}
        if not isinstance(state.get('sessions'), dict):
            state['sessions'] = {}
        if not isinstance(state.get('pending'), list):
            state['pending'] = []
        state['pending'] = [client for client in state['pending'] if isinstance(client, str)][-2:]
        before = state_path.read_text(encoding='utf-8') if state_path.exists() else ''
        result = change(state)
        state['sessions'] = dict(list(state['sessions'].items())[-SESSION_LIMIT:])
        text = json.dumps(state)
        if text != before:
            with open(state_path, 'w', opener=lambda p, f: os.open(p, f, 0o600), encoding='utf-8') as stream:
                restrict(stream, state_path)
                stream.write(text)
        return result


def session_key(session):
    return hashlib.sha256(session.encode('utf-8')).hexdigest()[:32]


def remember_initialize(session, client):
    def change(state):
        if session:
            state['sessions'].pop(session_key(session), None)
            state['sessions'][session_key(session)] = client
        else:
            state['pending'] = (state['pending'] + [client])[-2:]
    return change


def bind_created(session):
    def change(state):
        if len(state['pending']) == 1:
            state['sessions'][session_key(session)] = state['pending'][0]
        state['pending'] = []
    return change


def lookup(session):
    return lambda state: state['sessions'].get(session_key(session), 'unknown')


def gateway_request(root, line):
    created = SESSION_CREATED.search(line)
    if created:
        session_state(root, bind_created(created.group(1)))
        return
    marker = 'Handling MCP post request for sessionId: '
    if marker not in line or ' with body: ' not in line:
        return
    body = json.loads(line.split(' with body: ', 1)[1])
    if not isinstance(body, dict):
        return
    params = body.get('params', {})
    params = params if isinstance(params, dict) else {}
    info = params.get('clientInfo', {})
    initializing = body.get('method') == 'initialize'
    client = info.get('name', 'unknown') if initializing and isinstance(info, dict) else 'unknown'
    meta = params.get('_meta')
    setup_check = isinstance(meta, dict) and meta.get(META_KEY) == SETUP_CHECK
    if setup_check:
        client = SETUP_CHECK
    found = SESSION_PATTERN.search(line.split(' with body: ', 1)[0])
    session = found.group(1) if found and found.group(1) not in ('undefined', 'null') else None
    if initializing:
        session_state(root, remember_initialize(session, client))
    elif session and not setup_check:
        client = session_state(root, lookup(session))
    tool = params.get('name') if body.get('method') == 'tools/call' else None
    append(root, client, body.get('method'), tool, None, source='gateway')
