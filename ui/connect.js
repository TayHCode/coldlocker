import { $, state, api, message, element, action, refresh } from './api.js';

const sub = text => { const node = element('span', text); node.className = 'sub'; return node; };
const labels = {claude: 'Claude Code', codex: 'Codex', cursor: 'Cursor', opencode: 'OpenCode'};
function renderCalls(result) {
  const log = $('call-log'); log.replaceChildren();
  if (!Object.keys(result.first_call || {}).length) log.append(element('p', 'Waiting for the first call from an agent.'));
  if (result.older) log.append(element('p', result.older + ' earlier ' + (result.older === 1 ? 'call' : 'calls') + ' not shown.'));
  if (result.setup_checks) log.append(element('p', result.setup_checks + ' setup ' + (result.setup_checks === 1 ? 'check' : 'checks') + ' run by this wizard, not shown.'));
  const marked = new Set();
  result.calls.forEach(call => {
    const row = element('div', ''); row.setAttribute('role', 'row');
    const time = element('span', new Date(call.ts * 1000).toLocaleTimeString([], {hour12: false})); time.className = 't';
    const method = element('span', call.method + (call.tool ? ' ' + call.tool : '') + (call.tools != null ? ' · ' + call.tools + ' tools' : ''));
    const first = result.first_call?.[call.client];
    const isFirst = !marked.has(call.client) && first && first.ts === call.ts && first.method === call.method;
    if (isFirst) marked.add(call.client);
    const tail = element('span', isFirst ? 'First call' : call.duration_ms == null ? '—' : Math.round(call.duration_ms) + ' ms'); tail.className = isFirst ? 'live' : 't';
    row.append(time, element('span', call.client), method, tail); log.append(row);
  });
}
function renderAgent() {
  const agents = state.connection.agents; const selected = agents.find(item => item.agent === state.connectAgent) || agents.find(item => item.agent === 'codex') || agents[0];
  const tabs = $('agent-tabs'); tabs.replaceChildren();
  agents.forEach(item => {
    const tab = element('button', item.label || labels[item.agent] || item.agent); tab.setAttribute('role', 'tab'); tab.setAttribute('aria-selected', String(item === selected)); tab.className = item === selected ? 'on' : ''; tab.tabIndex = item === selected ? 0 : -1;
    tab.id = 'agent-tab-' + item.agent; tab.setAttribute('aria-controls', 'agent-config');
    tab.onkeydown = event => {
      const index = agents.indexOf(item);
      const next = {ArrowRight: (index + 1) % agents.length, ArrowLeft: (index + agents.length - 1) % agents.length, Home: 0, End: agents.length - 1}[event.key];
      if (next === undefined) return;
      event.preventDefault(); state.connectAgent = agents[next].agent; renderAgent(); renderCatalog(); $('agent-tab-' + state.connectAgent).focus();
    };
    tab.onclick = () => { state.connectAgent = item.agent; renderAgent(); renderCatalog(); $('agent-tab-' + item.agent).focus(); }; tabs.append(tab);
  });
  const panel = $('agent-config'); panel.replaceChildren(); panel.setAttribute('role', 'tabpanel');
  if (selected) panel.setAttribute('aria-labelledby', 'agent-tab-' + selected.agent);
  if (!selected) { panel.textContent = 'No agent configuration available.'; return; }
  const pre = element('pre', ''); const path = element('span', '# ' + selected.display_path); path.className = 'config-path';
  pre.append(path, document.createTextNode('\n' + selected.snippet));
  const actions = element('div', ''); actions.className = 'connect-actions';
  const copy = element('button', 'Copy'); copy.className = 'subtle'; copy.onclick = () => action(copy, async () => { await navigator.clipboard.writeText(selected.snippet); copy.textContent = 'Copied'; message('Configuration copied.'); });
  actions.append(copy, element('span', 'Then restart ' + (selected.label || labels[selected.agent] || selected.agent) + '.')); panel.append(pre, actions);
}
function renderGateway() {
  const gateway = state.connection.gateway; const side = $('connect-side'); side.replaceChildren(element('h2', 'Gateway'));
  const running = gateway.status === 'running';
  const served = gateway.total == null ? '' : gateway.served ? 'The gateway serves ' + gateway.served + ' of ' + gateway.total + ' servers. ' : 'The gateway is running but serves no servers yet. ';
  $('connect-state').textContent = running ? served + "Copy the entries for your agent's config."
    : "Agents can't reach your servers until the gateway is running. Start it, then add its entry to your agent's config.";
  const status = element('p', '');
  if (running) {
    status.append(document.createTextNode('Running on ')); const host = element('span', gateway.url.replace(/^https?:\/\//, '').replace(/\/.*$/, '')); host.className = 'mono'; status.append(host);
    status.append(document.createTextNode((gateway.started_at ? ' since ' + new Date(gateway.started_at * 1000).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}) : '') + '.'));
    if (gateway.total != null) status.append(document.createTextNode(' Serves ' + gateway.served + ' of ' + gateway.total + ' servers.'));
    side.append(status);
    if (gateway.total != null) {
      const list = element('ul', ''); list.className = 'down';
      (gateway.up || []).forEach(name => { const row = element('li', name); row.append(sub('Serving')); list.append(row); });
      (gateway.down || []).forEach(item => { const row = element('li', item.name); row.append(sub(item.reason)); if (item.next_step) row.append(sub(item.next_step)); list.append(row); });
      side.append(list);
    }
    if (gateway.stoppable) {
      const stop = element('button', 'Stop gateway'); stop.className = 'subtle';
      stop.onclick = () => action(stop, async () => { if (!window.confirm('Stop this locker gateway? Agents will keep access to the read-only catalog, but gateway tool calls will stop.')) return; await api('gateway/stop', {}); await render(); });
      side.append(stop);
    } else side.append(element('p', 'This gateway is managed separately. Stop it in its own runtime.'));
    renderCatalog(); return;
  }
  status.textContent = 'Not running.'; side.append(status);
  if (gateway.startable) {
    const start = element('button', 'Start gateway'); start.className = 'subtle';
    start.onclick = () => action(start, async () => { await api('gateway/start', {}); await render(); });
    side.append(start);
  } else if (gateway.install) {
    const command = element('p', 'Install it once: '); command.append(element('code', gateway.install)); side.append(command);
  }
  renderCatalog();
}
function renderCatalog() {
  let panel = $('catalog-fallback');
  if (!document.querySelector('#connect-side #catalog-fallback')) { panel = element('div', ''); panel.id = 'catalog-fallback'; $('connect-side').append(panel); }
  const agents = state.connection.agents; const selected = agents.find(item => item.agent === state.connectAgent) || agents.find(item => item.agent === 'codex') || agents[0];
  panel.replaceChildren(element('h2', 'Read-only catalog'), element('p', 'Available without the gateway. This stdio entry lists locker content; it does not execute upstream tools.'), element('pre', selected?.catalog_snippet || 'python3 mcp_endpoint.py'));
  if (selected?.catalog_snippet) {
    const copy = element('button', 'Copy catalog entry'); copy.className = 'subtle';
    copy.onclick = () => action(copy, async () => { await navigator.clipboard.writeText(selected.catalog_snippet); copy.textContent = 'Copied'; message('Read-only catalog entry copied.'); });
    panel.append(copy);
  }
}
export function init() {
  let busy = false;
  setInterval(async () => {
    if (state.current !== 3 || document.hidden || busy) return;
    busy = true;
    try { renderCalls(await api('calls')); } catch (error) { $('call-log').textContent = error.message; } finally { busy = false; }
  }, 2000);
}
export async function render() {
  await refresh(); state.connection = await api('connect', {});
  renderAgent(); renderGateway(); renderCalls(await api('calls'));
}
