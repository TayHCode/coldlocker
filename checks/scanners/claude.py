from checks import validators

from .common import ERRORS, mcp_json_disabled, paths, read, slug


def skills(inv, base):
    inv.packages('claude', base / '.claude/skills')


def rules(inv, base):
    claude_rule = base / '.claude/CLAUDE.md'
    inv.rule('claude', claude_rule, inv.instruction_name(base, 'claude', claude_rule))
    if base != inv.home:
        project_claude_rule = base / 'CLAUDE.md'
        inv.rule('claude', project_claude_rule,
                 inv.instruction_name(base, 'claude', project_claude_rule))


def configs(inv, base):
    switched_off = mcp_json_disabled(base)
    for path in (base / '.mcp.json', base / '.claude.json', base / '.claude/.mcp.json'):
        inv.config('claude', path, disabled=switched_off if path.name in ('.mcp.json', 'mcp.json') else ())


def desktop_config(inv, base):
    if base == inv.home:
        for path in paths(inv.home, inv.platform, inv.env)['claude_desktop']:
            inv.config('claude', path)


def plugin_registry(inv, base):
    for path in (base / '.claude/plugins/installed_plugins.json', base / '.claude/plugins/known_marketplaces.json', base / '.claude/settings.json'):
        inv.plugins('claude', path)


def plugin_cache(inv, base):
    inv.plugin_root('claude', base / '.claude/plugins')


def scheduled_tasks(inv):
    inv.tasks('claude', inv.home / '.claude/scheduled-tasks', on_skill=desktop_task)


def connections(inv):
    inv.connections('claude', inv.home / '.claude.json')
    inv.connections('claude', inv.home / '.claude/connections.json')


def desktop_task(inv, agent, path):
    if any(sibling.suffix in ('.json', '.toml') for sibling in path.parent.iterdir()):
        return
    try:
        text = read(path).decode().replace('\r\n', '\n')
        head, _, prompt = text.removeprefix('---\n').partition('\n---')
        fields = dict(line.split(':', 1) for line in head.splitlines() if ':' in line) if text.startswith('---\n') else {}
        name = fields.get('name', '').strip().strip('\'"') or path.parent.name
        prompt = inv.tilde(prompt if fields else text)
        validators.safe_fragment(prompt)
        inv.add(agent, 'routine', name, path, {'name': slug(name), 'prompt': prompt}, flags=['needs-manual'], skipped=True,
                 reason='schedule is kept by the Claude Desktop app, not on disk')
    except ERRORS:
        inv.failed(agent, 'routine', path, path.parent.name)


def searched(inv):
    return [inv.home / '.claude', inv.home / '.claude.json', inv.home / '.mcp.json']
