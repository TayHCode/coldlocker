from checks import validators
from importers import json_object, selected_path

from .common import ERRORS, read


def skills(inv, base):
    inv.packages('gemini', base / '.gemini/skills')


def rules(inv, base):
    if base != inv.home:
        path = base / 'GEMINI.md'
        inv.rule('gemini', path, inv.instruction_name(base, 'gemini', path))


def config(inv, base):
    mcp_config(inv, base / '.gemini/settings.json')


def commands(inv, base):
    for path in inv.walk(base / '.gemini/commands', depth=8):
        if path.suffix == '.toml':
            inv.rule('gemini', path, inv.instruction_name(base, 'gemini', path))


def extensions(inv):
    root = inv.home / '.gemini/extensions'
    for extension in sorted(root.iterdir()) if root.is_dir() else ():
        if extension.is_symlink() or not extension.is_dir():
            continue
        manifest = extension / 'gemini-extension.json'
        mcp_config(inv, manifest)
        inv.packages('gemini', extension / 'skills')
        try:
            value = json_object(read(manifest).decode())
            context = value.get('contextFileName', 'GEMINI.md')
            validators.require(isinstance(context, str) and context not in ('', '.', '..') and
                               not any(char in context for char in '/\\:'), 'context filename required')
            path = extension / context
            if path.exists() or path.is_symlink():
                path = selected_path(extension, context)
                inv.rule('gemini', path, inv.instruction_name(inv.home, 'gemini', path))
        except ERRORS:
            inv.failed('gemini', 'rule', manifest)
        for command in inv.walk(extension / 'commands', depth=8):
            if command.suffix == '.toml':
                inv.rule('gemini', command, inv.instruction_name(inv.home, 'gemini', command))


def global_sources(inv):
    base = inv.home
    inv.packages('gemini', base / '.gemini/skills')
    path = base / '.gemini/GEMINI.md'
    inv.rule('gemini', path, 'gemini-global')
    mcp_config(inv, base / '.gemini/settings.json')
    commands(inv, base)
    extensions(inv)


def searched(inv):
    return [inv.home / '.gemini']


def mcp_config(inv, path):
    if not path.exists() and not path.is_symlink():
        return
    try:
        value = json_object(read(path).decode())
        servers = value.get('mcpServers', {})
        validators.require(isinstance(servers, dict), 'MCP server mapping required')
        for name, source in servers.items():
            if isinstance(source, dict) and 'httpUrl' in source:
                source = dict(source, url=source['httpUrl'])
                source.pop('httpUrl')
            inv.server('gemini', path, name, source)
    except ERRORS:
        inv.failed('gemini', 'mcp_server', path)
