import json
import plistlib
import re
import subprocess

from checks import validators
from importers import LIMIT

from .common import ERRORS, read
from .schedules import calendar_schedule


def default_runner(command):
    validators.require(command == ['crontab', '-l'], 'unsupported runner read')
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        try:
            output, error = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise TimeoutError('crontab -l timed out') from None
        if process.returncode == 0:
            return output[:LIMIT].decode('utf-8', errors='replace')
        if b'no crontab for' in error.lower():
            return ''
        raise OSError('crontab -l failed')


def discover(inv):
    if inv.platform != 'win32' and inv.live:
        try:
            output = inv.runner(['crontab', '-l'])
            if hasattr(output, 'stdout'):
                output = output.stdout
            if isinstance(output, bytes):
                output = output.decode()
            for index, line in enumerate(str(output)[:LIMIT].splitlines()):
                line = line.strip()
                if not line or line.startswith('#') or re.match(r'\w+=', line):
                    continue
                fields = line.split(None, 5)
                aliases = {'@yearly': '0 0 1 1 *', '@annually': '0 0 1 1 *', '@monthly': '0 0 1 * *',
                           '@weekly': '0 0 * * 0', '@daily': '0 0 * * *', '@midnight': '0 0 * * *', '@hourly': '0 * * * *'}
                if fields[0] in aliases and len(fields) >= 2:
                    inv.routine('system', '<system:crontab>', f'cron-{index + 1}', {'type': 'cron', 'expression': aliases[fields[0]]}, line.split(None, 1)[1], 'cron', inv.zone)
                elif len(fields) == 6:
                    inv.routine('system', '<system:crontab>', f'cron-{index + 1}', {'type': 'cron', 'expression': ' '.join(fields[:5])}, fields[5], 'cron', inv.zone)
                else:
                    inv.failed('system', 'routine', '<system:crontab>')
        except ERRORS:
            inv.failed('system', 'routine', '<system:crontab>')
    for path in inv.walk(inv.home / '.config/systemd/user', depth=1):
        if path.suffix != '.timer':
            continue
        try:
            import configparser
            config = configparser.ConfigParser(interpolation=None, strict=False)
            config.read_string(read(path).decode())
            timer = config['Timer']
            service = path.with_name(timer.get('Unit', path.stem + '.service'))
            validators.require(service.parent == path.parent, 'invalid service')
            validators.require(service.is_file(), 'service file missing')
            prompt = read(service).decode()
            calendar = timer.get('OnCalendar')
            timezone = inv.zone
            if calendar == 'daily':
                schedule = {'type': 'fixed_time', 'time_of_day': '00:00'}
            elif calendar == 'hourly':
                schedule = {'type': 'interval', 'every_seconds': 3600}
            elif calendar is not None:
                schedule, timezone = calendar_schedule(calendar.strip(), inv.zone)
            else:
                delay = timer.get('OnUnitActiveSec', '')
                match = re.fullmatch(r'(\d+)(s|min|m|h|d)?', delay)
                validators.require(bool(match), 'unsupported OnCalendar expression (only daily, hourly or OnUnitActiveSec intervals convert)')
                schedule = {'type': 'interval', 'every_seconds': int(match[1]) * {'s': 1, 'min': 60, 'm': 60, 'h': 3600, 'd': 86400, None: 1}[match[2]]}
            inv.routine('system', path, path.stem, schedule, prompt, 'systemd-timer', timezone, label=service.name)
        except ERRORS:
            inv.failed('system', 'routine', path)
    if inv.platform == 'darwin':
        for path in inv.walk(inv.home / 'Library/LaunchAgents', depth=1):
            if path.suffix != '.plist':
                continue
            try:
                value = plistlib.loads(read(path))
                if 'StartInterval' in value:
                    schedule = {'type': 'interval', 'every_seconds': value['StartInterval']}
                else:
                    calendar = value['StartCalendarInterval']
                    validators.require(set(calendar) <= {'Hour', 'Minute'}, 'manual calendar conversion')
                    schedule = {'type': 'fixed_time', 'time_of_day': f"{calendar.get('Hour', 0):02}:{calendar.get('Minute', 0):02}"}
                inv.routine('system', path, value.get('Label', path.stem), schedule, json.dumps(value.get('ProgramArguments', [])), 'launchd', inv.zone)
            except ERRORS:
                inv.failed('system', 'routine', path)


def searched(inv):
    return [inv.home / '.config/systemd/user', inv.home / 'Library/LaunchAgents']
