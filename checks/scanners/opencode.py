from .common import paths


def project(inv, base):
    locations = paths(inv.home, inv.platform, inv.env)
    for directory in (base / '.opencode', *(locations['opencode'] if base == inv.home else ())):
        inv.packages('opencode', directory / 'skills')
        for folder in ('agents', 'commands', 'agent', 'command'):
            for path in inv.walk(directory / folder, depth=3):
                if path.suffix == '.md':
                    inv.rule('opencode', path)
        inv.rule('opencode', directory / 'AGENTS.md', inv.instruction_name(base, 'opencode', directory / 'AGENTS.md'))
        for name in ('opencode.json', 'opencode.jsonc'):
            inv.config('opencode', directory / name, opencode=True)


def searched(inv):
    return [inv.home / '.opencode', *paths(inv.home, inv.platform, inv.env)['opencode']]
