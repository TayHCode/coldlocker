import hashlib
import json
from pathlib import Path
import re

import safety
from checks import validators
from importers import json_object

from .common import ERRORS, digest, encoded, read, slug


ACCOUNT_NUMBER = re.compile(r'(?<!\d)\d{3}-\d{3}-\d{4}(?!\d)')


def routine_id(title):
    full = re.sub('[^a-z0-9-]+', '-', str(title).lower()).strip('-')
    if len(full) <= 63:
        return slug(full)
    head = full[:57]
    stem = head.rsplit('-', 1)[0] if '-' in head else full[:56]
    return slug(stem + '-' + hashlib.sha256(full.encode()).hexdigest()[:6])


def owner_label(value):
    label = re.sub('[^a-z0-9-]+', '-', str(value or '').lower()).strip('-')[:63]
    return label or 'agentlocker'


class RoutineMixin:
    def routine(self, agent, path, name, schedule, prompt, runner='t3-scheduler', timezone='UTC', owner='agentlocker', label='prompt', enabled_in_file=None):
        title = ' '.join(str(name).split())
        account = bool(ACCOUNT_NUMBER.search(title))
        hidden = account or re.search(r'\d{6,}', title)
        name = 'routine-' + digest(encoded([title, schedule, prompt]))[:8] if hidden else routine_id(title)
        description = 'Imported disabled schedule' if hidden or not title else title
        review = [safety.finding('private', 'account-number', 'title contains what looks like an account number', 'title')] if account else []
        if isinstance(prompt, str) and ACCOUNT_NUMBER.search(prompt):
            review.append(safety.finding('private', 'account-number', 'prompt contains what looks like an account number', 'prompt'))
        value = dict(id=name, description=description, owner=owner, runner=runner,
                     schedule=schedule, timezone=timezone, prompt='prompt.md', overlap='skip', timeout_seconds=600, enabled=False)
        prompt = self.tilde(prompt)
        try:
            validators.routine(value)
            validators.safe_fragment(prompt)
            self.add(agent, 'routine', name, path, (value, prompt), digest(encoded([value, prompt])), review=review, enabled_in_file=enabled_in_file,
                     flags=['checkpointed-snapshot'] if agent == 't3' and Path(str(path) + '-wal').exists() and Path(str(path) + '-wal').stat().st_size else [])
        except ERRORS:
            self.failed(agent, 'routine', path, name, files=[(label, prompt)] if isinstance(prompt, str) else [])

    def schedule(self, value):
        if isinstance(value, str):
            value = json.loads(value)
        kind = value['type']
        if kind == 'webhook':
            return {'type': kind}
        if kind == 'interval':
            seconds = value.get('every_seconds', value.get('everyMs', 0) / 1000)
            validators.require(seconds == int(seconds), 'fractional interval')
            return {'type': kind, 'every_seconds': int(seconds)}
        if kind == 'fixed_time':
            result = {'type': kind, 'time_of_day': value.get('time_of_day', value.get('timeOfDay'))}
            if 'weekdays' in value:
                result['weekdays'] = value['weekdays']
            return result
        return value

    def rrule(self, text):
        fields = dict(part.split('=', 1) for part in text.removeprefix('RRULE:').split(';'))
        validators.require(not (set(fields) - {'FREQ', 'INTERVAL', 'BYHOUR', 'BYMINUTE', 'BYDAY'}), 'manual recurrence conversion')
        frequency = fields['FREQ']
        if frequency in ('HOURLY', 'MINUTELY') and set(fields) <= {'FREQ', 'INTERVAL'}:
            interval = int(fields.get('INTERVAL', 1))
            validators.require(interval > 0, 'manual recurrence conversion')
            multiplier = 3600 if frequency == 'HOURLY' else 60
            return {'type': 'interval', 'every_seconds': interval * multiplier}
        validators.require(frequency in ('DAILY', 'WEEKLY') and fields.get('INTERVAL', '1') == '1', 'manual recurrence conversion')
        result = {'type': 'fixed_time', 'time_of_day': f"{int(fields.get('BYHOUR', 0)):02}:{int(fields.get('BYMINUTE', 0)):02}"}
        if 'BYDAY' in fields:
            days = {'MO': 1, 'TU': 2, 'WE': 3, 'TH': 4, 'FR': 5, 'SA': 6, 'SU': 7}
            result['weekdays'] = [days[day] for day in fields['BYDAY'].split(',')]
        validators.require(frequency != 'WEEKLY' or 'weekdays' in result, 'weekly days required')
        return result

    def tasks(self, agent, base, runner='t3-scheduler', on_skill=None):
        for path in [base] if base.is_file() else self.walk(base, depth=3):
            if on_skill and path.name == 'SKILL.md':
                on_skill(self, agent, path)
                continue
            if path.suffix not in ('.json', '.toml'):
                continue
            fallback = path.parent.name if path.name == 'automation.toml' else path.stem
            try:
                if path.suffix == '.toml':
                    import tomllib
                    value = tomllib.loads(read(path).decode())
                else:
                    value = json_object(read(path).decode())
                if isinstance(value, dict) and 'rrule' in value and 'schedule' not in value:
                    value['schedule'] = self.rrule(value['rrule'])
                entries = value if isinstance(value, list) else value.get('tasks', value.get('routines', [value]))
                for task in entries:
                    name = task.get('name', task.get('id', fallback)) if isinstance(task, dict) else fallback
                    try:
                        schedule = self.schedule(task.get('schedule', task.get('trigger')))
                        prompt = task.get('prompt', '')
                        if isinstance(prompt, str) and len(Path(prompt).parts) == 1 and prompt.endswith('.md') and (path.parent / prompt).exists():
                            prompt = read(path.parent / prompt).decode()
                        self.routine(agent, path, name, schedule, prompt,
                                     runner=task.get('runner', runner),
                                     timezone=task.get('timezone', 'UTC'), owner=owner_label(task.get('owner_bot')), enabled_in_file=task.get('enabled'))
                    except ERRORS:
                        self.failed(agent, 'routine', path, name)
            except ERRORS:
                self.failed(agent, 'routine', path, fallback)
