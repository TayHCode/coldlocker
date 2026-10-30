import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from checks.validators import require
from portable import lock, restrict
from store import ordinary_path


def zone(name):
    return timezone.utc if name == 'UTC' else ZoneInfo(name)


def cron_fields(expression):
    fields = expression.split()
    require(len(fields) == 5, 'local cron requires five numeric fields')
    result = []
    for field, low, high in zip(fields, (0, 0, 1, 1, 0), (59, 23, 31, 12, 7)):
        values = set()
        for part in field.split(','):
            span, separator, step = part.partition('/')
            require(not separator or (step.isdecimal() and int(step) > 0), 'invalid cron step')
            stride = int(step) if separator else 1
            if span == '*':
                start, end = low, high
            elif '-' in span:
                bounds = span.split('-')
                require(len(bounds) == 2 and all(x.isdecimal() for x in bounds), 'invalid cron range')
                start, end = map(int, bounds)
            else:
                require(span.isdecimal(), 'cron names and macros need a platform handoff')
                start = int(span)
                end = high if separator else start
            require(low <= start <= end <= high, 'cron field out of range')
            values.update(range(start, end + 1, stride))
        result.append(values)
    return result


def due(definition, previous, now):
    schedule = definition['schedule']
    kind = schedule['type']
    if kind == 'interval':
        return now - previous.get('timestamp', 0) >= schedule['every_seconds']
    local = datetime.fromtimestamp(now, zone(definition['timezone']))
    slot = local.strftime('%Y-%m-%dT%H:%M%z')
    if previous.get('slot') == slot:
        return False
    if kind == 'fixed_time':
        return (local.strftime('%H:%M') == schedule['time_of_day']
                and local.isoweekday() in (schedule.get('weekdays') or range(1, 8)))
    minute, hour, day, month, weekday = cron_fields(schedule['expression'])
    fields = schedule['expression'].split()
    dom = local.day in day
    dow = local.isoweekday() % 7 in {x % 7 for x in weekday}
    calendar_day = dom and dow if fields[2].startswith('*') or fields[4].startswith('*') else dom or dow
    return local.minute in minute and local.hour in hour and local.month in month and calendar_day


def private_write(path, value):
    ordinary_path(path)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temporary = path.with_name(path.name + '.pending-' + str(os.getpid()))
    ordinary_path(temporary)
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            restrict(stream, temporary)
            stream.write(value)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_job(path):
    path = ordinary_path(Path(path))
    guard = ordinary_path(path.with_suffix('.lock'))
    with guard.open('a+b') as stream:
        restrict(stream, guard)
        try:
            lock(stream, wait=False)
        except BlockingIOError:
            return 0
        if not path.exists():
            return 0
        job = json.loads(path.read_text(encoding='utf-8'))
        if not job.get('active'):
            return 0
        definition = job['definition']
        receipt = ordinary_path(path.with_suffix('.last.json'))
        previous = json.loads(receipt.read_text(encoding='utf-8')) if receipt.exists() else {}
        now = time.time()
        if not due(definition, previous, now):
            return 0
        private_write(receipt, json.dumps({'timestamp': now, 'slot': datetime.fromtimestamp(
            now, zone(definition['timezone'])).strftime('%Y-%m-%dT%H:%M%z')}))
    execution = ordinary_path(path.with_suffix('.execution.lock'))
    with execution.open('a+b') as stream:
        restrict(stream, execution)
        if definition['overlap'] != 'allow':
            try:
                lock(stream, wait=definition['overlap'] == 'queue')
            except BlockingIOError:
                return 0
        if not path.exists() or not json.loads(path.read_text(encoding='utf-8')).get('active'):
            return 0
        try:
            return subprocess.run(job['argv'] + [job['prompt']], cwd=job['cwd'],
                                  timeout=definition['timeout_seconds'],
                                  env={**os.environ, 'PATH': job['path']}).returncode
        except subprocess.TimeoutExpired:
            return 124


if __name__ == '__main__':
    raise SystemExit(run_job(sys.argv[1]))
