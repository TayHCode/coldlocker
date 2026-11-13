import contextlib
import sqlite3

from checks import validators
from importers import selected_path

from .common import ERRORS, paths


def discover(inv):
    for base in dict.fromkeys(paths(inv.home, inv.platform, inv.env)['t3']):
        for path in (base / 'settings.json', base / 'userdata/settings.json'):
            inv.config('t3', path)
            inv.connections('t3', path)
        for path in inv.walk(base / 'userdata', depth=1, limit=100):
            if path.suffix in ('.sqlite', '.sqlite3', '.db'):
                database(inv, path)
        inv.tasks('t3', base / 'schedules')


def database(inv, path):
    try:
        selected_path(path.parent, path.name)
        with contextlib.closing(sqlite3.connect(path.absolute().as_uri() + '?mode=ro&immutable=1', uri=True, timeout=1)) as connection:
            connection.execute('PRAGMA query_only=ON')
            connection.set_progress_handler(lambda: 1, 1000000)
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'scheduled_tasks' not in tables:
                return
            columns = {row[1] for row in connection.execute('PRAGMA table_info(scheduled_tasks)')}
            validators.require({'task_id', 'title', 'prompt', 'schedule_json'} <= columns, 'unknown task schema')
            for row in connection.execute('SELECT task_id,title,prompt,schedule_json FROM scheduled_tasks LIMIT 1000'):
                try:
                    inv.routine('t3', path, row[1], inv.schedule(row[3]), row[2])
                except ERRORS:
                    inv.failed('t3', 'routine', path)
    except ERRORS:
        inv.failed('t3', 'routine', path)


def searched(inv):
    return paths(inv.home, inv.platform, inv.env)['t3']
