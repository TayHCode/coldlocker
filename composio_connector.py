import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from checks import validators
from gateway_render import render_server, write_output
from hub import ordinary_parents, private_store, read_json, read_manifest, record
from store import FileStore, ordinary_path, reference_name


KEY_NAME = 'composio-api-key'


def definition(name, url):
    name = validators.name('composio-' + validators.name(name))
    validators.require(isinstance(url, str) and not any(c.isspace() or ord(c) < 32 for c in url),
                       'invalid URL')
    validators.require(not any(c in url for c in '@?#\\'), 'URL credentials and queries are forbidden')
    parsed = urlsplit(url)
    validators.require(parsed.scheme in ('https', 'http') and bool(parsed.hostname)
                       and parsed.port != 0, 'invalid URL')
    validators.require(parsed.scheme == 'https' or parsed.hostname in ('localhost', '::1',
                       '.'.join(('127', '0', '0', '1'))), 'remote URL requires HTTPS')
    server = {'name': name, 'description': 'Live Composio hosted MCP upstream',
              'transport': 'http', 'url': url,
              'headers': {'x-api-key': 'secret://' + KEY_NAME}}
    validators.mcp(server)
    render_server(server)
    return server


def connect(root, name, url):
    server = definition(name, url)
    ordinary_path(root / 'manifest.json')
    manifest = read_manifest(root)
    catalog = root / validators.LOCAL
    destination = catalog / 'mcp' / 'servers' / (server['name'] + '.json')
    ordinary_parents(destination.parent)
    validators.require(not destination.is_symlink(), 'destination must not be symlinked')
    existing = next((entry for entry in manifest['mcp_servers'] if entry['name'] == server['name']), None)
    if existing or destination.exists():
        validators.require(existing is not None and destination.is_file()
                           and existing['owner'] == 'agentlocker'
                           and existing['provenance']['origin'] == 'connected'
                           and existing['sha256'] == validators.digest(destination)
                           and read_json(destination) == server, 'conflicting server; preserve existing definition')
        print('Already connected: ' + server['name'])
    else:
        private_store(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_output(destination, json.dumps(server, indent=2) + '\n')
        record(validators.read_local(root, read_json), 'mcp_servers', server['name'], destination, 'agentlocker', 'connected', catalog)
    print('Private key entry: python3 store.py set ' + KEY_NAME)
    print('Use the invisible terminal prompt, never argv. Store.put uses name ' + KEY_NAME + '.')
    print('Wizard step 5: select ' + server['name'] + ', header x-api-key, API key / complete header (raw).')
    return 0


UPSERT_URL = 'https://backend.composio.dev/api/v3.1/custom/toolkits/upsert'
CUSTOM_MCP_DOC = 'https://docs.composio.dev/docs/extending-sessions/custom-mcp'


def catalog_payload(root, url=None):
    from remote_settings import catalog_url, oauth_discovery_url
    url = catalog_url(root, url)
    return {'slug': 'AGENTLOCKER', 'toolkit_config': {
        'name': 'agentlocker', 'app_url': url,
        'auth_schemes': [{'mode': 'DCR_OAUTH', 'discovery_url':
                          oauth_discovery_url(url)}]}}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def catalog(root, write=False, yes=False, key_ref='secret://composio-api-key', url=None):
    reference_name(key_ref)
    validators.require(not write or yes, 'catalog registration requires --write --yes')
    payload = catalog_payload(root, url)
    print('Composio → agentlocker read-only catalog (experimental Custom MCP; API-only).')
    print('POST ' + UPSERT_URL)
    print('Header x-api-key: ' + key_ref + ' (resolved privately only with --write --yes)')
    print(json.dumps(payload, indent=2))
    print('Registration is project-scoped; an existing slug may have its display metadata updated.')
    print('URL/auth conflicts are preserved; this command never deletes or replaces a toolkit.')
    print('Next (owner actions; these are not executed):')
    print('POST /api/v3.1/auth_configs with {"toolkit": {"slug": "CUSTOM_AGENTLOCKER"}}; retain auth_config.id.')
    print('POST /api/v3.1/connected_accounts/link with auth_config_id and your session user_id; open redirect_url privately.')
    print('Approve the owner Access login and catalog:read consent in your browser.')
    print('Initial tool sync starts when the connection becomes active; resync after tool changes.')
    print('Add CUSTOM_AGENTLOCKER to your Composio session toolkits and pin its active connected account.')
    print('Discover hub_list_skills, hub_list_rules, hub_list_routines; read content with hub_get_item.')
    print('Only published content is served. Routine definitions do not activate schedules.')
    print('Setup and connection steps: docs/composio.md; upstream: ' + CUSTOM_MCP_DOC)
    if not write:
        print('Preview only; no network, Store reads or writes. Register with --catalog --write --yes.')
        return 0
    try:
        key = FileStore(root).resolve(key_ref)
        validators.require(bool(key.strip()) and not any(ord(c) < 32 or ord(c) > 126 for c in key),
                           'invalid project key')
        request = urllib.request.Request(UPSERT_URL, json.dumps(payload).encode('utf-8'),
                                         {'Content-Type': 'application/json', 'x-api-key': key}, method='POST')
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(request, timeout=30) as response:
            result = json.loads(response.read(65537))
        validators.require(isinstance(result, dict) and result.get('slug') == 'CUSTOM_AGENTLOCKER',
                           'unexpected registration response')
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise ValueError('Composio registration refused (HTTP ' + str(status) +
                         '); no automatic retry or replacement. Review project state before retrying.') from None
    except (OSError, ValueError, TypeError):
        raise ValueError('Composio registration could not be confirmed; check the private Store key and '
                         'project state before retrying. No automatic retry.') from None
    print('Registered CUSTOM_AGENTLOCKER. OAuth connection, sync and session use remain to be completed.')
    return 0


def run(args, root=None):
    if root is None:
        from hub import ROOT
        root = ROOT
    if args.catalog:
        validators.require(args.name is None, '--catalog cannot be combined with --name')
        return catalog(root, args.write, args.yes, args.api_key_ref, args.url)
    validators.require(not args.write and not args.yes and args.api_key_ref == 'secret://' + KEY_NAME,
                       '--write, --yes and --api-key-ref require --catalog')
    validators.require(args.name is not None and args.url is not None,
                       'optional upstream connector requires --name and --url; use --catalog for locker access')
    return connect(root, args.name, args.url)


def register(commands):
    command = commands.add_parser('connect-composio', help='Expose the locker to Composio (--catalog), or optionally register a Composio upstream.')
    command.add_argument('--catalog', action='store_true', help='Print remote catalog OAuth registration and next steps.')
    command.add_argument('--write', action='store_true', help='Register only the catalog toolkit via the Composio API.')
    command.add_argument('--yes', action='store_true', help='Confirm the project-scoped registration write.')
    command.add_argument('--api-key-ref', default='secret://' + KEY_NAME, help='Private Store secret:// reference for the Composio project key.')
    command.add_argument('--name')
    command.add_argument('--url', help='Catalog public_url override with --catalog; upstream server URL otherwise.')
    command.set_defaults(action=run)
