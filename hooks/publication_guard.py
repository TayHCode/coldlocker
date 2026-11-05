"""Conservative guard for direct shell publication commands, not a shell sandbox."""
import json
from pathlib import Path
import re
import shlex
import subprocess


REASON = ('Public publication is blocked by the publication hook. '
          'Public pushes require a release receipt for the exact commit. Private pushes require '
          'a standalone git push with an explicit remote whose destination is verified PRIVATE.')


def deny():
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
            'permissionDecision': 'deny', 'permissionDecisionReason': REASON}}


TOKENS = re.compile(r'''\#[^\n]*|(?:[^\s;&|()<>'"\\]|\\.|'[^']*'|"(?:\\.|[^"\\])*")+|<<-|[<>]+[&|]?|[;&|()\n]+''', re.S)
WRAPPER_ARGUMENTS = {
    'env': ('-u', '--unset', '-C', '--chdir', '-a', '--argv0'),
    'exec': ('-a',),
    'time': ('-f', '--format', '-o', '--output'),
    'nice': ('-n', '--adjustment'),
    'ionice': ('-c', '--class', '-n', '--classdata', '-p', '--pid', '-P', '--pgid', '-u', '--uid'),
    'timeout': ('-k', '--kill-after', '-s', '--signal'),
    'stdbuf': ('-i', '--input', '-o', '--output', '-e', '--error'),
    'sudo': ('-u', '--user', '-g', '--group', '-h', '--host', '-p', '--prompt', '-C', '--close-from',
             '-R', '--chroot', '-D', '--chdir', '-r', '--role', '-t', '--type', '-U', '--other-user'),
}
API_READ_OPTIONS = ('--paginate', '--slurp', '--include', '-i', '--silent', '--verbose', '--jq', '-q',
                    '--template', '-t', '--cache', '--hostname', '-H', '--header', '--help', '-h')


def without_heredocs(command):
    lines, pending = [], []
    for line in command.splitlines(keepends=True):
        if pending:
            delimiter, strip_tabs, expandable = pending[0]
            body = line.lstrip('\t') if strip_tabs else line
            if body.rstrip('\r\n') == delimiter:
                pending.pop(0)
            elif expandable and re.search(r'\$\(|`', body):
                raise ValueError('unsupported heredoc substitution')
            lines.append('\n')
            continue
        lines.append(line)
        raw = [match[0] for match in TOKENS.finditer(line)]
        for index, token in enumerate(raw[:-1]):
            if token in ('<<', '<<-'):
                delimiter = shlex.split(raw[index + 1])
                if len(delimiter) != 1:
                    raise ValueError('unsupported heredoc delimiter')
                expandable = not any(char in raw[index + 1] for char in "'\"\\")
                pending.append((delimiter[0], token == '<<-', expandable))
    if pending:
        raise ValueError('unterminated heredoc')
    return ''.join(lines)


def shell_commands(command):
    command = without_heredocs(command)
    words = []
    end = 0
    redirect_target = False
    for match in TOKENS.finditer(command):
        if command[end:match.start()].strip():
            raise ValueError('unsupported shell syntax')
        adjacent = match.start() == end
        end = match.end()
        token = match[0]
        if redirect_target:
            redirect_target = False
            continue
        if token.startswith(('<', '>')):
            if adjacent and words and words[-1].isdigit():
                words.pop()
            redirect_target = True
            continue
        if token.startswith('#'):
            continue
        if token and all(char in ';&|()\n' for char in token):
            if words:
                yield words
            words = []
        else:
            words.extend(shlex.split(token))
    if command[end:].strip():
        raise ValueError('unsupported shell syntax')
    if words:
        yield words


