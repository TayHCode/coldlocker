import contextlib
import os
import re

from checks import validators


ZONE = re.compile(r'[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+)*')
WEEKDAYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')


def calendar_field(spec, low, high):
    values = set()
    parts = [re.fullmatch(r'(\*|\d+)(?:/(\d+))?', part) for part in spec.split(',')]
    validators.require(all(parts), 'unsupported OnCalendar expression')
    for match in parts:
        start = low if match[1] == '*' else int(match[1])
        step = int(match[2]) if match[2] else None
        validators.require(low <= start <= high and step != 0, 'unsupported OnCalendar expression')
        if step:
            values.update(range(start, high + 1, step))
        else:
            values.update(range(low, high + 1) if match[1] == '*' else [start])
    if len(parts) == 1 and parts[0][2] and int(parts[0][2]) > 1 and (parts[0][1] == '*' or int(parts[0][1]) == low):
        return f'*/{parts[0][2]}'
    return '*' if values == set(range(low, high + 1)) else ','.join(str(value) for value in sorted(values))


def calendar_weekdays(spec):
    days = set()
    for part in spec.lower().split(','):
        match = re.fullmatch(r'([a-z]{3})[a-z]*(?:\.\.([a-z]{3})[a-z]*)?', part)
        validators.require(bool(match) and match[1] in WEEKDAYS and match[2] in (None, *WEEKDAYS), 'unsupported OnCalendar expression')
        first = WEEKDAYS.index(match[1])
        last = WEEKDAYS.index(match[2]) if match[2] else first
        validators.require(first <= last, 'unsupported OnCalendar expression')
        days.update((day + 1) % 7 for day in range(first, last + 1))
    return '*' if len(days) == 7 else ','.join(str(day) for day in sorted(days))


def local_timezone(env, localtime=None):
    name = env.get('TZ', '').lstrip(':')
    if ZONE.fullmatch(name):
        return name
    if localtime:
        with contextlib.suppress(OSError):
            _, found, zone = os.readlink(localtime).partition('zoneinfo/')
            if found and ZONE.fullmatch(zone):
                return zone
    return 'UTC'


def calendar_schedule(text, zone='UTC'):
    if text == 'weekly':
        return {'type': 'cron', 'expression': '0 0 * * 1'}, zone
    tokens = text.split()
    weekdays = '*'
    if tokens and re.fullmatch(r'[A-Za-z]+(?:\.\.[A-Za-z]+)?(?:,[A-Za-z]+(?:\.\.[A-Za-z]+)?)*', tokens[0]):
        weekdays = calendar_weekdays(tokens.pop(0))
    days = '*'
    if tokens and re.fullmatch(r'\*-\*-(\*|\d+(?:,\d+)*)', tokens[0]):
        days = calendar_field(tokens.pop(0).split('-')[2], 1, 31)
    validators.require(bool(tokens) and (weekdays == '*' or days == '*'), 'unsupported OnCalendar expression')
    fields = tokens.pop(0).split(':')
    validators.require(len(fields) in (2, 3), 'unsupported OnCalendar expression')
    if len(fields) == 3:
        validators.require(bool(re.fullmatch(r'[0-5]?\d', fields[2])), 'unsupported OnCalendar expression')
        validators.require(int(fields[2]) == 0,
                           'unsupported OnCalendar expression (nonzero seconds need manual conversion)')
    timezone = tokens.pop(0) if tokens else zone
    validators.require(not tokens and bool(ZONE.fullmatch(timezone)), 'unsupported OnCalendar expression')
    expression = f'{calendar_field(fields[1], 0, 59)} {calendar_field(fields[0], 0, 23)} {days} * {weekdays}'
    return {'type': 'cron', 'expression': expression}, timezone
