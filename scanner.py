import contextlib
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import plistlib
import sys
import tempfile

from checks import validators
from checks.scanners import registry
from checks.scanners.common import (ERRORS, EXCLUDE, KINDS, MANUAL_IMPORT, NEXT_STEPS, NOT_SCANNED, PRIVATE, REPLACES, REUSED,
                                    SOURCE_PATH, STATUSES, conflict, credential_finding, digest, disabled_names, encoded,
                                    home_pattern, linked, located, next_step, paths, read, slug, texts, tree_hash, walk)
from checks.scanners.inventory import Inventory
from checks.scanners.plugins import plugin_name
from checks.scanners.routines import ACCOUNT_NUMBER, owner_label, routine_id
from checks.scanners.schedules import ZONE, WEEKDAYS, calendar_field, calendar_schedule, calendar_weekdays, local_timezone
from checks.scanners.servers import SECRET_KEY, SECRET_SHAPES, environment_headers
from checks.scanners.system import default_runner
from importers import LIMIT, selected_path, skill_package
from portable import python_command

ROOT = Path(__file__).resolve().parent


def scan(home, projects=(), platform=None, env=None, runner=None, root=None):
    return Inventory(home, projects, platform, env, runner, ROOT if root is None else root).discover()


def apply(root, report, selected_ids, store, home, projects=(), platform=None, env=None, runner=None, approved=(), live=True):
    from hub import import_item, ordinary_parents, write_json
    from types import SimpleNamespace
    root = Path(root)
    inventory = Inventory(home, projects, platform, env, runner, root, live)
    fresh = inventory.discover()
    selected = set(selected_ids)
    approved = set(approved)
    links = linked(report['items'])
    granted = {connection for connection, server in links.items() if server in approved and server in selected}
    approved = (approved - links.keys()) | granted
    selected |= granted

    def cleared(item):
        return item['status'] == 'needs_review' and item['id'] in approved and not any(f['blocking'] for f in item['review'])

    allowed = {i['id'] for i in report['items'] if (i['status'] in ('new', 'connect') and i['importable']) or cleared(i)}
    imported = []
    rejected = [i['id'] for i in report['items'] if i['id'] in selected and i['status'] == 'needs_review' and not cleared(i)]
    reasons = {}
    for item in fresh['items']:
        identity = item['id']
        if identity not in selected or identity not in allowed or not (item['importable'] or cleared(item)):
            continue
        payload, secrets = inventory.payloads[identity]
        kind = item['kind']
        name = item['name']
        relative = {'skill': f'skills/{name}', 'rule': f'rules/{name}.md', 'routine': f'routines/{name}/routine.json',
                    'mcp_server': f'mcp/servers/{name}.json', 'plugin': f'plugins/{name}.json', 'connection': f'connections/{name}.json'}[kind]
        destination = root / validators.LOCAL / relative
        try:
            ordinary_parents(destination.parent)
            validators.require(not destination.is_symlink() and (item.get('reason') == REPLACES or not destination.exists()), 'unindexed destination')
            with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
                temporary = Path(directory)
                owner = payload[0]['owner'] if kind == 'routine' else 'agentlocker'
                if kind == 'skill':
                    if isinstance(payload, dict):
                        path = temporary / name
                        path.mkdir()
                        for relative, text in payload.items():
                            target = path / relative
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(text.encode('utf-8'))
                    else:
                        path = payload
                    skill_package(path)
                elif kind == 'rule':
                    path = temporary / (name + '.md')
                    path.write_bytes(payload)
                else:
                    path = temporary / 'input.json'
                    value = payload[0] if kind == 'routine' else payload
                    if kind == 'routine':
                        (temporary / 'prompt.md').write_bytes(payload[1].encode('utf-8'))
                    write_json(path, value)
                for secret_name, value in secrets.items():
                    store.put(secret_name, value)
                import_item(SimpleNamespace(command='import-' + ('mcp' if kind == 'mcp_server' else kind), path=str(path),
                                            owner=owner, origin='scan-' + item['agent'], name=name, approve_risky=True,
                                            path_bindings=inventory.path_bindings.get(identity, {})), root)
            imported.append(identity)
        except ERRORS as error:
            rejected.append(identity)
            reasons[identity] = str(error) or type(error).__name__
    return {'imported': imported, 'rejected': rejected, 'reasons': reasons, 'counts': {'imported': len(imported), 'rejected': len(rejected)}}


