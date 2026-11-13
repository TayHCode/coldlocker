from checks import validators
from importers import json_object

from .common import ERRORS, read, slug


def plugin_name(path):
    for parent in (path, *path.parents):
        if parent.name in ('cache', 'plugins'):
            return None
        for folder in ('.claude-plugin', '.codex-plugin'):
            manifest = parent / folder / 'plugin.json'
            if manifest.is_file():
                try:
                    return slug(json_object(read(manifest).decode())['name'])
                except ERRORS:
                    return None
    return None


class PluginMixin:
    def plugin(self, agent, path, name, value, marketplace=None, origin='config'):
        try:
            if isinstance(value, str):
                value = {'source': value}
            validators.require(isinstance(value.get('source', name), str), 'source reference required')
            identity = f'{name}@{marketplace}' if marketplace else name
            fields = {'source': value.get('source', identity), 'version': str(value.get('version', 'unknown')),
                      'enabled': value.get('enabled', True) is True if origin == 'config' else None}
            validators.metadata(self.plugin_data(agent, identity, fields), 'plugin')
            self.sightings.setdefault((agent, identity), []).append((0 if origin != 'cache' else 1, path, fields))
        except ERRORS:
            self.failed(agent, 'plugin', path)

    def plugin_data(self, agent, identity, fields):
        return {'name': slug(identity), 'agent': agent, 'source': fields['source'], 'version': fields['version'],
                'enabled': fields['enabled'] is True, 'install_hint': 'Install with the source agent plugin manager'}

    def plugin_records(self):
        for (agent, identity), sightings in self.sightings.items():
            merged = {}
            differing = []
            for field, missing in (('source', identity), ('version', 'unknown'), ('enabled', None)):
                for rank in (0, 1):
                    values = list(dict.fromkeys(f[field] for r, _, f in sightings if r == rank and f[field] not in (None, 'unknown')))
                    if values:
                        break
                if len(values) > 1:
                    differing.append(f"{field} differs: {' vs '.join(map(str, values))}")
                merged[field] = values[0] if values else missing
            try:
                if not differing:
                    self.add(agent, 'plugin', identity, sightings[0][1], self.plugin_data(agent, identity, merged))
                    continue
                for _, path, fields in sightings:
                    count = len(self.items)
                    self.add(agent, 'plugin', identity, path, self.plugin_data(agent, identity, fields))
                    if len(self.items) > count:
                        self.items[-1]['reason'] = '; '.join(differing)
            except ERRORS:
                self.failed(agent, 'plugin', sightings[0][1])

    def plugins(self, agent, path):
        if not path.is_file():
            return
        try:
            value = json_object(read(path).decode())
            if isinstance(value, list):
                for v in value:
                    if isinstance(v, dict):
                        self.plugin(agent, path, v.get('identifier', {}).get('id', v.get('name', 'extension')), v)
                return
            for name, entries in value.get('plugins', value.get('enabledPlugins', value.get('extensions', {}))).items():
                entry = entries[0] if isinstance(entries, list) and entries else entries
                self.plugin(agent, path, name, entry if isinstance(entry, dict) else {'enabled': bool(entry)},
                            origin='registry' if path.name == 'installed_plugins.json' else 'config')
            for name, entry in value.items() if path.name == 'known_marketplaces.json' else []:
                source = entry.get('source', {}) if isinstance(entry, dict) else {}
                reference = source.get('repo', source.get('url')) if isinstance(source, dict) else source
                if reference:
                    self.plugin(agent, path, name, {'source': reference})
        except ERRORS:
            self.failed(agent, 'plugin', path)

    def cached_plugins(self, agent, base):
        for path in self.walk(base, depth=9):
            if path.name != 'plugin.json' or (agent != 'grok' and path.parent.name not in ('.codex-plugin', '.claude-plugin')):
                continue
            try:
                value = json_object(read(path).decode())
                parts = path.relative_to(base).parts
                parts = parts[1:] if parts[0] == 'cache' else parts
                marketplace = parts[0] if agent != 'grok' and len(parts) == 5 else None
                self.plugin(agent, path, value['name'], {'version': value.get('version', 'unknown')}, marketplace, 'cache')
            except ERRORS:
                self.failed(agent, 'plugin', path)

    def plugin_root(self, agent, plugin_root):
        self.packages(agent, plugin_root, vendor=True)
        self.cached_plugins(agent, plugin_root)
        for path in self.walk(plugin_root, depth=12):
            if path.name in ('.mcp.json', 'mcp.json'):
                self.config(agent, path, ownership='plugin_cache')