def publication_kind(words):
    words = list(words)
    while words:
        if words[0].rsplit('/', 1)[-1] in ('command', 'builtin', 'nohup', 'setsid', *WRAPPER_ARGUMENTS):
            wrapper = words.pop(0).rsplit('/', 1)[-1]
            while words and words[0].startswith('-'):
                option = words.pop(0)
                if option == '--':
                    break
                if wrapper == 'command' and any(flag in option for flag in ('v', 'V')):
                    return None
                split_option = re.fullmatch(r'-[iv0]*S(.*)', option, re.S) if wrapper == 'env' else None
                if split_option:
                    if not split_option[1] and not words:
                        raise ValueError('missing env split string')
                    value = split_option[1] or words.pop(0)
                    words = shlex.split(value) + words
                    continue
                if wrapper == 'env' and (option == '--split-string' or option.startswith('--split-string=')):
                    if '=' not in option and not words:
                        raise ValueError('missing env split string')
                    value = option.split('=', 1)[1] if '=' in option else words.pop(0)
                    words = shlex.split(value) + words
                elif option in WRAPPER_ARGUMENTS.get(wrapper, ()) and words:
                    words.pop(0)
            if wrapper == 'timeout' and words:
                words.pop(0)
        elif (words[0] in ('if', 'elif', 'then', 'else', 'while', 'until', 'do', '!', '{')
                or re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', words[0])):
            words.pop(0)
        else:
            break
    if not words:
        return None
    program = words[0].rsplit('/', 1)[-1]
    if program not in ('[', '[[') and re.search(r'[$`*?{}\[\]]', program):
        raise ValueError('unsupported executable expansion')
    if program in ('eval', 'trap', 'coproc', 'xargs', 'parallel'):
        return 'unsupported'
    if program == 'find' and any(word in ('-exec', '-execdir', '-ok', '-okdir') for word in words[1:]):
        return 'unsupported'
    if program in ('sh', 'bash', 'zsh', 'pwsh', 'powershell'):
        for index, word in enumerate(words[1:-1], 1):
            if word.lower() == '-command' or re.fullmatch(r'-[A-Za-z]*c[A-Za-z]*', word):
                if words[index + 1] == '-':
                    return 'unsupported'
                return command_kind(words[index + 1])
        if (not any(not word.startswith('-') for word in words[1:])
                or program in ('sh', 'bash', 'zsh') and any(
                    re.fullmatch(r'-[A-Za-z]*s[A-Za-z]*', word) for word in words[1:])):
            return 'unsupported'
    if program == 'git':
        index = 1
        while index < len(words) and words[index].startswith('-'):
            index += 2 if words[index] in ('-C', '-c', '--git-dir', '--work-tree',
                                           '--namespace', '--exec-path', '--config-env') else 1
        if index < len(words):
            if re.search(r'[$`*?{}\[\]]', words[index]):
                raise ValueError('unsupported git subcommand expansion')
            if words[index] == 'push':
                return 'push'
    if program == 'gh':
        if words[1:2] == ['api']:
            args = words[2:]
            for index, word in enumerate(args):
                if word in ('-X', '--method'):
                    if index + 1 >= len(args) or args[index + 1].upper() not in ('GET', 'HEAD'):
                        return 'unsupported'
                elif word.startswith('--method=') or word.startswith('-X'):
                    method = word.split('=', 1)[1] if '=' in word else word[2:]
                    if method.upper() not in ('GET', 'HEAD'):
                        return 'unsupported'
                elif word.startswith('-') and word.split('=', 1)[0] not in API_READ_OPTIONS:
                    return 'unsupported'
            return None
        for index in range(1, len(words) - 1):
            if words[index] == 'repo' and words[index + 1] in ('create', 'edit'):
                if ('--public' in words or any(word.startswith('--public=') and
                        word.split('=', 1)[1].lower() not in ('false', 'f', '0') for word in words)
                        or '--visibility=public' in words
                        or any(words[i:i + 2] == ['--visibility', 'public'] for i in range(len(words)))):
                    return 'public'
    if program not in ('git', 'gh', 'echo', 'printf', 'cat'):
        for index, word in enumerate(words[1:], 1):
            if word.rsplit('/', 1)[-1] in ('git', 'gh'):
                kind = publication_kind(words[index:])
                if kind is not None:
                    return kind
    return None


def command_kind(command):
    for match in TOKENS.finditer(without_heredocs(command)):
        if re.search(r'`|\$\(|[<>]\(', match[0]) and not re.fullmatch(r"'[^']*'", match[0]):
            raise ValueError('unsupported command substitution')
    kinds = [publication_kind(words) for words in shell_commands(command)]
    return next((kind for kind in ('public', 'push', 'unsupported') if kind in kinds), None)


def permitted_push(words, cwd):
    if words[:2] != ['git', 'push'] or not cwd:
        return False
    args = words[2:]
    while args and args[0] in ('-u', '--set-upstream', '--tags', '--all', '--dry-run', '--atomic'):
        args = args[1:]
    if not args or not re.fullmatch(r'[A-Za-z0-9_.-]+', args[0]) or args[0].startswith('-'):
        return False
    if any(not re.fullmatch(r'[A-Za-z0-9_./:+*-]+', arg) or arg.startswith('-') for arg in args[1:]):
        return False
    try:
        result = subprocess.run(['git', 'remote', 'get-url', '--push', '--all', args[0]],
                                cwd=cwd, capture_output=True, text=True, timeout=3)
        urls = result.stdout.splitlines()
        if result.returncode or len(urls) != 1:
            return False
        match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)', urls[0])
        if not match:
            return False
        repo = match[1].removesuffix('.git')
        result = subprocess.run(['gh', 'repo', 'view', 'https://github.com/' + repo, '--json', 'visibility'],
                                cwd=cwd, capture_output=True, text=True, timeout=3)
        if result.returncode:
            return False
        visibility = json.loads(result.stdout).get('visibility')
        if visibility not in ('PRIVATE', 'PUBLIC'):
            return False
        result = subprocess.run(['git', 'config', '--get', 'push.recurseSubmodules'],
                                cwd=cwd, capture_output=True, text=True, timeout=3)
        if result.returncode == 1:
            result = subprocess.run(['git', 'config', '--bool', '--get', 'submodule.recurse'],
                                    cwd=cwd, capture_output=True, text=True, timeout=3)
            if result.returncode != 1 and not (result.returncode == 0 and result.stdout.strip() == 'false'):
                return False
        elif result.returncode or result.stdout.strip().lower() not in ('no', 'false', 'off', '0', 'check'):
            return False
        if visibility == 'PRIVATE':
            return True
        if visibility != 'PUBLIC' or len(words) != 4 or words[2:] != args:
            return False
        refspec = re.fullmatch(r'([A-Za-z0-9_./-]+)(?::[A-Za-z0-9_./-]+)?', args[1])
        if not refspec or args[1].startswith('-'):
            return False
        result = subprocess.run(['git', 'rev-parse', '--verify', refspec[1]],
                                cwd=cwd, capture_output=True, text=True, timeout=3)
        sha = result.stdout.strip()
        if result.returncode or not re.fullmatch(r'[0-9a-f]{40}', sha):
            return False
        receipt = Path.home() / '.local/state/coldlocker/release-approvals' / repo.replace('/', '__') / sha
        if not receipt.is_file():
            return False
        result = subprocess.run(['git', 'config', '--bool', '--get', 'push.followTags'],
                                cwd=cwd, capture_output=True, text=True, timeout=3)
        return result.returncode == 1 or (result.returncode == 0 and result.stdout.strip() == 'false')
    except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
        return False


def publication_guard(payload):
    name = re.split(r'__|\.', payload.get('tool_name', ''))[-1]
    if name not in ('Bash', 'exec_command'):
        return {}
    tool = payload.get('tool_input') or {}
    command = tool.get('command') if name == 'Bash' else tool.get('cmd')
    if not isinstance(command, str):
        return deny()
    normalized = command.replace('\\\r\n', '').replace('\\\n', '')
    try:
        kind = command_kind(normalized)
    except ValueError:
        return deny()
    if kind in ('public', 'unsupported'):
        return deny()
    if kind is None:
        return {}
    if any(char in command for char in '\n\r;&|`$<>'):
        return deny()
    try:
        words = shlex.split(command)
    except ValueError:
        return deny()
    cwd = tool.get('workdir') or tool.get('cwd') or payload.get('cwd')
    return {} if permitted_push(words, cwd) else deny()