def human(report, all_items=False, missing=None, hint=False):
    collapsed = ('vendor', 'plugin_cache', 'duplicate', 'already_in_locker')
    for agent, counts in report['counts'].items():
        print(agent + ': ' + ', '.join(f'{c} {n}' for c, n in counts.items()))
        hidden = dict.fromkeys(collapsed, 0)
        for item in report['items']:
            if item['agent'] != agent:
                continue
            status = item['status']
            if not all_items and status in ('vendor', 'duplicate', 'already_in_locker'):
                category = 'plugin_cache' if status == 'vendor' and item['ownership'] == 'plugin_cache' else status
                hidden[category] += 1
                continue
            detail = ', '.join(item['flags'])
            if status == 'skipped':
                detail = 'reason: ' + (', '.join(filter(None, [item.get('reason'), detail])) or 'unsupported or unsafe input')
            elif status in ('conflict', 'new'):
                detail = ', '.join(filter(None, [item.get('reason'), detail]))
            label = 'needs review' if status == 'needs_review' else status
            print(f"  {item['id']} {item['name']} ({item['kind']}) {label}" + (' [' + detail + ']' if detail else ''))
            if status in ('skipped', 'conflict') and item.get('next_step'):
                print(f"    next: {item['next_step']}")
            if status in ('needs_review', 'skipped'):
                for found in item.get('review', []):
                    print(f"    - {found['reason']} ({found['location']})")
        if any(hidden.values()):
            print('  Collapsed: ' + ', '.join(f'{kind} {count}' for kind, count in hidden.items()) + ' (use --all for every item)')
    for agent, looked in (missing or {}).items():
        print(f'{agent}: nothing found (looked in {", ".join(looked)})')
    unscanned = report.get('not_scanned', {})
    if unscanned.get('agents'):
        print('Not scanned (no importer): ' + ', '.join(f"{a['name']} {a['path']}" for a in unscanned['agents']))
        print('  ' + unscanned['next_step'])
    for incomplete in report.get('incomplete_inventory', []):
        print(f"Incomplete inventory: {incomplete['source']} reached the limit of {incomplete['limit']} entries. Narrow that folder and scan again.")
    if hint:
        print('Project-level skills are not scanned by default; pass --project <dir> to include them.')


def safety_summary(report):
    flagged = [i for i in report['items'] if i['review']]
    return {'scanned': sum(i['ownership'] == 'user' and i['status'] not in ('skipped', 'vendor') for i in report['items']),
            'flagged': len(flagged), 'needs_review': sum(i['status'] == 'needs_review' for i in report['items']),
            'findings': sum(len(i['review']) for i in flagged)}


def safety_report(report):
    agents = {}
    for item in report['items']:
        if item['review']:
            agents.setdefault(item['agent'], []).append({k: item[k] for k in ('id', 'kind', 'name', 'source', 'status', 'review')})
    return {'version': 1, 'summary': safety_summary(report), 'agents': agents}


def safety_human(report):
    result = safety_report(report)
    summary = result['summary']
    print(f"Safety scan: {summary['flagged']} of {summary['scanned']} items have findings ({summary['needs_review']} need review before import)")
    for agent, items in result['agents'].items():
        print(agent)
        for item in items:
            label = 'needs review' if item['status'] == 'needs_review' else item['status']
            print(f"  {item['id']} {item['name']} ({item['kind']}) {label}")
            for found in item['review']:
                print(f"    - [{found['category']}] {found['reason']} ({found['location']})")
    if not result['agents']:
        print('No findings.')


