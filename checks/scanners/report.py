import contextlib
import hashlib
from pathlib import Path
import re
from urllib.parse import urlsplit

import safety
from checks import validators
from importers import json_object, selected_path
from portable import python_command

from .common import (ERRORS, KINDS, MANUAL_IMPORT, NOT_SCANNED, REPLACES, REUSED, STATUSES, conflict, credential_finding, digest,
                     encoded, linked, next_step, read, texts, tree_hash)


class ReportMixin:
    def known_hosts(self, manifest):
        hosts = set()
        values = [item['upstream']['endpoint'] for item in self.items if item.get('upstream')]
        for entry in manifest.get('mcp_servers', []):
            with contextlib.suppress(*ERRORS):
                value = json_object(read(selected_path(self.root, entry['path'])).decode())
                values += [v for v in (value.get('url'),) if isinstance(v, str)]
        for value in values:
            with contextlib.suppress(*ERRORS):
                hosts.add((urlsplit(value).hostname or '').lower())
        return hosts

    def assess(self, manifest):
        known = self.known_hosts(manifest)
        for item in self.items:
            if item['ownership'] != 'user' or item['status'] in ('skipped', 'vendor') or item['kind'] == 'connection':
                continue
            payload = self.review_payloads.get(item['id'], self.payloads[item['id']][0])
            found = safety.review(item['kind'], payload, texts(payload, item['name']), known, bool(item['upstream']))
            if found:
                item['review'] = item['review'] + found
                if item['status'] in ('new', 'connect'):
                    item.update(status='needs_review', importable=False)
        by_id = {item['id']: item for item in self.items}
        for connection, server in linked(self.items).items():
            flagged = by_id[server]
            if flagged['status'] == 'needs_review' and by_id[connection]['status'] == 'connect':
                by_id[connection].update(status='needs_review', importable=False, review=flagged['review'])

    def not_scanned(self):
        agents = [{'name': name, 'path': '~/' + folder} for name, folder in NOT_SCANNED if (self.home / folder).is_dir()]
        return {'agents': agents, 'next_step': MANUAL_IMPORT.replace('{python}', python_command())}

    def leaked(self, text):
        for value, origin in self.credential_values.items():
            if re.search(r'(?<!\w)' + re.escape(value) + r'(?!\w)', text):
                return origin

    def finish(self):
        for item in self.items:
            public = encoded({k: v for k, v in item.items() if k not in ('content_hash', 'id')}).decode()
            origin = self.leaked(public)
            if origin:
                old_id = item['id']
                for field, placeholder in (('name', 'redacted-item'), ('source', '<system:redacted>')):
                    if self.leaked(item[field]):
                        item[field] = placeholder
                item.update(status='skipped', importable=False,
                            upstream=None, flags=['needs-manual'], review=[credential_finding(origin)], reason=REUSED, next_step=next_step(REUSED))
                item['id'] = 'sc-' + hashlib.sha256(f"{item['agent']}|{item['kind']}|{item['source']}|{item['content_hash']}".encode()).hexdigest()[:12]
                self.payloads[item['id']] = self.payloads.pop(old_id)
                if old_id in self.review_payloads:
                    self.review_payloads[item['id']] = self.review_payloads.pop(old_id)
                if old_id in self.path_bindings:
                    self.path_bindings[item['id']] = self.path_bindings.pop(old_id)
            payload, _ = self.payloads[item['id']]
            if item['importable'] and item['kind'] in ('skill', 'rule', 'routine', 'plugin', 'connection'):
                if isinstance(payload, Path):
                    content = b''.join(read(p) for p in self.walk(payload, limit=1001)).decode('utf-8', errors='replace')
                elif isinstance(payload, bytes):
                    content = payload.decode('utf-8', errors='replace')
                else:
                    content = encoded(payload).decode()
                origin = self.leaked(content)
                if origin:
                    item.update(status='skipped', importable=False, flags=sorted(set(item['flags'] + ['needs-manual'])),
                                review=[credential_finding(origin)], reason=REUSED, next_step=next_step(REUSED))
        manifest = validators.layered(self.root, json_object(read(self.root / 'manifest.json').decode()))
        existing = {}
        origins = {}
        leaky = set()
        for kind, collection in KINDS.items():
            for entry in manifest.get(collection, []):
                origins[kind, entry.get('name', entry.get('id'))] = entry.get('provenance', {}).get('origin')
                try:
                    path = selected_path(self.root, entry['path'])
                    if kind == 'skill':
                        checksum = tree_hash(path)
                    elif kind == 'rule':
                        checksum = digest(read(path))
                    elif kind == 'routine':
                        value = json_object(read(path).decode())
                        prompt = read(path.parent / value['prompt']).decode() if (path.parent / value['prompt']).exists() else ''
                        checksum = digest(encoded([value, prompt]))
                    else:
                        value = json_object(read(path).decode())
                        checksum = digest(encoded(value))
                        if kind == 'mcp_server' and isinstance(value.get('args'), list) and validators.credential_args(value['args']):
                            leaky.add((kind, entry['name']))
                    existing[kind, entry.get('name', entry.get('id'))] = checksum
                except ERRORS:
                    existing[kind, entry.get('name', entry.get('id'))] = None
        hashes = {}
        existing_hashes = {(k[0], v) for k, v in existing.items()}
        names = {}
        for item in self.items:
            if not item['importable']:
                continue
            key = item['kind'], item['name']
            content = item['kind'], item['content_hash']
            if content in existing_hashes:
                item.update(status='already_in_locker', importable=False)
            elif key in existing and key not in leaky:
                label = item['kind'].replace('_', ' ')
                if origins[key] == 'scan-' + item['agent']:
                    conflict(item, f"the {label} {item['name']} in the locker was imported from this source, which has changed since it was imported", changed=True)
                else:
                    conflict(item, f"a different {label} named {item['name']} is already in the locker")
            elif content in hashes:
                item.update(status='duplicate', duplicate_of=hashes[content], importable=False)
            else:
                hashes[content] = item['id']
                if key in leaky:
                    item['reason'] = REPLACES
            names.setdefault(key, []).append(item)
        for group in names.values():
            if len({i['content_hash'] for i in group}) > 1:
                for item in group:
                    if item['status'] != 'already_in_locker':
                        item.update(status='conflict', importable=False, duplicate_of=None)
                        if item.get('reason') == REPLACES:
                            del item['reason']
                        if 'reason' not in item:
                            other = next(i for i in group if i['content_hash'] != item['content_hash'])
                            conflict(item, f"same name as {other['id']} ({other['agent']}) with different content")
        self.assess(manifest)
        agents = {}
        for item in self.items:
            agents.setdefault(item['agent'], {c: [] for c in KINDS.values()})[KINDS[item['kind']]].append(item['id'])
        counts = {a: {c: len(ids) for c, ids in values.items()} for a, values in agents.items()}
        totals = {c: sum(v[c] for v in counts.values()) for c in KINDS.values()}
        totals['statuses'] = {s: sum(i['status'] == s for i in self.items) for s in STATUSES}
        incomplete = sorted(self.incomplete_inventory.values(), key=lambda row: (row['source'], row['limit']))
        return {'version': 1, 'platform': self.platform, 'agents': agents, 'items': self.items,
                'counts': counts, 'totals': totals, 'not_scanned': self.not_scanned(),
                'incomplete_inventory': incomplete}
