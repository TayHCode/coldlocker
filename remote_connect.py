import json


SCOPE = 'catalog:read'
CLAUDE_SOURCE = 'https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp'
DOTS_SOURCE = 'https://help.openai.com/en/articles/20001530-getting-started-with-your-dot'
MUSE_SOURCE = 'https://www.meta.com/help/artificial-intelligence/1687253048996149/'
VERIFY_PROMPT = ('Use the agentlocker remote MCP connection. Discover tools/list, then call hub_list_skills, '
                 'hub_list_rules and hub_list_routines. Read shared-policy with hub_get_item '
                 '{"kind": "rule", "name": "shared-policy"}. Report the names and hashes returned. '
                 'Load only relevant skills and supporting files. Keep secret:// references opaque. '
                 'Listing must not activate routines or execute upstream MCP servers. '
                 'If tools are unavailable, stop and report that; do not invent catalog content.')


AGENTS = ('codex', 'claude', 'claude_ai', 'claude_desktop', 'chatgpt', 'grok',
          'dots', 'muse', 'muse_code', 'mausbot', 't3')


def clients(root, override=None):
    from remote_settings import catalog_url
    url = catalog_url(root, override)
    claude = ('Customize > Connectors > + Add > Add custom connector; name agentlocker, URL ' + url +
              '. Review detected OAuth, choose Sign in now and Register automatically, then Add and complete browser sign-in. '
              'Team/Enterprise owners first add Custom > Web in Organization settings > Connectors; members then Connect. '
              'Enable the connector for the conversation. Desktop uses this account connector, not claude_desktop_config.json.')
    return {
        'codex': {'status': 'verified-from-docs',
                  'steps': 'codex mcp add agentlocker --url ' + url + '\ncodex mcp login agentlocker',
                  'settings': '[mcp_servers.agentlocker]\nurl = "' + url + '"',
                  'notes': 'CLI/app remote HTTP MCP. Codex Cloud availability is UNCONFIRMED; do not infer it from CLI support.',
                  'sources': ['https://developers.openai.com/codex/mcp']},
        'claude': {'status': 'verified-from-docs',
                   'steps': 'claude mcp add --transport http agentlocker ' + url + '\nThen use /mcp in Claude Code to authenticate.',
                   'sources': ['https://code.claude.com/docs/en/mcp']},
        'claude_ai': {'status': 'verified-from-docs', 'steps': claude, 'sources': [CLAUDE_SOURCE]},
        'claude_desktop': {'status': 'verified-from-docs', 'steps': claude, 'sources': [CLAUDE_SOURCE]},
        'chatgpt': {'status': 'verified-from-docs',
                    'steps': 'ChatGPT Plugins > plus > Add custom MCP server. Name agentlocker; Connection: public HTTPS URL ' + url +
                             '; authentication: OAuth. Review warning, create the connection and finish owner sign-in. Select the plugin in chat. Account/workspace policies apply.',
                    'notes': 'General MCP tools in chat; this catalog has no search/fetch for Company Knowledge indexing.',
                    'sources': ['https://developers.openai.com/plugins/deploy/connect-chatgpt',
                                'https://developers.openai.com/plugins/build/auth']},
        'grok': {'status': 'verified-from-docs',
                 'steps': 'In the intended Grok Bot conversation, paste: Add this MCP server: ' + url +
                          '. Name it agentlocker and use OAuth browser sign-in. Complete the Authenticate card, then start a new message to discover tools. See docs/grok-bot-bootstrap.md.',
                 'notes': 'Cursor Grok Bot, not xAI Grok Business. Team connector/network policy may restrict the domain. Connection, catalog reads and shared updates were user-reported working in an owner acceptance trial; other accounts remain untested.',
                 'sources': ['https://forum.cursor.com/t/grokbot-custom-connectors/169965/5',
                             'https://cursor.com/docs/grok-bot/security',
                             'https://cursor.com/help/grok-bot/connect-apps']},
        'dots': {'status': 'unconfirmed',
                 'steps': 'Dot profile > Customize > Plugins uses shared ChatGPT settings. Add agentlocker at ' + url + ' through the ChatGPT custom MCP flow, then check whether the Dot can select it. Dot-specific custom remote MCP OAuth support is UNCONFIRMED. Run verify_prompt before scheduling; stop if tools are absent.',
                 'sources': [DOTS_SOURCE, 'https://learn.chatgpt.com/docs/enterprise/dots-admin-guide', 'https://developers.openai.com/plugins/deploy/connect-chatgpt']},
        'muse': {'status': 'unconfirmed',
                 'steps': 'Hosted Muse: Settings > Connectors documents supported apps, but a custom MCP OAuth flow is UNCONFIRMED in official docs. Request: Create a Custom Connector named agentlocker for ' + url +
                          ' using remote MCP over Streamable HTTP and OAuth browser sign-in. Treat this as a capability probe; stop if unsupported. Muse Code is separate: see muse_code.',
                 'sources': [MUSE_SOURCE]},
        'muse_code': {'status': 'verified-from-docs',
                      'steps': 'Add settings below for ' + url + ' to Muse Code settings, then run muse mcp login agentlocker. Use /mcp to inspect tools.',
                      'settings': {'mcp_servers': {'agentlocker': {'transport': 'streamable_http', 'url': url, 'enabled': True, 'mode': 'optional'}}},
                      'sources': ['https://dev.meta.ai/docs/muse-code/extending']},
        'mausbot': {'status': 'unconfirmed',
                    'steps': 'Custom remote MCP OAuth registration in MausBot is UNCONFIRMED. Choose an engine with documented remote MCP OAuth support (Claude Code or Codex), configure agentlocker at ' + url + ' in that engine on the selected computer, authenticate there, then verify tools from the bot. This does not prove hosted connector parity.',
                    'notes': 'OpenMausBot is a separate project: its custom MCP guide documents Plugins > MCP servers > Add server > Connect to a URL; enter ' + url + '; Test, Sign in, complete OAuth, Test, enable and grant bot access. This does not verify the original MausBot.',
                    'sources': ['https://github.com/dr-biko/MausBot', 'https://github.com/milind-soni/OpenMausBot/blob/main/docs/custom-mcp-servers.md']},
        't3': {'status': 'unconfirmed',
               'steps': 'T3 remote MCP OAuth configuration is UNCONFIRMED from public official docs. Use a documented provider connection (Codex or Claude Code) for ' + url + ' and verify the catalog tools in the intended T3 thread before scheduling. Do not assume provider settings propagate.',
               'sources': ['https://github.com/pingdotgg/t3code']},
    }


def run(args, output, root):
    if args.write:
        raise ValueError('--remote is print-only; --write is not supported')
    from remote_settings import catalog_url
    url = catalog_url(root, getattr(args, 'url', None))
    available = clients(root, url)
    requested = args.agent or AGENTS
    unsupported = set(requested) - set(available)
    if unsupported:
        raise ValueError('--remote has no researched instructions for: ' + ', '.join(sorted(unsupported)))
    print(json.dumps({'print_only': True, 'url': url, 'scope': SCOPE, 'auth': 'OAuth; your Cloudflare Access one-time PIN in browser; never paste credentials into chat',
                      'reviewed': '2026-10-20', 'clients': {name: available[name] for name in dict.fromkeys(requested)},
                      'verify_prompt': VERIFY_PROMPT,
                      'note': 'Only the nonsecret remote URL configuration was read when no --url was supplied. No network requests, client config reads/writes, OAuth login or schedules performed. Documentation support is not an account connection test.'}, indent=2), file=output)
    return 0
