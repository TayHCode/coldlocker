import json

from checks import validators
from executor_connector import Session
from gateway_render import render_server
from mcp_endpoint import entries, read_server
from store import reference_name


def invoke(session, path, arguments, approve=False):
    result = session.exchange('tools/call', {'name': 'execute', 'arguments': {
        'code': 'return await tools.' + path + '(' + json.dumps(arguments) + ');'}})
    state = result.get('structuredContent', {})
    if state.get('status') == 'waiting_for_interaction' and approve:
        interaction = state.get('interaction', {})
        if (interaction.get('address') != path or interaction.get('args') != arguments
                or interaction.get('requestedSchema', {}).get('properties') != {}):
            raise ValueError('Executor requires manual interaction')
        result = session.exchange('tools/call', {'name': 'resume', 'arguments': {
            'executionId': state['executionId'], 'action': 'accept', 'content': '{}'}})
        state = result.get('structuredContent', {})
    if result.get('isError') or state.get('status') != 'completed':
        raise ValueError('Executor operation incomplete; inspect it in Executor')
    value = state.get('result', {})
    if not isinstance(value, dict) or value.get('ok') is not True:
        raise ValueError('Executor operation failed')
    return value['data']


def registration(root, name, apply=False):
    validators.name(name)
    matches = [entry for entry in entries(root, 'mcp_servers') if entry['name'] == name]
    if len(matches) != 1:
        raise ValueError('select an indexed MCP server')
    server = read_server(root, matches[0])
    if server.get('env') or server.get('headers'):
        raise ValueError('configure credentialed sources directly in Executor; Store references are not portable')
    rendered = render_server(server, root)
    slug = 'locker-' + name
    validators.name(slug)
    source = {'name': name, 'slug': slug, 'description': server['description']}
    if server['transport'] == 'stdio':
        source.update(transport='stdio', command=rendered['command'], args=rendered['args'])
    else:
        source.update(transport='remote', endpoint=rendered['url'], remoteTransport='streamable-http', auth={'kind': 'none'})
    if not apply:
        print('Would register selected no-auth server and user connection:', name)
        return 0
    with Session(root) as session:
        catalog = invoke(session, 'executor.coreTools.integrations.list', {})['integrations']
        if any(item['slug'] == slug for item in catalog):
            existing = invoke(session, 'executor.mcp.getServer', {'slug': slug})['integration']
            config = existing['config']
            expected = {key: value for key, value in source.items() if key not in ('name', 'slug', 'description', 'auth')}
            if (existing['kind'] != 'mcp' or any(config.get(key) != value for key, value in expected.items())
                    or any(config.get(key) for key in ('env', 'staticEnv', 'headers', 'queryParams', 'envVars', 'cwd'))
                    or any(method.get('kind') != 'none' for method in existing.get('authMethods', []))):
                raise ValueError('existing Executor integration conflicts; left unchanged')
        else:
            invoke(session, 'executor.mcp.addServer', source, approve=True)
        connections = invoke(session, 'executor.coreTools.connections.list', {})['connections']
        matching = [item for item in connections if item['integration'] == slug
                    and item['owner'] == 'user' and item['name'] == 'agentlocker']
        if matching:
            if len(matching) != 1 or matching[0].get('template') != 'none':
                raise ValueError('existing Executor connection conflicts; left unchanged')
        else:
            invoke(session, 'executor.coreTools.connections.create', {
                'owner': 'user', 'name': 'agentlocker', 'integration': slug, 'template': 'none'}, approve=True)
    print('Executor source connected:', name)
    return 0


def nonsecret_definition(integration):
    config = integration['config']
    if integration.get('kind') != 'mcp':
        raise ValueError('not an MCP integration')
    server = {'name': integration['slug'], 'description': 'Optional Executor source copy',
              'transport': 'stdio' if config.get('transport') == 'stdio' else 'http'}
    if server['transport'] == 'stdio':
        server.update(command=config['command'], args=config.get('args', []))
        if config.get('cwd') or config.get('envVars'):
            raise ValueError('source requires Executor runtime configuration')
    else:
        server['url'] = config['endpoint']
        if config.get('queryParams'):
            raise ValueError('query parameters are not exportable')
    for field, names in (('env', ('env', 'staticEnv')), ('headers', ('headers',))):
        values = {}
        for name in names:
            for key, value in config.get(name, {}).items():
                if key in values or not isinstance(value, str):
                    raise ValueError('invalid source mapping')
                reference_name(value)
                values[key] = value
        if values:
            server[field] = values
    if any(method.get('kind') != 'none' for method in integration.get('authMethods', [])):
        raise ValueError('source requires Executor-owned auth; use the live connector')
    validators.mcp(server)
    render_server(server)
    return server


def import_source(args):
    import hub
    root = hub.ROOT
    validators.name(args.slug)
    with Session(root) as session:
        integration = invoke(session, 'executor.mcp.getServer', {'slug': args.slug})['integration']
    server = nonsecret_definition(integration)
    if server['name'] != args.slug:
        raise ValueError('Executor source identity mismatch')
    manifest = hub.read_manifest(root)
    catalog = root / validators.LOCAL
    destination = catalog / 'mcp/servers' / (server['name'] + '.json')
    if destination.exists() or destination.is_symlink() or any(e['name'] == server['name'] for e in manifest['mcp_servers']):
        raise ValueError('existing manifest source preserved')
    hub.screen('mcp', server, [], getattr(args, 'approve_risky', False), root)
    if not args.apply:
        print('Would copy nonsecret Executor MCP definition:', server['name'])
        return 0
    hub.ordinary_parents(destination.parent)
    hub.private_store(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    hub.write_json(destination, server)
    hub.record(validators.read_local(root, hub.read_json), 'mcp_servers', server['name'], destination, 'agentlocker', 'executor-copy', catalog)
    return 0
