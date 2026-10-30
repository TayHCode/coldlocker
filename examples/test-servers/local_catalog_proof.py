"""Python 3.11+ offline CLI/catalog fixture; no product login or runner execution."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None


ROOT = Path(__file__).resolve().parents[2]
PROOF = 'examples/test-servers/local_catalog_proof.py'
KINDS = ('skills', 'rules', 'mcp_servers', 'routines', 'plugins', 'connections')
RPC_TIMEOUT = 45


class ProofFailure(Exception):
    pass


def require(condition, stage):
    if not condition:
        raise ProofFailure(stage)


def environment(base):
    home, temporary = base / 'home', base / 'tmp'
    values = {'HOME': home, 'USERPROFILE': home, 'TMPDIR': temporary,
              'TMP': temporary, 'TEMP': temporary, 'APPDATA': home / 'AppData/Roaming',
              'LOCALAPPDATA': home / 'AppData/Local', 'XDG_CONFIG_HOME': home / '.config',
              'XDG_DATA_HOME': home / '.local/share', 'XDG_STATE_HOME': home / '.local/state',
              'XDG_CACHE_HOME': home / '.cache', 'CODEX_HOME': home / '.codex',
              'CLAUDE_CONFIG_DIR': home / '.claude'}
    for path in values.values():
        path.mkdir(parents=True, exist_ok=True)
    env = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'WINDIR') if key in os.environ}
    env.update({key: str(value) for key, value in values.items()})
    env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
               GIT_TERMINAL_PROMPT='0')
    return env


def run(argv, root, env, data='', expected=0):
    try:
        result = subprocess.run(argv, cwd=root, env=env, input=data, text=True,
                                encoding='utf-8', capture_output=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired):
        raise ProofFailure('subprocess unavailable or timed out') from None
    require(result.returncode == expected, 'unexpected subprocess exit')
    return result


def reviewed_copy(source, target, env):
    tracked = run(['git', 'ls-files', '-z'], source, env).stdout.split('\0')
    names = {name for name in tracked if name and
             (('/' not in name and name.endswith('.py')) or
              (name.startswith('checks/') and name.endswith('.py')) or
              name.startswith(('skills/', 'rules/', 'routines/', 'mcp/servers/')) or
              name == 'manifest.json')}
    names.add(PROOF)
    if (source / 'remote_settings.py').exists() or (source / 'remote_settings.py').is_symlink():
        names.add('remote_settings.py')
    require({'hub.py', 'mcp_endpoint.py', 'manifest.json'} <= names, 'missing reviewed source')
    revision = run(['git', 'rev-parse', 'HEAD'], source, env).stdout.strip()
    require(re.fullmatch('[0-9a-f]{40,64}', revision), 'invalid source revision')
    digest = hashlib.sha256()
    for name in sorted(names):
        relative = Path(name)
        require(not relative.is_absolute() and '..' not in relative.parts, 'invalid source path')
        path = source
        for part in relative.parts:
            path = path / part
            require(not path.is_symlink(), 'symlinked source refused')
        require(path.is_file(), 'missing ordinary source')
        data = path.read_bytes()
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        digest.update(name.encode() + b'\0' + hashlib.sha256(data).digest())
    return {'revision': revision, 'sha256': digest.hexdigest(), 'file_count': len(names),
            'digest_scope': 'copied runtime/catalog files plus proof; working bytes, including local edits'}


class Fixture:
    def __enter__(self):
        require(tomllib is not None, 'Python 3.11+ with tomllib required')
        self.temporary = tempfile.TemporaryDirectory(prefix='alc-')
        self.base = Path(self.temporary.name)
        try:
            self.env = environment(self.base)
            self.root = self.base / 'a b'
            self.root.mkdir()
            self.source = reviewed_copy(ROOT, self.root, self.env)
            guard = self.base / 'guard'
            guard.mkdir()
            (guard / 'sitecustomize.py').write_text(
                'import sys\n'
                'def offline(event, args):\n'
                '    if event.startswith("socket.") or event in ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn"):\n'
                '        raise OSError("offline fixture boundary")\n'
                'sys.addaudithook(offline)\n', encoding='utf-8')
            self.env['PYTHONPATH'] = str(guard)
            self.home = Path(self.env['HOME'])
            self.claude = Path(self.env['CLAUDE_CONFIG_DIR']) / '.claude.json'
            self.codex = Path(self.env['CODEX_HOME']) / 'config.toml'
            self.original = {
                self.claude: b'{"theme":"fixture-sentinel","mcpServers":{"unrelated":{"command":"fixture-only"}}}\n',
                self.codex: b'# fixture-sentinel\nmodel = "fixture-model"\n[mcp_servers.unrelated]\ncommand = "fixture-only"\n',
            }
            for path, data in self.original.items():
                path.write_bytes(data)
            self.skill = self.base / 'inputs/catalog-sample'
            (self.skill / 'references').mkdir(parents=True)
            (self.skill / 'SKILL.md').write_text(
                '---\nname: catalog-sample\ndescription: Read the fixture reference.\n---\n\nRead references/guide.md.\n', encoding='utf-8')
            self.guide = self.skill / 'references/guide.md'
            self.guide.write_text('Fixture guide version one.\n', encoding='utf-8')
            self.routine = self.base / 'inputs/routine/routine.json'
            self.routine.parent.mkdir()
            self.routine.write_text(json.dumps({
                'id': 'catalog-sample-routine', 'description': 'Read fixture inventory.',
                'owner': 'fixture', 'runner': 't3-scheduler',
                'schedule': {'type': 'interval', 'every_seconds': 3600}, 'timezone': 'UTC',
                'prompt': 'prompt.md', 'overlap': 'skip', 'timeout_seconds': 60, 'enabled': False,
            }), encoding='utf-8')
            (self.routine.parent / 'prompt.md').write_text('Read the fixture guide without changes.\n', encoding='utf-8')
            self.rule = self.base / 'inputs/unrelated.md'
            self.rule.write_text('Preserve this unrelated fixture rule.\n', encoding='utf-8')
            self.unrelated = self.home / 'unrelated.bin'
            self.unrelated.write_bytes(b'fixture-sentinel\x00\xff')
            self.commands = []
            return self
        except BaseException:
            self.temporary.cleanup()
            raise

    def __exit__(self, *args):
        self.temporary.cleanup()

    def cli(self, *args, expected=0):
        result = run([sys.executable, 'hub.py', *map(str, args)], self.root, self.env, expected=expected)
        self.commands.append({'command': ['python', 'hub.py', *[str(arg).replace(str(self.base), '<fixture>') for arg in args]],
                              'exit_code': result.returncode})
        return result

    def rpc(self, requests, server=None):
        argv = [server['command'], *server['args']] if server else [sys.executable, 'mcp_endpoint.py']
        require(requests and requests[0] == self.initialize(), 'initialize must be first')
        try:
            return asyncio.run(self._rpc(argv, requests))
        except (OSError, asyncio.TimeoutError):
            raise ProofFailure('RPC unavailable or timed out') from None
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise ProofFailure('invalid RPC response') from None

    async def _rpc(self, argv, requests):
        process = await asyncio.create_subprocess_exec(
            *argv, cwd=self.root, env=self.env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        async def exchange():
            results = []
            for index, request in enumerate(requests):
                require(index == 0 or request.get('method') != 'initialize', 'duplicate initialize')
                if index == 1:
                    require(request == {'method': 'notifications/initialized'}, 'initialized notification required')
                process.stdin.write((json.dumps({'jsonrpc': '2.0', **request}) + '\n').encode('utf-8'))
                await process.stdin.drain()
                if 'id' not in request:
                    continue
                response = json.loads(await process.stdout.readline())
                require(isinstance(response, dict) and type(response.get('id')) is type(request['id']) and
                        response.get('id') == request['id'] and
                        response.get('jsonrpc') == '2.0' and 'result' in response and
                        'error' not in response, 'invalid RPC response')
                result = response['result']
                if index == 0:
                    require(isinstance(result, dict) and
                            result.get('protocolVersion') == self.initialize()['params']['protocolVersion'],
                            'unsupported RPC protocol version')
                    identity, capabilities = result.get('serverInfo'), result.get('capabilities')
                    require(isinstance(identity, dict) and identity.get('name') == 'agentlocker' and
                            isinstance(identity.get('version'), str) and bool(identity['version']) and
                            isinstance(capabilities, dict) and isinstance(capabilities.get('tools'), dict),
                            'invalid RPC server identity or capabilities')
                results.append(result)
            process.stdin.close()
            require(not await process.stdout.readline(), 'unexpected RPC output')
            require(await process.wait() == 0, 'unexpected subprocess exit')
            self.commands.append({'command': ['python', 'mcp_endpoint.py'], 'exit_code': process.returncode})
            return results

        try:
            return await asyncio.wait_for(exchange(), timeout=RPC_TIMEOUT)
        finally:
            process.stdin.close()
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.communicate(), timeout=2)
                except asyncio.TimeoutError:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await asyncio.wait_for(process.communicate(), timeout=2)

    def initialize(self):
        return {'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2024-11-05', 'capabilities': {},
            'clientInfo': {'name': 'fixture-client', 'version': '1.0.0'}}}

    def catalog(self, present=True, guide='Fixture guide version one.\n'):
        hello = [self.initialize(), {'method': 'notifications/initialized'},
                 {'id': 2, 'method': 'tools/list'}]
        discovered = self.rpc(hello)
        require(discovered[0]['serverInfo']['name'] == 'agentlocker', 'initialize server identity')
        tools = {tool['name']: tool for tool in discovered[1]['tools']}
        names = ['hub_list_' + kind for kind in KINDS]
        require(set(names + ['hub_get_item']) <= tools.keys(), 'discovered catalog tools')
        calls = [(name, {}) for name in names]
        if present:
            calls += [('hub_get_item', {'kind': kind, 'name': name}) for kind, name in
                      (('skill', 'catalog-sample'), ('routine', 'catalog-sample-routine'))]
        results = self.rpc(hello + [
            {'id': index + 3, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}}
            for index, (name, arguments) in enumerate(calls)])[2:]
        require(all(not result.get('isError') for result in results), 'catalog tool errors')
        values = [json.loads(result['content'][0]['text']) for result in results]
        require(any(item['name'] == 'catalog-sample' for item in values[0]) == present, 'skill listing presence')
        require(any(item['id'] == 'catalog-sample-routine' for item in values[3]) == present, 'routine listing presence')
        if present:
            skill, routine = values[6:]
            require(not skill['omitted'] and not skill['truncated'], 'complete skill retrieval')
            require(any(path.endswith('/SKILL.md') for path in skill['files']), 'skill entry text')
            require(any(path.endswith('/references/guide.md') and text == guide
                        for path, text in skill['files'].items()), 'supporting skill text')
            require(any(path.endswith('/prompt.md') and text == 'Read the fixture guide without changes.\n'
                        for path, text in routine['files'].items()), 'routine supporting prompt')
            definitions = [json.loads(text) for path, text in routine['files'].items() if path.endswith('/routine.json')]
            require(len(definitions) == 1 and definitions[0]['enabled'] is False, 'disabled routine')

    def import_items(self):
        self.cli('import-skill', self.skill, '--owner', 'fixture')
        self.cli('import-routine', self.routine, '--owner', 'fixture')
        self.cli('import-rule', self.rule, '--owner', 'fixture')

    def configured_endpoint(self, entry):
        stage = 'fixture-only configured endpoint'
        try:
            args = entry['args']
            require(isinstance(args, list) and len(args) == 1, stage)
            endpoint, command = Path(args[0]), Path(entry['command'])
            root = self.root.resolve()
            expected = root / 'mcp_endpoint.py'
            require(root.is_relative_to(self.base.resolve()) and
                    endpoint.is_absolute() and endpoint.is_file() and
                    endpoint.resolve().is_relative_to(root) and expected.resolve().is_relative_to(root) and
                    endpoint.samefile(expected) and command.is_absolute(), stage)
            if not command.samefile(sys.executable):
                # macOS launchers can report a different executable in the child.
                # Query our trusted runtime, never the unvalidated configured command.
                child = run([sys.executable, '-c', 'import sys; print(sys.executable)'],
                            self.root, self.env).stdout.strip()
                require(Path(child).is_absolute() and command.samefile(child), stage)
        except ProofFailure:
            raise ProofFailure(stage) from None
        except (OSError, ValueError, TypeError, KeyError):
            raise ProofFailure(stage) from None

    def prove(self):
        require(not (self.root / '.hub-store').exists(), 'clean install starts without local state')
        shipped = (self.root / 'manifest.json').read_bytes()
        original_guide = self.guide.read_bytes()
        routine_prompt = (self.routine.parent / 'prompt.md').read_bytes()
        self.cli('list')
        self.catalog(present=False)
        self.import_items()
        self.cli('list')
        self.catalog()
        connect = ('connect', '--agent', 'claude', 'codex')
        self.cli(*connect)
        require(all(path.read_bytes() == data for path, data in self.original.items()), 'print default preserves config bytes')
        require(not list(self.home.rglob('*.bak')), 'print default creates no backups')
        self.cli(*connect, '--write', expected=1)
        require(all(path.read_bytes() == data for path, data in self.original.items()), 'unconfirmed writes refused')
        self.cli(*connect, '--write', '--yes')
        configured = {path: path.read_bytes() for path in self.original}
        claude = json.loads(configured[self.claude])
        require(claude['theme'] == 'fixture-sentinel' and
                claude['mcpServers']['unrelated'] == {'command': 'fixture-only'}, 'Claude unrelated settings preserved')
        server = claude['mcpServers']['agentlocker']
        codex = tomllib.loads(configured[self.codex].decode('utf-8'))
        for entry in (server, codex['mcp_servers']['agentlocker']):
            self.configured_endpoint(entry)
            require(self.rpc([self.initialize()], server=entry)[0]['serverInfo']['name'] == 'agentlocker',
                    'configured endpoint initialize')
        require(self.original[self.codex] in configured[self.codex] and
                b'[mcp_servers.agentlocker]' in configured[self.codex], 'Codex unrelated bytes preserved')
        for path, original in self.original.items():
            backups = list(path.parent.glob(path.name + '.*.bak'))
            require(len(backups) == 1 and backups[0].read_bytes() == original, 'exact config backup')
        self.cli(*connect, '--write', '--yes')
        require(all(path.read_bytes() == data for path, data in configured.items()) and
                len(list(self.home.rglob('*.bak'))) == 2, 'idempotent connection')
        unrelated_rule = self.root / '.hub-store/catalog/rules/unrelated.md'
        unrelated_rule_bytes = unrelated_rule.read_bytes()
        self.guide.write_text('Fixture guide version two.\n', encoding='utf-8')
        updated_guide = self.guide.read_bytes()
        self.cli('import-skill', self.skill, '--owner', 'fixture')
        self.catalog(guide='Fixture guide version two.\n')
        history = self.root.resolve() / '.hub-store/history/skills/catalog-sample'
        require(any(path.read_bytes() == original_guide
                    for path in history.rglob('guide.md')), 'upgrade retained previous support file')
        for kind, name in (('skill', 'catalog-sample'), ('routine', 'catalog-sample-routine')):
            self.cli('remove', kind, name, '--owner', 'fixture', '--yes')
        self.catalog(present=False)
        require(any(path.read_bytes() == updated_guide
                    for path in history.rglob('guide.md')), 'removal retains upgraded support file')
        routine_history = self.root.resolve() / '.hub-store/history/routines/catalog-sample-routine'
        require(any(path.read_bytes() == routine_prompt
                    for path in routine_history.rglob('prompt.md')), 'removal retains routine prompt')
        require(not (self.root / '.hub-store/catalog/skills/catalog-sample').exists(), 'skill removed')
        require(not (self.root / '.hub-store/catalog/routines/catalog-sample-routine/prompt.md').exists(), 'routine prompt removed')
        require(unrelated_rule.read_bytes() == unrelated_rule_bytes and
                self.unrelated.read_bytes() == b'fixture-sentinel\x00\xff', 'unrelated content preserved')
        require(all(path.read_bytes() == data for path, data in configured.items()), 'removal preserves client settings')
        require((self.root / 'manifest.json').read_bytes() == shipped, 'shipped manifest preserved')
        return {'status': 'passed', 'evidence_class': 'offline-cli-stdio-fixture',
                'product_agent_proof': False, 'real_provider_proof': False,
                'gateway_proof': False, 'remote_proof': False, 'scheduler_proof': False,
                'source': self.source, 'python': '.'.join(map(str, sys.version_info[:3])),
                'platform': sys.platform, 'commands': self.commands,
                'checks': ['clean install', 'six discovered listings and supporting content',
                           'default print and confirmed fixture config merge with backups',
                           'upgrade retains history', 'owned removal preserves unrelated bytes'],
                'limitations': ['current-source import upgrade, not historical release migration',
                                'no native client, provider, gateway, remote or scheduler execution']}


def main():
    try:
        with Fixture() as fixture:
            summary = fixture.prove()
    except Exception:
        print(json.dumps({'status': 'failed', 'evidence_class': 'offline-cli-stdio-fixture',
                          'product_agent_proof': False, 'real_provider_proof': False,
                          'gateway_proof': False, 'remote_proof': False, 'scheduler_proof': False,
                          'diagnostic': ('Python 3.11+ with tomllib required; proof not run' if tomllib is None else
                                         'local catalog proof failed; raw diagnostics suppressed')}))
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
