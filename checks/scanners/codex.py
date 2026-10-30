from .common import ERRORS, digest, read, slug


def home_for(inv, base):
    return inv.codex_home if base == inv.home else base / '.codex'


def skills(inv, base):
    codex = home_for(inv, base)
    if codex:
        inv.packages('codex', codex / 'skills')


def rules(inv, base):
    codex = home_for(inv, base)
    for relative in ('AGENTS.md', 'AGENTS.override.md'):
        if codex:
            path = codex / relative
            inv.rule('codex', path, inv.instruction_name(base, 'codex', path))
        if base != inv.home:
            path = base / relative
            inv.rule('codex', path, inv.instruction_name(base, 'codex', path))


def exec_policies(inv, base):
    codex = home_for(inv, base)
    if codex:
        for path in inv.walk(codex / 'rules', depth=0):
            if path.suffix == '.rules':
                execpolicy(inv, path)


def config(inv, base):
    codex = home_for(inv, base)
    if codex:
        inv.config('codex', codex / 'config.toml')


def plugin_cache(inv, base):
    codex = home_for(inv, base)
    if codex:
        inv.plugin_root('codex', codex / 'plugins/cache')


def plugin_registry(inv, base):
    codex = home_for(inv, base)
    if codex:
        for path in (codex / 'plugins/installed_plugins.json', codex / 'extensions.json'):
            inv.plugins('codex', path)


def automations(inv):
    if inv.codex_home:
        inv.tasks('codex', inv.codex_home / 'automations')


def execpolicy(inv, path):
    try:
        inv.add('codex', 'rule', path.stem, path, {'name': slug(path.stem)}, digest(read(path)), flags=['needs-manual'], skipped=True, reason='Codex command-approval rules are not imported')
    except ERRORS:
        inv.failed('codex', 'rule', path)


def searched(inv):
    return ['CODEX_HOME' if inv.env.get('CODEX_HOME') else inv.home / '.codex']