def cli(args):
    from store import FileStore
    args.approve_risky = [identity for group in args.approve_risky for identity in group]
    projects = list(args.project)
    if args.projects_root:
        base = selected_path(Path(args.projects_root))
        projects += [p for p in itertools.islice(base.iterdir(), 1000) if p.is_dir() and not p.is_symlink() and p.name not in EXCLUDE]
    live = args.live or os.path.realpath(args.home) == os.path.realpath(Path.home())
    if not live:
        print('Skipped crontab and Executor discovery: --home is not the real home. Pass --live to include them.', file=sys.stderr if args.json or args.safety else sys.stdout)
    inventory = Inventory(args.home, projects, args.platform, None, None, ROOT, live)
    report = inventory.discover()
    if args.agent:
        report['items'] = [i for i in report['items'] if i['agent'] in args.agent]
        report['agents'] = {k: v for k, v in report['agents'].items() if k in args.agent}
        report['counts'] = {k: v for k, v in report['counts'].items() if k in args.agent}
        report['totals'] = {c: sum(v[c] for v in report['counts'].values()) for c in KINDS.values()}
        report['totals']['statuses'] = {s: sum(i['status'] == s for i in report['items']) for s in STATUSES}
    missing = {agent: inventory.searched(agent) for agent in dict.fromkeys(args.agent or ()) if agent not in report['counts']}
    hint = not projects
    validators.require(args.do_import or not args.approve_risky, '--approve-risky requires --import')
    if args.safety:
        validators.require(not args.do_import and not args.only and not args.yes, '--safety is read-only and cannot be combined with import options')
        print(json.dumps(safety_report(report), indent=2)) if args.json else safety_human(report)
        return 0
    if not args.do_import:
        validators.require(not args.only and not args.yes, '--only and --yes require --import')
        print(json.dumps(report, indent=2)) if args.json else human(report, args.all, missing, hint)
        return 0
    human(report, args.all, missing, hint)
    if not args.yes:
        if not sys.stdin.isatty():
            print('Import refused: non-interactive input requires --yes.', file=sys.stderr)
            return 1
        if input('Import all selected importable items? [y/N] ').lower() not in ('y', 'yes'):
            return 1
    reviewed = {i['id'] for i in report['items'] if i['status'] == 'needs_review'}
    validators.require(set(args.approve_risky) <= reviewed, '--approve-risky needs ids shown as needs review')
    selected = args.only if args.only is not None else [i['id'] for i in report['items'] if i['importable']] + list(args.approve_risky)
    validators.require(set(selected) <= {i['id'] for i in report['items']}, 'unknown selection')
    result = apply(ROOT, report, selected, FileStore(ROOT), args.home, projects, args.platform, approved=args.approve_risky, live=live)
    held = sorted(reviewed - set(args.approve_risky))
    if held:
        print(f'{len(held)} item(s) need review and were not imported; inspect them with --safety, then pass --approve-risky <id>.', file=sys.stderr)
    print(json.dumps(result))
    for identity in result['rejected']:
        item = next((item for item in report['items'] if item['id'] == identity), None)
        reasons = '; '.join(f"{finding['reason']} ({finding['location']})" for finding in item.get('review', [])) if item else ''
        reasons = reasons or result['reasons'].get(identity)
        print(f"Rejected {identity}: {reasons or 'item could not be imported'}", file=sys.stderr)
    if (ROOT / 'connect.py').is_file():
        print(python_command() + ' hub.py connect')
    return 1 if result['rejected'] else 0


def register(commands):
    command = commands.add_parser('scan', help='Discover local agent content; preview unless --import is explicit.')
    command.add_argument('--home', type=Path, default=Path.home())
    command.add_argument('--project', type=Path, action='append', default=[])
    command.add_argument('--projects-root', type=Path)
    command.add_argument('--platform', choices=('linux', 'darwin', 'win32'))
    command.add_argument('--agent', action='append', choices=tuple(registry.SEARCHED))
    command.add_argument('--live', action='store_true', help='With an alternate --home, still read the real crontab and query a local Executor.')
    command.add_argument('--json', action='store_true')
    command.add_argument('--all', action='store_true', help='List every item, including vendor content and duplicates.')
    command.add_argument('--import', dest='do_import', action='store_true')
    command.add_argument('--only', nargs='+')
    command.add_argument('--yes', action='store_true')
    command.add_argument('--safety', action='store_true', help='Read-only safety report of risky items, grouped by agent.')
    command.add_argument('--approve-risky', action='append', nargs='+', default=[], metavar='ID', help='With --import, also import these needs-review items after you inspected them; repeat the flag or pass several IDs.')
    command.set_defaults(action=cli)
