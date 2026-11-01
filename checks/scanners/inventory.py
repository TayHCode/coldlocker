import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys

import safety
from checks import validators
from client_paths import codex_home
from importers import json_object
from store import ordinary_path

from . import registry
from .common import (ERRORS, PRIVATE, digest, encoded, home_pattern, located, next_step, read, selected_runner, slug, texts,
                     walk)
from .plugins import PluginMixin, plugin_name
from .report import ReportMixin
from .routines import RoutineMixin
from .schedules import local_timezone
from .servers import ServerMixin
from .skills import SkillMixin


class Inventory(ReportMixin, SkillMixin, ServerMixin, PluginMixin, RoutineMixin):
    def __init__(self, home, projects, platform, env, runner, root, live=True):
        self.live = live
        self.home = Path(os.path.abspath(home))
        self.projects = tuple(Path(os.path.abspath(p)) for p in projects)
        self.platform = platform or ('win32' if sys.platform == 'win32' else 'darwin' if sys.platform == 'darwin' else 'linux')
        validators.require(self.platform in ('linux', 'darwin', 'win32'), 'invalid platform')
        validators.require(self.home.is_dir() and all(p.is_dir() for p in self.projects), 'missing scan directory')
        self.env = dict(os.environ if env is None else env)
        if env is None and os.path.realpath(self.home) != os.path.realpath(Path.home()):
            self.env.pop('XDG_CONFIG_HOME', None)
            self.env.pop('CODEX_HOME', None)
        selected_codex_home = codex_home(self.home, self.env, self.platform)
        try:
            ordinary_path(selected_codex_home)
            validators.require(not selected_codex_home.exists() or selected_codex_home.is_dir(),
                               'Codex home must be a directory')
            self.codex_home = selected_codex_home
        except (OSError, ValueError):
            self.codex_home = None
        self.runner = runner or selected_runner()
        self.zone = local_timezone(self.env, '/etc/localtime' if env is None else None)
        self.home_prefix = home_pattern(self.home, self.platform)
        self.root = Path(root)
        self.items = []
        self.payloads = {}
        self.review_payloads = {}
        self.path_bindings = {}
        self.seen = set()
        self.sightings = {}
        self.executor_endpoints = set()
        self.needs_auth = set()
        self.credential_values = {}
        self.incomplete_inventory = {}
        cache = self.home / '.claude/mcp-needs-auth-cache.json'
        if cache.is_file():
            try:
                entries = json_object(read(cache).decode())
                for key, value in entries.items():
                    self.needs_auth.add(key)
                    if isinstance(value, dict) and isinstance(value.get('id'), str):
                        self.needs_auth.add(value['id'])
            except ERRORS:
                pass

    def tilde(self, text):
        return self.home_prefix.sub('~', text)

    def walk(self, base, depth=8, limit=5000):
        return walk(base, depth, limit, self.walk_limited)

    def walk_limited(self, base, limit):
        source = self.source(base)
        self.incomplete_inventory[source, limit] = {'source': source, 'limit': limit}

    def source_path_binding(self, original, portable):
        if not isinstance(original, str) or not isinstance(portable, str):
            return None
        prefix = ''
        candidate = original
        if original.startswith('-') and '=' in original:
            prefix, _, candidate = original.partition('=')
        path = Path(candidate)
        if not path.is_absolute():
            return None
        try:
            resolved = Path(os.path.abspath(path))
            resolved.relative_to(self.home)
        except (OSError, ValueError):
            return None
        if prefix:
            resolved = Path(str(resolved))
            resolved_value = prefix + '=' + str(resolved)
        else:
            resolved_value = str(resolved)
        if prefix and not portable.startswith(prefix + '='):
            return None
        portable_path = portable[len(prefix) + 1:] if prefix else portable
        if portable == original or not (portable_path == '~' or portable_path.startswith(('~/', '~\\'))):
            return None
        if portable_path == '~' and resolved != self.home:
            return None
        return {'portable': portable, 'resolved': resolved_value}

    def source(self, path):
        if isinstance(path, str) and path.startswith('<system:'):
            return path
        path = Path(path)
        for index, project in enumerate(self.projects):
            if path.is_relative_to(project):
                return ('<project>' if index == 0 else f'<project-{index + 1}>') + '/' + path.relative_to(project).as_posix()
        if self.codex_home and self.codex_home != self.home / '.codex' and path.is_relative_to(self.codex_home):
            return '<codex-home>/' + path.relative_to(self.codex_home).as_posix()
        if path.is_relative_to(self.home):
            return '~/' + path.relative_to(self.home).as_posix()
        return '<external>/' + slug(path.name)

    def add(self, agent, kind, name, path, payload=None, checksum=None, ownership='user', flags=(), secrets=None, upstream=None, skipped=False, review=None, reason=None, review_payload=None, path_bindings=None, enabled_in_file=None, source_error=False):
        source = self.source(path)
        name = slug(name)
        validators.safe_identifier(name)
        validators.safe_fragment(source)
        checksum = checksum or digest(encoded(payload))
        identity = 'sc-' + hashlib.sha256(f'{agent}|{kind}|{source}|{checksum}'.encode()).hexdigest()[:12]
        if identity in self.seen:
            return
        self.seen.add(identity)
        flags = list(flags)
        for base in (self.home, *self.projects):
            if not isinstance(path, str) and Path(path).is_relative_to(base) and (base / '.agents/agents.json').is_file():
                flags.append('managed_by agents')
        status = 'skipped' if skipped or 'managed_by agents' in flags else 'vendor' if ownership != 'user' else 'connect' if upstream or kind == 'connection' else 'needs_review' if review and kind == 'routine' else 'new'
        item = dict(id=identity, kind=kind, agent=agent, name=name, source=source, content_hash=checksum,
                    status=status, duplicate_of=None, ownership=ownership,
                    importable=status in ('new', 'connect'), flags=sorted(set(flags)), secrets=len(secrets or {}), upstream=upstream, review=review or [])
        if source_error:
            item['source_error'] = True
        if kind == 'routine' and type(enabled_in_file) is bool:
            item['enabled_in_file'] = enabled_in_file
        if kind == 'routine' and isinstance(payload, tuple):
            item.update({field: payload[0][field] for field in ('runner', 'schedule', 'timezone')})
        if ownership == 'plugin_cache' and not isinstance(path, str) and (plugin := plugin_name(Path(path))):
            item['plugin'] = plugin
        if status == 'skipped' or 'needs-manual' in flags:
            item['reason'] = reason or ('managed by agents sync' if 'managed_by agents' in flags else 'unsupported or unsafe input')
            item['next_step'] = next_step(item['reason'])
        self.items.append(item)
        self.payloads[identity] = (payload, secrets or {})
        if review_payload is not None:
            self.review_payloads[identity] = review_payload
        if path_bindings:
            self.path_bindings[identity] = path_bindings

    def cause(self, path, files=None, definition=None):
        error = sys.exc_info()[1]
        if isinstance(error, FileNotFoundError):
            return 'file missing'
        if isinstance(error, OSError):
            return 'file unreadable'
        if isinstance(error, KeyError):
            return 'required field missing'
        if not isinstance(error, ValueError):
            return f'unsupported structure ({type(error).__name__})'
        message = str(error)
        if message == PRIVATE:
            if definition is not None:
                categories = sorted({category for _, category in validators.findings(json.dumps(definition))})
                found = ', '.join(categories) + ' in the server definition' if categories else ''
            else:
                if files is None:
                    files = []
                    with contextlib.suppress(*ERRORS):
                        files = texts(Path(path))
                found = located(files)
            if found:
                return 'definition contains ' + found
        if not message or validators.findings(message) or any(value in message for value in self.credential_values):
            return 'invalid content'
        return message

    def failed(self, agent, kind, path, name=None, ownership='user', files=None, definition=None):
        reason = self.cause(path, files, definition)
        review = []
        if kind in ('skill', 'rule') and ownership == 'user':
            with contextlib.suppress(*ERRORS):
                review = safety.secret_findings(texts(Path(path)))
        try:
            self.add(agent, kind, name or Path(path).stem, path, {'name': slug(name or Path(path).stem)}, flags=['needs-manual'], skipped=ownership == 'user', ownership=ownership, review=review, reason=reason, source_error=kind == 'mcp_server' and name is None)
        except ERRORS:
            pass

    def discover(self):
        for base in (self.home, *self.projects):
            for step in registry.PROJECT_STEPS:
                step(self, base)
        for step in registry.GLOBAL_STEPS:
            step(self)
        self.plugin_records()
        return self.finish()

    def searched(self, agent):
        found = registry.SEARCHED[agent](self)
        return [path if isinstance(path, str) else self.tilde(str(path)) for path in dict.fromkeys(found)]
