from .common import mcp_json_disabled


def skills(inv, base):
    inv.packages('cursor', base / '.cursor/skills')


def builtin_skills(inv, base):
    inv.packages('cursor', base / '.cursor/skills-cursor', builtin=True)


def rules(inv, base):
    for path in inv.walk(base / '.cursor/rules', depth=4):
        if path.suffix in ('.md', '.mdc'):
            inv.rule('cursor', path)


def config(inv, base):
    path = base / '.cursor/mcp.json'
    inv.config('cursor', path, disabled=mcp_json_disabled(base) if path.name in ('.mcp.json', 'mcp.json') else ())


def extensions(inv, base):
    inv.plugins('cursor', base / '.cursor/extensions/extensions.json')


def searched(inv):
    return [inv.home / '.cursor']
