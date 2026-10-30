from pathlib import Path

from checks import validators
from importers import LIMIT, json_object

from .common import EXCLUDE, ERRORS, digest, read


def skills(inv, base):
    inv.packages('grok', base / '.grok/skills')


def global_sources(inv):
    inv.tasks('grok', inv.home / '.grok/routines', runner='grok-bot')
    for base in dict.fromkeys((inv.home / '.grok/exports', inv.home / '.config/grok/exports', Path(inv.env.get('GROK_EXPORT_DIR', inv.home / '.grok/exports')))):
        inv.packages('grok', base / 'skills')
        inv.packages('grok', base / 'workflows')
        inv.tasks('grok', base / 'routines', runner='grok-bot')
        inv.tasks('grok', base / 'routines.json', runner='grok-bot')
        exported_skills(inv, base / 'skills.json')
        skills_archive(inv, base / 'skills.tgz')
        inv.config('grok', base / 'mcp.json')
        inv.plugins('grok', base / 'plugins.json')
        inv.connections('grok', base / 'connections.json')
    inv.cached_plugins('grok', inv.home / '.grok/installed-plugins')
    inv.plugins('grok', inv.home / '.grok/plugins.json')


def exported_skills(inv, path):
    if not path.is_file():
        return
    try:
        value = json_object(read(path).decode())
        entries = value if isinstance(value, list) else value.get('skills', [])
        for entry in entries:
            try:
                name = validators.name(entry['name'])
                files = entry['files']
                validators.require(isinstance(files, dict) and 0 < len(files) <= 1000 and 'SKILL.md' in files, 'invalid exported package')
                parts = []
                total = 0
                for relative, text in files.items():
                    validators.relative_path(relative)
                    validators.require(not EXCLUDE.intersection(Path(relative).parts), 'excluded file')
                    validators.require(isinstance(text, str), 'text export required')
                    validators.safe_fragment(text)
                    data = text.encode()
                    total += len(data)
                    parts.append((relative, digest(data)))
                validators.require(total <= LIMIT, 'export too large')
                checksum = digest(''.join(f'{r}\0{sha}\n' for r, sha in sorted(parts)).encode())
                inv.add('grok', 'skill', name, path, files, checksum)
            except ERRORS:
                inv.failed('grok', 'skill', path)
    except ERRORS:
        inv.failed('grok', 'skill', path)


def skills_archive(inv, path):
    base = path.parent
    if path.is_file() and not (base / 'skills').is_dir() and not (base / 'workflows').is_dir():
        inv.add('grok', 'skill', 'skills-archive', path, {'name': 'skills-archive'}, flags=['needs-manual'], skipped=True,
                 reason='skills.tgz is not unpacked by the scanner')


def searched(inv):
    grok = [inv.home / '.grok', inv.home / '.config/grok']
    if 'GROK_EXPORT_DIR' in inv.env:
        grok.append(Path(inv.env['GROK_EXPORT_DIR']))
    return grok
