from pathlib import Path
import subprocess
import unittest

import connect

ROOT = Path(__file__).resolve().parents[1]


class Screens34Tests(unittest.TestCase):
    def test_dom_states_and_actions(self):
        result = subprocess.run(['node', 'tests/ui_screens34_smoke.cjs'], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_welcome_sign_in_only_when_a_server_needs_it(self):
        result = subprocess.run(['node', 'tests/ui_welcome_smoke.cjs'], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_welcome_shows_scanning_state_until_results(self):
        result = subprocess.run(['node', 'tests/ui_welcome_scanning_smoke.cjs'], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_no_ui_config_write_and_limited_red(self):
        for path in (ROOT / 'ui').iterdir():
            if path.suffix in ('.js', '.html'):
                self.assertNotIn('/api/connect/write', path.read_text(encoding='utf-8'))
        css = (ROOT / 'ui/connect.css').read_text(encoding='utf-8')
        self.assertEqual(css.count('var(--red)'), 1)
        self.assertIn('.connect-log .live{color:var(--red)}', css)
        self.assertNotIn('var(--red)', (ROOT / 'ui/signin.css').read_text(encoding='utf-8'))

    def test_four_tabs_return_snippets_without_detection(self):
        import tempfile
        import hub_ui
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'name': 'agentlocker', 'license': 'MIT', 'skills': [], 'rules': [], 'routines': [], 'mcp_servers': []}), encoding='utf-8')
            with hub_ui.SetupServer(root, scan_home=root / 'home') as server:
                result = server.api('/api/connect', {})
                self.assertEqual([item['agent'] for item in result['agents']], list(connect.AGENTS))
                self.assertTrue(all(item['display_path'].startswith('~/') and item['snippet'] for item in result['agents']))

    def test_gateway_counts_live_tool_owners_once(self):
        import tempfile
        import json
        from unittest.mock import patch
        import hub_ui
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            servers = [{'name': name, 'path': 'mcp/servers/' + name + '.json', 'owner': 'agentlocker', 'sha256': '0' * 64,
                        'provenance': {'source_id': 'src-' + '0' * 12, 'origin': 'fixture'}} for name in ('demo', 'demo-other')]
            (root / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'name': 'agentlocker', 'license': 'MIT', 'skills': [], 'rules': [], 'routines': [], 'mcp_servers': servers}), encoding='utf-8')
            result = {'backend': 'executor', 'status': 'running', 'tools': ['demo-other-add', 'demo-other-subtract']}
            with patch('hub_ui.shared_gateway_connection', return_value=result):
                status = hub_ui.gateway_connection(root)
            self.assertEqual((status['served'], status['total']), (1, 2))

    def test_gateway_names_each_down_server_and_why(self):
        import tempfile
        import json
        from unittest.mock import patch
        import hub_ui
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            servers = [{'name': name, 'path': 'mcp/servers/' + name + '.json', 'owner': 'agentlocker', 'sha256': '0' * 64,
                        'provenance': {'source_id': 'src-' + '0' * 12, 'origin': 'fixture'}} for name in ('down-one', 'down-two', 'up')]
            (root / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'name': 'agentlocker', 'license': 'MIT', 'skills': [], 'rules': [], 'routines': [], 'mcp_servers': servers}), encoding='utf-8')
            result = {'backend': 'executor', 'status': 'running', 'tools': ['up-add']}
            with patch('hub_ui.shared_gateway_connection', return_value=result):
                status = hub_ui.gateway_connection(root)
            self.assertEqual((status['served'], status['total']), (1, 3))
            self.assertEqual([item['name'] for item in status['down']], ['down-one', 'down-two'])
            self.assertTrue(all(item['reason'] for item in status['down']))

    def test_gateway_lists_every_catalog_server_with_a_next_step_for_silent_ones(self):
        import tempfile
        import json
        from unittest.mock import patch
        import hub_ui
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = ['s%d' % number for number in range(9)]
            servers = [{'name': name, 'path': 'mcp/servers/' + name + '.json', 'owner': 'agentlocker', 'sha256': '0' * 64,
                        'provenance': {'source_id': 'src-' + '0' * 12, 'origin': 'fixture'}} for name in names]
            (root / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'name': 'agentlocker', 'license': 'MIT', 'skills': [], 'rules': [], 'routines': [], 'mcp_servers': servers}), encoding='utf-8')
            result = {'backend': 'executor', 'status': 'running', 'tools': ['s0-add']}
            with patch('hub_ui.shared_gateway_connection', return_value=result):
                status = hub_ui.gateway_connection(root)
            self.assertEqual(status['up'], ['s0'])
            self.assertEqual(sorted(status['up'] + [item['name'] for item in status['down']]), names)
            self.assertEqual(len(status['up']) + len(status['down']), status['total'])
            silent = [item for item in status['down'] if item['reason'] == 'Lists no tools through the gateway']
            self.assertTrue(silent and all(item['next_step'] for item in silent))
