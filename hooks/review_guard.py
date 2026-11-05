import json
import os
from pathlib import Path
import re
import subprocess
import sys
import sqlite3


def dispatch_guard(payload, root, models):
    name = re.split(r'__|\.', payload.get('tool_name', ''))[-1]
    if name.endswith('spawn_agent'):
        name = 'spawn_agent'
    if name not in ('Task', 'Agent', 'delegate_task', 'spawn_agent'):
        return {}
    tool = payload.get('tool_input') or {}
    prompt = tool.get('task') or tool.get('prompt') or tool.get('message') or ''
    def field(key):
        match = re.search(r'^(?:coldlocker|fleet)-' + key + r': (.+)$', prompt, re.MULTILINE)
        return match[1].strip() if match else None
    if not field('role'):
        return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                'permissionDecisionReason': 'Dispatch requires readable coldlocker-role metadata; resolve cold model target and pass an explicit brief.'}}
    role = field('role')
    command = [sys.executable, str(Path(root) / 'cold'), 'model', 'target', role]
    for key in ('reason', 'audit-kind'):
        if field(key):
            command += ['--' + key, field(key)]
    for builder in re.findall(r'^(?:coldlocker|fleet)-builder-model: (\S+)\s*$', prompt, re.MULTILINE):
        command += ['--builder-model', builder]
    try:
        request_cwd = Path(payload.get('cwd') or Path.cwd())
        env = dict(os.environ, AGENTFLEET_REPO_ROOT=str(request_cwd), FLEET_MODELS_FILE=str(Path(models).resolve()))
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, cwd=request_cwd, env=env)
        expected = json.loads(result.stdout) if result.returncode == 0 else None
        actual = tool.get('target') or {}
        if name in ('Task', 'Agent'):
            actual = dict(providerInstanceId='claudeAgent', model=tool.get('model'))
        elif name == 'spawn_agent':
            actual = dict(providerInstanceId='codex', model=tool.get('model'),
                          options=[{'id': 'reasoningEffort', 'value': tool['reasoning_effort']}] if 'reasoning_effort' in tool else [])
        is_review = tool.get('role') == 'review' or role in ('reviewer', 'auditor/verifier', 'auditor-high-stakes', 'gate-verifier', 'gate-high-risk')
        permitted_role = not is_review or role in ('reviewer', 'auditor/verifier', 'auditor-high-stakes', 'gate-verifier', 'gate-high-risk')
        permitted_options = name not in ('delegate_task', 'spawn_agent') or actual.get('options') == (expected or {}).get('options')
        if (expected and permitted_role and actual.get('providerInstanceId') == expected['providerInstanceId']
                and actual.get('model') == expected['model'] and permitted_options):
            if 'opus' in expected['model'].lower():
                sys.path.insert(0, str(root))
                from ops import model_usage
                model_usage.reserve_opus(model_usage.repo_key(request_cwd))
            return {}
        reason = result.stderr.strip() or 'target must explicitly match models.yaml; inheritance and auto are forbidden'
    except (OSError, ValueError, sqlite3.Error, subprocess.TimeoutExpired) as error:
        reason = str(error) if isinstance(error, ValueError) else 'routing could not be verified: ' + type(error).__name__
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
            'permissionDecisionReason': 'Resolve cold model target ' + role + '. ' + reason}}
