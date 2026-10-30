import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'skills/verify-loop/verify_loop.py'
PASSING = 'import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n'
FAILING = 'import unittest\n\nclass T(unittest.TestCase):\n    def test_bad(self):\n        self.assertTrue(False)\n'
MODEL_NAMES = re.compile(r'\b(Sol|Terra|Luna|Astra|Sonnet|Haiku|Opus|Fable|GPT)\b|gpt-\d|claude-', re.I)


def git(cwd, *args):
    return subprocess.run(['git', '-C', str(cwd), '-c', 'user.name=t', '-c', 'user.email=t', *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def commit(repo, files, message='change'):
    for name, text in files.items():
        path = Path(repo, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', message)
    return git(repo, 'rev-parse', 'HEAD')


def recipe(shell, python):
    files = {'.fleet/verify.sh': shell}
    if os.name == 'nt':
        files['.fleet/recipe.py'] = python + '\n'
        interpreter = sys.executable.replace("'", "''")
        files['.fleet/verify.ps1'] = f"& '{interpreter}' .fleet/recipe.py\nexit $LASTEXITCODE\n"
    return files


class Recorder(BaseHTTPRequestHandler):
    calls = []
    existing = []
    foreign = []
    rules = False

    def log_message(self, *args):
        pass

    def reply(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        Recorder.calls.append(('POST', self.path, body, self.headers['Authorization']))
        self.reply({'id': 100, 'app': {'id': 7}})

    def do_PATCH(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        Recorder.calls.append(('PATCH', self.path, body, self.headers['Authorization']))
        self.reply({'id': 100})

    def do_GET(self):
        Recorder.calls.append(('GET', self.path, None, self.headers['Authorization']))
        if '/rules/branches/' in self.path and Recorder.rules:
            self.reply([{'type': 'required_status_checks', 'parameters': {'required_status_checks': [
                {'context': 'Required CI', 'integration_id': 5495865 if Recorder.rules == 'ours' else 15368}]}}])
        elif '/rules/branches/' in self.path:
            self.reply([])
        elif '/protection/required_status_checks' in self.path and Recorder.rules == 'classic':
            self.reply({'checks': [{'context': 'Classic CI', 'app_id': 5495865}]})
        elif '/protection/required_status_checks' in self.path:
            self.reply({'checks': []})
        else:
            self.reply({'check_runs': [{'id': i, 'app': {'id': 7}} for i in Recorder.existing]
                                       + [{'id': i, 'app': {'id': 9}} for i in Recorder.foreign]})


class VerifyLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.origin = base / 'origin.git'
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(self.origin)], check=True)
        self.repo = base / 'repo'
        subprocess.run(['git', 'clone', '-q', str(self.origin), str(self.repo)], check=True, capture_output=True)
        git(self.repo, 'checkout', '-q', '-b', 'main')
        commit(self.repo, {'tests/test_a.py': PASSING, 'lib.txt': 'one\n'}, 'init')
        git(self.repo, 'push', '-q', 'origin', 'main')
        git(self.repo, 'remote', 'set-head', 'origin', 'main')
        git(self.repo, 'checkout', '-q', '-b', 'feature')

    def run_loop(self, *args, env=None):
        launcher = ROOT / 'tests/fixtures/verify-loop/fake_gh.py'
        return subprocess.run([sys.executable, '-B', str(launcher), str(RUNNER),
                               '--repo', str(self.repo), *args],
                              capture_output=True, text=True,
                              env={**os.environ, **(env or {})}, check=False)

    def logs(self):
        return list((self.repo / '.git/verify-loop').glob('*.log'))

    def test_pass_reports_real_test_count_and_writes_log_in_git_dir(self):
        result = self.run_loop()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS: tests=1', result.stdout)
        self.assertEqual(len(self.logs()), 1)
        self.assertEqual(git(self.repo, 'status', '--short'), '')

    def test_failing_test_fails_and_prints_tail(self):
        commit(self.repo, {'tests/test_a.py': FAILING})
        result = self.run_loop()
        self.assertEqual(result.returncode, 1)
        self.assertIn('FAIL:', result.stdout)
        self.assertIn('test_bad', result.stdout)

    def test_zero_tests_is_a_failure_even_on_exit_zero(self):
        result = self.run_loop('--cmd', shlex.join([sys.executable, '-c', 'pass']))
        self.assertEqual(result.returncode, 1)
        self.assertIn('0 tests ran', result.stdout)

    def test_recipe_marker_sets_count(self):
        commit(self.repo, recipe('#!/usr/bin/env bash\necho FLEET_TESTS=7\n', "print('FLEET_TESTS=7')"))
        result = self.run_loop()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('tests=7', result.stdout)
        self.assertIn('.fleet/verify.sh', result.stdout.split('verification changed:')[1])

    def test_marker_zero_fails(self):
        commit(self.repo, recipe('#!/usr/bin/env bash\necho FLEET_TESTS=0\n', "print('FLEET_TESTS=0')"))
        self.assertEqual(self.run_loop().returncode, 1)

    def test_marker_must_be_the_last_line(self):
        commit(self.repo, recipe('#!/usr/bin/env bash\necho FLEET_TESTS=7\necho Ran 0 tests\n', "print('FLEET_TESTS=7'); print('Ran 0 tests')"))
        result = self.run_loop()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('0 tests ran', result.stdout)

    def test_explicit_pr_that_cannot_be_read_fails_without_post(self):
        result = self.run_loop('--pr', '3', env={'FAKE_GH': 'fail'})
        self.assertEqual(result.returncode, 1)
        self.assertIn('could not read the pull request', result.stdout)

    def test_pr_whose_head_is_not_the_checked_out_head_fails_without_post(self):
        pr_head = 'f' * 40
        result = self.run_loop('--pr', '3', env={'FAKE_GH_HEAD': pr_head})
        head = git(self.repo, 'rev-parse', 'HEAD')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f'FAIL: worktree HEAD {head[:12]} is not the pull request head {pr_head[:12]}; '
                      'check out the PR head (e.g. its worktree) or pass --repo <worktree>', result.stdout)
        self.assertNotIn('PASS', result.stdout)
        self.assertEqual(self.logs(), [])

    def test_pr_with_matching_head_proceeds_and_reports_both_heads(self):
        result = self.run_loop('--pr', '3')
        head = git(self.repo, 'rev-parse', 'HEAD')[:12]
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'PASS: tests=1 head={head} pr_head={head}', result.stdout)

    def test_pr_with_uncommitted_changes_is_refused(self):
        (self.repo / 'scratch.txt').write_text('x')
        result = self.run_loop('--pr', '3')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('FAIL: worktree has uncommitted changes', result.stdout)
        self.assertNotIn('PASS', result.stdout)

    def test_checks_marker_passes_for_build_only_repos(self):
        commit(self.repo, recipe('#!/usr/bin/env bash\necho FLEET_CHECKS=2\n', "print('FLEET_CHECKS=2')"))
        result = self.run_loop()
        self.assertEqual(result.returncode, 0)
        self.assertIn('checks=2', result.stdout)

    def test_base_changes_are_merged_before_the_run(self):
        other = Path(self.tmp.name, 'other')
        subprocess.run(['git', 'clone', '-q', str(self.origin), str(other)], check=True, capture_output=True)
        commit(other, {'tests/test_b.py': FAILING}, 'base breaks')
        git(other, 'push', '-q', 'origin', 'HEAD:main')
        result = self.run_loop()
        self.assertEqual(result.returncode, 1)
        self.assertIn('test_bad', result.stdout)

    def test_head_is_merged_into_the_base_like_github_does(self):
        commit(self.repo, {'.gitattributes': '*.lst merge=union\n', 'f.lst': 'x\n'}, 'common')
        git(self.repo, 'push', '-q', 'origin', 'HEAD:main')
        other = Path(self.tmp.name, 'other')
        subprocess.run(['git', 'clone', '-q', str(self.origin), str(other)], check=True, capture_output=True)
        commit(other, {'f.lst': 'x\nbase\n'}, 'base adds')
        git(other, 'push', '-q', 'origin', 'HEAD:main')
        commit(self.repo, {'f.lst': 'x\nfeature\n',
                           **recipe('#!/usr/bin/env bash\n[ "$(sed -n 2p f.lst)" = base ] || exit 1\necho FLEET_TESTS=1\n', "from pathlib import Path; assert Path('f.lst').read_text().splitlines()[1] == 'base'; print('FLEET_TESTS=1')")}, 'feature adds')
        result = self.run_loop()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_merge_conflict_with_base_fails(self):
        commit(self.repo, {'lib.txt': 'feature\n'})
        other = Path(self.tmp.name, 'other')
        subprocess.run(['git', 'clone', '-q', str(self.origin), str(other)], check=True, capture_output=True)
        commit(other, {'lib.txt': 'main\n'}, 'base edit')
        git(other, 'push', '-q', 'origin', 'HEAD:main')
        result = self.run_loop()
        self.assertEqual(result.returncode, 1)
        self.assertIn('rebase on main', result.stdout)

    def test_uncommitted_changes_are_tested_without_post_and_refused_with_post(self):
        Path(self.repo, 'tests/test_a.py').write_text(FAILING)
        self.assertEqual(self.run_loop().returncode, 1)
        refused = self.run_loop('--post')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('uncommitted', refused.stdout)

    def test_secrets_are_not_visible_to_the_verified_code(self):
        commit(self.repo, recipe('#!/usr/bin/env bash\necho "seen=${MY_SECRET_TOKEN:-none} gh=${GH_TOKEN:-none}"\necho FLEET_TESTS=1\n', "import os; print('seen=' + os.environ.get('MY_SECRET_TOKEN', 'none') + ' gh=' + os.environ.get('GH_TOKEN', 'none')); print('FLEET_TESTS=1')"))
        result = self.run_loop(env={'MY_SECRET_TOKEN': 'leak-me', 'GH_TOKEN': 'leak-me-too'})
        self.assertEqual(result.returncode, 0, result.stdout)
        text = self.logs()[0].read_text()
        self.assertIn('seen=none gh=none', text)
        self.assertNotIn('leak-me', text + result.stdout)

    def test_origin_url_credentials_are_stripped_in_the_snapshot(self):
        git(self.repo, 'remote', 'set-url', '--push', 'origin', '@'.join(['https://user:pw', 'github.com/o/r.git']))
        commit(self.repo, recipe('#!/usr/bin/env bash\ngit remote get-url origin\necho FLEET_TESTS=1\n', "import subprocess; subprocess.run(['git', 'remote', 'get-url', 'origin'], check=True); print('FLEET_TESTS=1')"))
        result = self.run_loop()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('pw@', self.logs()[0].read_text())

    def post(self, *extra, existing=(), foreign=(), env=None):
        Recorder.calls, Recorder.existing, Recorder.foreign, Recorder.rules = [], list(existing), list(foreign), False
        server = HTTPServer(('localhost', 0), Recorder)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        token = f'{shlex.quote(sys.executable)} -c "print(\'fake-app-token\')"'
        return self.run_loop('--post', '--slug', 'o/r', '--base', 'main', '--api-url',
                             f'http://localhost:{server.server_port}', '--token-cmd', token, *extra, env=env)

    def test_post_success_uses_one_check_run_with_exact_sha_and_base_sha(self):
        head = commit(self.repo, {'extra.txt': 'x\n'})
        base_sha = git(self.repo, 'rev-parse', 'origin/main')
        result = self.post()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        post, patch = [c for c in Recorder.calls if c[0] in ('POST', 'PATCH')]
        self.assertEqual(post[1], '/repos/o/r/check-runs')
        self.assertEqual((post[2]['name'], post[2]['head_sha'], post[2]['status']), ('verify', head, 'in_progress'))
        self.assertEqual(patch[1], '/repos/o/r/check-runs/100')
        self.assertEqual((patch[2]['conclusion'], patch[2]['external_id']), ('success', base_sha))
        self.assertIn('tests: 1', patch[2]['output']['summary'])
        self.assertEqual(post[3], 'Bearer ' + 'fake-app-token')
        self.assertNotIn('fake-app-token', result.stdout + result.stderr + self.logs()[0].read_text())

    def test_post_failure_after_an_earlier_pass_completes_as_failure(self):
        commit(self.repo, {'tests/test_a.py': FAILING})
        result = self.post()
        self.assertEqual(result.returncode, 1)
        patch = [c for c in Recorder.calls if c[0] == 'PATCH'][0]
        self.assertEqual(patch[2]['conclusion'], 'failure')

    def test_post_zero_tests_completes_as_failure(self):
        result = self.post('--cmd', shlex.join([sys.executable, '-c', 'pass']))
        self.assertEqual(result.returncode, 1)
        self.assertEqual([c for c in Recorder.calls if c[0] == 'PATCH'][0][2]['conclusion'], 'failure')

    def test_older_run_finishing_after_a_newer_one_is_neutral(self):
        result = self.post(existing=[101])
        self.assertEqual(result.returncode, 1)
        self.assertIn('SUPERSEDED', result.stdout)
        patch = [c for c in Recorder.calls if c[0] == 'PATCH'][0]
        self.assertEqual(patch[2]['conclusion'], 'neutral')
        self.assertIn('superseded by 101', patch[2]['output']['summary'])

    def test_newer_run_from_another_app_does_not_supersede(self):
        result = self.post(foreign=[101])
        self.assertEqual(result.returncode, 0, result.stdout)
        patch = [c for c in Recorder.calls if c[0] == 'PATCH'][0]
        self.assertEqual(patch[2]['conclusion'], 'success')

    def test_post_uses_the_required_context_of_the_app(self):
        result = self.run_loop_with_context()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([c for c in Recorder.calls if c[0] == 'POST'][0][2]['name'], 'Required CI')

    def run_loop_with_context(self, rules='ours', base='main'):
        Recorder.calls, Recorder.existing, Recorder.foreign, Recorder.rules = [], [], [], rules
        server = HTTPServer(('localhost', 0), Recorder)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        token = f'{shlex.quote(sys.executable)} -c "print(\'t\')"'
        return self.run_loop('--post', '--slug', 'o/r', '--base', base, '--app-id', '5495865', '--api-url',
                             f'http://localhost:{server.server_port}', '--token-cmd', token, env={'FAKE_GH_BASE': base})

    def test_required_context_of_another_integration_is_not_used(self):
        result = self.run_loop_with_context('foreign')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([c for c in Recorder.calls if c[0] == 'POST'][0][2]['name'], 'verify')

    def test_classic_branch_protection_context_of_the_app_is_used(self):
        result = self.run_loop_with_context('classic')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([c for c in Recorder.calls if c[0] == 'POST'][0][2]['name'], 'Classic CI')

    def test_post_fails_closed_when_the_pull_request_cannot_be_read(self):
        result = self.post(env={'FAKE_GH': 'fail'})
        self.assertEqual(result.returncode, 1)
        self.assertIn('could not read the pull request', result.stdout)
        self.assertEqual([c for c in Recorder.calls if c[0] in ('POST', 'PATCH')], [])

    def test_post_refuses_a_base_that_is_not_the_pull_request_base(self):
        git(self.repo, 'branch', 'other', 'main')
        result = self.post('--base', 'other')
        self.assertEqual(result.returncode, 1)
        self.assertIn('is not the pull request base', result.stdout)

    def test_unreachable_origin_fails_instead_of_using_a_stale_base(self):
        git(self.repo, 'remote', 'set-url', 'origin', str(Path(self.tmp.name, 'missing.git')))
        result = self.run_loop()
        self.assertEqual(result.returncode, 1)
        self.assertIn('could not fetch origin/main', result.stdout)

    def test_tracked_symlinks_are_kept_in_the_dirty_snapshot(self):
        commit(self.repo, recipe('#!/usr/bin/env bash\ntest -L link.txt || exit 1\necho FLEET_TESTS=1\n', "from pathlib import Path; assert Path('link.txt').is_symlink(); print('FLEET_TESTS=1')"))
        os.symlink('lib.txt', self.repo / 'link.txt')
        git(self.repo, 'add', 'link.txt')
        git(self.repo, 'commit', '-q', '-m', 'link')
        (self.repo / 'scratch.txt').write_text('x')
        result = self.run_loop()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_base_is_fetched_from_the_remote_of_the_pull_request_base(self):
        upstream = Path(self.tmp.name, 'upstream.git')
        subprocess.run(['git', 'clone', '-q', '--bare', str(self.origin), str(upstream)], check=True, capture_output=True)
        other = Path(self.tmp.name, 'other')
        subprocess.run(['git', 'clone', '-q', str(upstream), str(other)], check=True, capture_output=True)
        commit(other, {'tests/test_b.py': FAILING}, 'upstream breaks')
        git(other, 'push', '-q', 'origin', 'HEAD:main')
        url = 'https://github.com/up/stream.git'
        git(self.repo, 'remote', 'add', 'upstream', url)
        git(self.repo, 'config', f'url.{upstream}.insteadOf', url)
        result = self.run_loop('--pr', '3', env={'FAKE_GH_URL': 'https://github.com/up/stream/pull/3'})
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('test_bad', result.stdout)

    def test_pull_request_base_without_a_matching_remote_is_refused(self):
        result = self.run_loop('--pr', '3', env={'FAKE_GH_URL': 'https://github.com/up/stream/pull/3'})
        self.assertEqual(result.returncode, 1)
        self.assertIn('no remote points at up/stream', result.stdout)

    def test_base_is_fetched_when_the_remote_refspec_excludes_it(self):
        git(self.repo, 'config', 'remote.origin.fetch', '+refs/heads/feature:refs/remotes/origin/feature')
        other = Path(self.tmp.name, 'other')
        subprocess.run(['git', 'clone', '-q', str(self.origin), str(other)], check=True, capture_output=True)
        commit(other, {'tests/test_b.py': FAILING}, 'base breaks')
        git(other, 'push', '-q', 'origin', 'HEAD:main')
        result = self.run_loop()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('test_bad', result.stdout)

    def test_submodules_are_initialized_and_dirty_runs_survive_them(self):
        sub = Path(self.tmp.name, 'sub')
        subprocess.run(['git', 'init', '-q', str(sub)], check=True)
        commit(sub, {'lib.txt': 'sub\n'}, 'sub init')
        allow = ['-c', 'protocol.file.allow=always']
        git(self.repo, *allow, 'submodule', 'add', '-q', str(sub), 'vendor/sub')
        commit(self.repo, recipe('#!/usr/bin/env bash\ntest -f vendor/sub/lib.txt || exit 1\necho FLEET_TESTS=1\n', "from pathlib import Path; assert Path('vendor/sub/lib.txt').is_file(); print('FLEET_TESTS=1')"), 'use submodule')
        Path(self.repo, 'scratch.txt').write_text('dirty\n')
        allowed = {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'protocol.file.allow', 'GIT_CONFIG_VALUE_0': 'always'}
        result = self.run_loop(env=allowed)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS: tests=1', result.stdout)

    def test_relative_submodule_urls_resolve_against_the_real_origin(self):
        remotes = Path(self.tmp.name, 'remotes')
        remotes.mkdir()
        shutil.move(str(self.origin), str(remotes / 'origin.git'))
        git(self.repo, 'remote', 'set-url', 'origin', str(remotes / 'origin.git'))
        seed = Path(self.tmp.name, 'seed')
        subprocess.run(['git', 'init', '-q', str(seed)], check=True)
        commit(seed, {'lib.txt': 'sub\n'}, 'sub init')
        subprocess.run(['git', 'clone', '-q', '--bare', str(seed), str(remotes / 'sub.git')], check=True, capture_output=True)
        git(self.repo, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', '../sub.git', 'vendor/sub')
        commit(self.repo, recipe('#!/usr/bin/env bash\ntest -f vendor/sub/lib.txt || exit 1\necho FLEET_TESTS=1\n', "from pathlib import Path; assert Path('vendor/sub/lib.txt').is_file(); print('FLEET_TESTS=1')"), 'use submodule')
        allowed = {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'protocol.file.allow', 'GIT_CONFIG_VALUE_0': 'always'}
        result = self.run_loop(env=allowed)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_stderr_before_the_marker_keeps_the_marker_last(self):
        commit(self.repo, recipe('#!/usr/bin/env bash\necho warning >&2\necho FLEET_TESTS=1\n', "import sys; print('warning', file=sys.stderr); print('FLEET_TESTS=1')"))
        result = self.run_loop()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_pytest_style_functions_select_pytest_without_configuration(self):
        commit(self.repo, {'tests/test_p.py': 'def test_function():\n    assert False\n'})
        self.run_loop()
        self.assertIn('-m pytest', self.logs()[0].read_text())

    def test_skipped_tests_are_not_counted_as_run(self):
        skipped = 'import unittest\n\nclass T(unittest.TestCase):\n    @unittest.skip("x")\n    def test_s(self):\n        pass\n'
        git(self.repo, 'rm', '-q', 'tests/test_a.py')
        commit(self.repo, {'tests/test_s.py': skipped})
        result = self.run_loop()
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_nested_test_directory_without_init_is_run(self):
        commit(self.repo, {'tests/nested/test_b.py': FAILING})
        result = self.run_loop()
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_root_and_tests_counts_are_added(self):
        commit(self.repo, {'test_root.py': PASSING.replace('test_ok', 'test_root_ok')})
        result = self.run_loop()
        self.assertIn('PASS: tests=2', result.stdout)

    def test_skipped_node_tests_are_not_counted(self):
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location('verify_loop_under_test', RUNNER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.count_tests('# tests 1\n# pass 0\n# skipped 1\n')[0], 0)

    def test_staged_deletion_is_not_verified_from_head(self):
        commit(self.repo, {'tests/test_gone.py': FAILING})
        git(self.repo, 'rm', '-q', 'tests/test_gone.py')
        result = self.run_loop()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_pytest_style_classes_select_pytest(self):
        commit(self.repo, {'tests/test_c.py': 'class TestFeature:\n    def test_it(self):\n        assert False\n'})
        result = self.run_loop()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('-m pytest', self.logs()[0].read_text())

    def test_pytest_classes_with_a_custom_base_select_pytest(self):
        commit(self.repo, {'tests/test_d.py': 'class Base:\n    pass\n\nclass TestFeature(Base):\n    def test_it(self):\n        assert False\n'})
        result = self.run_loop()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('-m pytest', self.logs()[0].read_text())

    def test_tests_package_under_a_root_test_runs_once(self):
        commit(self.repo, {'tests/__init__.py': '', 'test_root.py': PASSING.replace('test_ok', 'test_root_ok')})
        result = self.run_loop()
        self.assertIn('PASS: tests=2', result.stdout)

    def test_sibling_test_directories_are_all_run(self):
        git(self.repo, 'rm', '-q', 'tests/test_a.py')
        commit(self.repo, {'tests/unit/test_u.py': PASSING, 'tests/integration/test_i.py': FAILING})
        result = self.run_loop()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('-s tests/integration', self.logs()[0].read_text())

    def test_tests_inside_package_directories_are_run(self):
        commit(self.repo, {'src/pkg/tests/test_feature.py': FAILING})
        result = self.run_loop()
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_vendored_dependencies_are_not_discovered(self):
        commit(self.repo, {'node_modules/dep/test_dep.py': FAILING})
        result = self.run_loop()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_nested_conftest_selects_pytest(self):
        commit(self.repo, {'tests/sub/conftest.py': '', 'tests/sub/test_n.py': PASSING})
        self.run_loop()
        self.assertIn('-m pytest', self.logs()[0].read_text())

    def test_base_branch_with_a_slash_is_encoded_in_required_check_lookups(self):
        git(self.repo, 'push', '-q', 'origin', 'main:refs/heads/release/1.0')
        result = self.run_loop_with_context('classic', base='release/1.0')
        self.assertTrue(any('/branches/release%2F1.0/protection' in c[1] for c in Recorder.calls), Recorder.calls)

    def test_root_level_tests_run_next_to_the_tests_directory(self):
        commit(self.repo, {'test_root.py': 'import unittest\n\nclass R(unittest.TestCase):\n    def test_fails(self):\n        self.fail()\n'})
        result = self.run_loop()
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_relative_submodules_resolve_against_the_pull_request_base_remote(self):
        remotes = Path(self.tmp.name, 'remotes')
        remotes.mkdir()
        upstream = remotes / 'stream.git'
        subprocess.run(['git', 'clone', '-q', '--bare', str(self.origin), str(upstream)], check=True, capture_output=True)
        seed = Path(self.tmp.name, 'seed')
        subprocess.run(['git', 'init', '-q', str(seed)], check=True)
        sub_sha = commit(seed, {'lib.txt': 'sub\n'}, 'sub init')
        subprocess.run(['git', 'clone', '-q', '--bare', str(seed), str(remotes / 'sub.git')], check=True, capture_output=True)
        url = 'https://github.com/up/stream.git'
        git(self.repo, 'remote', 'add', 'upstream', url)
        git(self.repo, 'config', f'url.{upstream}.insteadOf', url)
        Path(self.repo, '.gitmodules').write_text('[submodule "vendor/sub"]\n\tpath = vendor/sub\n\turl = ../sub.git\n')
        git(self.repo, 'update-index', '--add', '--cacheinfo', f'160000,{sub_sha},vendor/sub')
        Path(self.repo, 'vendor/sub').mkdir(parents=True)
        Path(self.repo, '.fleet').mkdir()
        for name, text in recipe('#!/usr/bin/env bash\ntest -f vendor/sub/lib.txt || exit 1\necho FLEET_TESTS=1\n', "from pathlib import Path; assert Path('vendor/sub/lib.txt').is_file(); print('FLEET_TESTS=1')").items():
            Path(self.repo, name).write_text(text)
        git(self.repo, 'add', '.gitmodules', '.fleet')
        git(self.repo, 'commit', '-q', '-m', 'use submodule')
        env = {'FAKE_GH_URL': 'https://github.com/up/stream/pull/3', 'GIT_CONFIG_COUNT': '2',
               'GIT_CONFIG_KEY_0': 'protocol.file.allow', 'GIT_CONFIG_VALUE_0': 'always',
               'GIT_CONFIG_KEY_1': f'url.{remotes}/sub.git.insteadOf', 'GIT_CONFIG_VALUE_1': 'https://github.com/up/sub.git'}
        result = self.run_loop('--pr', '3', env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pytest_configured_in_pyproject_runs_pytest_not_unittest(self):
        commit(self.repo, {'pyproject.toml': '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'})
        self.run_loop()
        self.assertIn('-m pytest', self.logs()[0].read_text())
        self.assertNotIn('unittest', self.logs()[0].read_text())

    def test_package_json_change_is_reported_as_verification_change(self):
        commit(self.repo, {'package.json': '{"scripts": {"test": "true"}}\n'})
        result = self.run_loop('--cmd', shlex.join([sys.executable, '-c', "print('FLEET_TESTS=1')"]))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('package.json', result.stdout.split('verification changed:')[1])

    def test_uncommitted_changes_inside_a_submodule_are_refused_locally(self):
        sub = Path(self.tmp.name, 'sub')
        subprocess.run(['git', 'init', '-q', str(sub)], check=True)
        commit(sub, {'lib.txt': 'sub\n'}, 'sub init')
        git(self.repo, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', str(sub), 'vendor/sub')
        git(self.repo, 'commit', '-q', '-m', 'add submodule')
        Path(self.repo, 'vendor/sub/lib.txt').write_text('changed\n')
        result = self.run_loop()
        self.assertEqual(result.returncode, 1)
        self.assertIn('submodule vendor/sub has uncommitted', result.stdout)

    def test_only_the_last_command_output_is_counted(self):
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location('verify_loop_under_test', RUNNER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as work, open(os.devnull, 'w') as log:
            code, output, texts = module.execute(
                work, [[sys.executable, '-c', "print('Ran 5 tests')"], [sys.executable, '-c', 'pass']], os.environ.copy(), log)
        self.assertEqual(code, 0)
        self.assertEqual(module.count_tests(output)[0], 5)
        self.assertEqual(module.count_run('node', texts)[0], 0)

    def test_pinned_base_ref_is_removed_after_the_run(self):
        self.assertEqual(self.run_loop().returncode, 0)
        self.assertEqual(git(self.repo, 'for-each-ref', 'refs/verify-loop'), '')

    def test_renaming_a_test_file_away_is_reported(self):
        commit(self.repo, {'tests/test_c.py': PASSING})
        git(self.repo, 'mv', 'tests/test_a.py', 'docs_a.txt')
        git(self.repo, 'commit', '-q', '-m', 'move test away')
        result = self.run_loop()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('tests/test_a.py', result.stdout.split('verification changed:')[1])

    def test_post_without_a_github_origin_is_refused(self):
        result = self.run_loop('--post', '--base', 'main')
        self.assertEqual(result.returncode, 1)
        self.assertIn('not a GitHub remote', result.stdout)

    def coverage(self, prs, runs=None, *extra, env=None, count=None):
        merged = [{'number': n, 'headRefOid': sha, 'mergedAt': f'2026-10-{n:02d}T00:00:00Z'} for n, sha in prs]
        return self.run_loop('--coverage', str(count or len(prs)), '--slug', 'o/r', '--token-cmd', 'definitely-not-a-command', *extra,
                             env={'FAKE_GH_PRS': json.dumps(merged), 'FAKE_GH_RUNS': json.dumps(runs or {}), **(env or {})})

    def test_coverage_all_posted_lists_conclusions_newest_first_and_exits_zero(self):
        runs = {'a' * 40: [{'id': 1, 'status': 'completed', 'conclusion': 'failure'}, {'id': 2, 'status': 'completed', 'conclusion': 'success'}],
                'b' * 40: [{'id': 3, 'status': 'in_progress', 'conclusion': None}]}
        result = self.coverage([(1, 'b' * 40), (2, 'a' * 40)], runs)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines(), [f'#2 {"a" * 40} posted success', f'#1 {"b" * 40} posted in_progress', 'posted 2 of 2'])

    def test_coverage_some_missing_exits_one(self):
        result = self.coverage([(1, 'b' * 40), (2, 'a' * 40)], {'a' * 40: [{'id': 2, 'status': 'completed', 'conclusion': 'success'}]})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines(), [f'#2 {"a" * 40} posted success', f'#1 {"b" * 40} missing', 'posted 1 of 2'])

    def test_coverage_picks_the_latest_merged_even_when_listed_last(self):
        result = self.coverage([(i, str(i) * 40) for i in (1, 2, 3, 4, 5)], count=2)
        self.assertEqual(result.stdout.splitlines(), [f'#5 {"5" * 40} missing', f'#4 {"4" * 40} missing', 'posted 0 of 2'])

    def test_coverage_error_exits_two(self):
        result = self.coverage([(1, 'a' * 40)], env={'FAKE_GH': 'fail'})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('ERROR:', result.stdout)

    def test_coverage_without_a_github_remote_exits_two(self):
        result = self.run_loop('--coverage', '3')
        self.assertEqual(result.returncode, 2)
        self.assertIn('--slug', result.stdout)

    def test_coverage_never_posts_or_mints_a_token(self):
        result = self.coverage([(1, 'a' * 40)], {}, '--api-url', 'http://localhost:9')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn('token', (result.stdout + result.stderr).lower())

    def test_coverage_rejects_a_non_positive_count(self):
        result = self.run_loop('--coverage', '0', '--slug', 'o/r')
        self.assertEqual(result.returncode, 2)


class VerifyLoopContentTests(unittest.TestCase):
    def test_new_content_names_no_models(self):
        paths = [ROOT / 'rules/verify-before-merge.md', ROOT / 'docs/verify-loop.md',
                 *sorted((ROOT / 'skills/verify-loop').rglob('*'))]
        checked = 0
        for path in paths:
            if path.is_file() and path.suffix in ('.md', '.py', '.json'):
                checked += 1
                self.assertIsNone(MODEL_NAMES.search(path.read_text(encoding='utf-8')), str(path))
        self.assertGreaterEqual(checked, 4)


if __name__ == '__main__':
    unittest.main()
