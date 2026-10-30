import getpass
import json
import os
import subprocess
import time
import xml.etree.ElementTree as ET

from checks.validators import require
from routine_runtime import private_write


NAMESPACE = 'http://schemas.microsoft.com/windows/2004/02/mit/task'


def task_xml(command, task):
    root = ET.Element('Task', {'version': '1.2', 'xmlns': NAMESPACE})
    def element(parent, tag, text=None, **attributes):
        child = ET.SubElement(parent, tag, attributes)
        child.text = text
        return child
    registration = element(root, 'RegistrationInfo')
    element(registration, 'Description', 'agentlocker owned routine: ' + task)
    element(registration, 'URI', task)
    trigger = element(element(root, 'Triggers'), 'TimeTrigger')
    repetition = element(trigger, 'Repetition')
    element(repetition, 'Interval', 'PT1M')
    element(repetition, 'StopAtDurationEnd', 'false')
    element(trigger, 'StartBoundary', '2000-01-01T00:00:00Z')
    element(trigger, 'Enabled', 'true')
    principal = element(element(root, 'Principals'), 'Principal', id='Author')
    user = getpass.getuser()
    domain = os.environ.get('USERDOMAIN', '')
    element(principal, 'UserId', domain + '\\' + user if domain else user)
    element(principal, 'LogonType', 'InteractiveToken')
    element(principal, 'RunLevel', 'LeastPrivilege')
    settings = element(root, 'Settings')
    element(settings, 'MultipleInstancesPolicy', 'Parallel')
    element(settings, 'DisallowStartIfOnBatteries', 'false')
    element(settings, 'StopIfGoingOnBatteries', 'false')
    element(settings, 'Enabled', 'true')
    element(settings, 'ExecutionTimeLimit', 'PT0S')
    action = element(element(root, 'Actions', Context='Author'), 'Exec')
    element(action, 'Command', command[0])
    element(action, 'Arguments', subprocess.list2cmdline(command[1:]))
    return ET.tostring(root, encoding='unicode') + '\n'


def signature(xml):
    def tree(node):
        return (node.tag, sorted(node.attrib.items()), node.text if not len(node) else '',
                [tree(child) for child in node])
    try:
        return json.dumps(tree(ET.fromstring(xml)))
    except ET.ParseError:
        raise ValueError('invalid Task Scheduler XML; refusing to change task') from None


def query(task):
    result = subprocess.run(['schtasks.exe', '/Query', '/TN', task, '/XML', '/HRESULT'],
                            capture_output=True, stdin=subprocess.DEVNULL)
    if result.returncode & 0xffffffff == 0x80070002:
        return None
    require(result.returncode == 0, 'cannot query Task Scheduler; refusing changes: ' + result.stderr.decode(errors='replace').strip())
    raw = result.stdout
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        xml = raw.decode('utf-16')
    elif b'\x00' in raw[:80]:
        xml = raw.decode('utf-16-le')
    else:
        xml = raw.decode('utf-8-sig')
    if xml.startswith('<?xml'):
        xml = xml.split('?>', 1)[1].lstrip('\r\n')
    signature(xml)
    return xml


def inspect(native, old):
    before = query(native['task'])
    require(before is None or (old.get('installed_xml') is not None
            and signature(before) == signature(old['installed_xml'])), 'unfamiliar or modified Task Scheduler task')
    return before


def sync(base, old, plan, enabling, before, changes, call):
    native = plan['native']
    xml_path = native['xml_path']
    if before is not None and enabling and old.get('files', {}).get(xml_path) == plan['files'][xml_path]:
        native['installed_xml'] = before
        return
    if before is None and not enabling:
        return
    require(query(native['task']) == before, 'Task Scheduler task changed; review and retry')
    backups = base / 'backups'
    recovery = {'before': before, 'after': None, 'task': native['task']}
    if before is not None:
        backup_path = backups / ('task-before-' + str(time.time_ns()) + '.xml')
        private_write(backup_path, before)
        recovery['backup'] = str(backup_path)
        call(['schtasks.exe', '/Delete', '/TN', native['task'], '/F'])
        changes['schtasks'] = recovery
    if enabling:
        call(['schtasks.exe', '/Create', '/TN', native['task'], '/XML', xml_path])
        changes['schtasks'] = recovery
        recovery['after'] = query(native['task'])
        require(recovery['after'] is not None, 'created Task Scheduler task is missing')
        native['installed_xml'] = recovery['after']
        private_write(backups / ('task-created-' + str(time.time_ns()) + '.xml'), recovery['after'])


def restore(change, call):
    actual = query(change['task'])
    require(actual == change['after'], 'Task Scheduler task changed during recovery; inspect backups')
    if actual is not None:
        call(['schtasks.exe', '/Delete', '/TN', change['task'], '/F'])
    if change['before'] is not None:
        call(['schtasks.exe', '/Create', '/TN', change['task'], '/XML', change['backup']])


def status(native):
    actual = query(native['task'])
    return {'exists': actual is not None,
            'matches_record': actual is not None and signature(actual) == signature(native['installed_xml'])}
