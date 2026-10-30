import json
import re
from pathlib import Path

from checks import validators

from .common import ERRORS, read
from .routines import owner_label


def home(inv):
    return Path(inv.env.get('HERMES_HOME', inv.home / '.hermes'))


def skills(inv, base):
    inv.packages('hermes', home(inv) / 'skills' if base == inv.home else base / '.hermes/skills')


def rules(inv, base):
    if base == inv.home:
        path = home(inv) / 'SOUL.md'
        inv.rule('hermes', path, 'hermes-soul')
    else:
        for name in ('.hermes.md', 'HERMES.md'):
            path = base / name
            inv.rule('hermes', path, inv.instruction_name(base, 'hermes', path))


def scalar(value):
    value = value.strip()
    if value.startswith(('!', '&', '*')):
        raise ValueError('unsupported YAML feature')
    if value.startswith('"'):
        return json.loads(value)
    if value.startswith("'"):
        if not value.endswith("'") or len(value) < 2:
            raise ValueError('malformed YAML string')
        return value[1:-1].replace("''", "'")
    if value.startswith('['):
        validators.require(value.endswith(']'), 'malformed YAML sequence')
        body = value[1:-1].strip()
        if not body:
            return []
        parts = []
        position = 0
        pattern = re.compile(r'''\s*("(?:\\.|[^"\\])*"|'(?:''|[^'])*'|[^,\[\]{}'"]+)\s*(,|$)''')
        while position < len(body):
            match = pattern.match(body, position)
            validators.require(match is not None and bool(match[1].strip()), 'malformed YAML sequence')
            parts.append(scalar(match[1]))
            position = match.end()
        return parts
    if value.startswith('{'):
        return json.loads(value)
    if value in ('', 'null', 'Null', 'NULL', '~'):
        return None
    if value.lower() in ('true', 'false'):
        return value.lower() == 'true'
    return value


def yaml_mapping(text):
    lines = []
    for line in text.splitlines():
        if '\t' in line[:len(line) - len(line.lstrip())]:
            raise ValueError('tabs are not allowed in YAML indentation')
        body = re.sub(r'''"(?:\\.|[^"\\])*"|'(?:''|[^'])*'|\s+#.*$''',
                      lambda match: '' if match[0].lstrip().startswith('#') else match[0], line).strip()
        if body and not body.startswith('#'):
            indent = len(line) - len(line.lstrip(' '))
            lines.append((indent, body))
    index = 0

    def parse_block(indent):
        nonlocal index
        is_list = lines[index][1].startswith('- ')
        result = [] if is_list else {}
        while index < len(lines) and lines[index][0] == indent:
            body = lines[index][1]
            if is_list:
                if not body.startswith('- '):
                    raise ValueError('mixed YAML collection')
                value = body[2:].strip()
                index += 1
                result.append(parse_value(value, indent))
            else:
                if body.startswith('- '):
                    raise ValueError('mixed YAML collection')
                match = re.match(r'^([^:#][^:]*):(?:\s+(.*))?$', body)
                if not match:
                    raise ValueError('malformed YAML mapping')
                key = match.group(1).strip()
                validators.require(key not in result, 'duplicate YAML key')
                value = match.group(2) or ''
                index += 1
                result[key] = parse_value(value, indent)
        return result

    def parse_value(value, parent_indent):
        nonlocal index
        if value:
            return scalar(value)
        if index < len(lines) and lines[index][0] > parent_indent:
            return parse_block(lines[index][0])
        return None

    if not lines:
        return {}
    validators.require(lines[0][0] == 0 and not lines[0][1].startswith('- '), 'YAML mapping required')
    result = parse_block(0)
    validators.require(index == len(lines), 'malformed YAML indentation')
    validators.require(isinstance(result, dict), 'YAML mapping required')
    return result


def mcp_config(inv):
    path = home(inv) / 'config.yaml'
    if not path.is_file():
        return
    try:
        text = read(path).decode()
        lines = text.splitlines()
        blocks = []
        for index, line in enumerate(lines):
            if re.match(r'^mcp_servers:\s*(?:#.*)?$', line):
                end = index + 1
                while end < len(lines) and (not lines[end].strip() or lines[end][0].isspace() or lines[end].startswith('#')):
                    end += 1
                blocks.append('\n'.join(lines[index:end]))
            elif line.startswith('mcp_servers:'):
                blocks.append(line)
        validators.require(len(blocks) <= 1, 'duplicate MCP mapping')
        value = yaml_mapping(blocks[0]) if blocks else {}
        servers = value.get('mcp_servers', {})
        validators.require(isinstance(servers, dict), 'MCP server mapping required')
        for name, source in servers.items():
            inv.server('hermes', path, name, source)
    except ERRORS:
        inv.failed('hermes', 'mcp_server', path)


def scheduled_tasks(inv):
    path = home(inv) / 'cron/jobs.json'
    if not path.is_file():
        return
    try:
        from importers import json_object
        value = json_object(read(path).decode())
        entries = value if isinstance(value, list) else value.get('jobs', []) if isinstance(value, dict) else None
        validators.require(isinstance(entries, list), 'cron job list required')
        for job in entries:
            try:
                validators.require(isinstance(job, dict), 'cron job object required')
                schedule = job.get('schedule')
                if isinstance(schedule, str):
                    schedule = {'type': 'cron', 'expression': schedule}
                if isinstance(schedule, dict) and 'kind' in schedule:
                    if schedule['kind'] == 'cron':
                        schedule = {'type': 'cron', 'expression': schedule['expr']}
                    elif schedule['kind'] == 'interval':
                        minutes = schedule['minutes']
                        validators.require(isinstance(minutes, (int, float)) and not isinstance(minutes, bool), 'interval minutes required')
                        schedule = {'type': 'interval', 'every_seconds': minutes * 60}
                    else:
                        raise ValueError('unsupported Hermes schedule')
                inv.routine('hermes', path, job.get('name', job.get('id', 'hermes-job')),
                            inv.schedule(schedule), job.get('prompt', ''),
                            timezone=job.get('timezone', 'UTC'), owner=owner_label(job.get('owner_bot')))
            except ERRORS:
                inv.failed('hermes', 'routine', path, job.get('name', job.get('id', 'hermes-job')) if isinstance(job, dict) else None)
    except ERRORS:
        inv.failed('hermes', 'routine', path)


def global_sources(inv):
    root = home(inv)
    inv.packages('hermes', root / 'skills')
    path = root / 'SOUL.md'
    inv.rule('hermes', path, 'hermes-soul')
    mcp_config(inv)
    scheduled_tasks(inv)


def searched(inv):
    return [home(inv)]
