import { $, state, element, on, pendingAuth, plural } from './api.js';
import { scan, eligible, summary, foundTitle, importItems, updateMeta, routineDetail } from './scan.js';
function go(step, group) { document.querySelector(`[data-go="${step}"]${group ? `[data-group="${group}"]` : ''}`).click(); }
function inventory() {
  const counts = state.status.counts;
  const total = Object.values(counts).reduce((sum, count) => sum + count, 0);
  const ready = (state.report?.items || []).filter(eligible);
  const attention = pendingAuth().length;
  const locker = state.status.inventory;
  const examples = Object.values(locker).reduce((sum, kind) => sum + kind.examples, 0);
  $('welcome-lede').className = 'lede';
  $('welcome-title').textContent = total ? `Your locker has ${total} ${total === 1 ? 'thing' : 'things'} in it` : "Let's fill your locker";
  $('welcome-lede').textContent = total ? [state.lastImport && `Imported at ${state.lastImport}.`, examples && `Includes ${plural(examples, 'built-in example')}.`,
    attention ? `${attention} ${attention === 1 ? 'server needs' : 'servers need'} a sign-in before agents can use them.` : 'Your tools are kept in this locker.'].filter(Boolean).join(' ') : foundTitle(state.report) + '.';
  const examplesIn = keys => keys.reduce((sum, key) => sum + locker[key].examples, 0);
  const names = (...keys) => [keys.flatMap(key => locker[key].imported.map(id => locker[key].details?.[id] ? `${id} (${routineDetail(locker[key].details[id])})` : id)).join(', '), examplesIn(keys) && plural(examplesIn(keys), 'built-in example')].filter(Boolean).join(' · ');
  const auth = state.status.auth || [];
  const verified = auth.filter(i => i.state === 'verified' && i.verified_at).length;
  const saved = auth.filter(i => i.state === 'saved').length;
  const connected = Object.keys(state.calls?.first_call || {}).filter(name => name !== 'unknown').length;
  const rows = [
    [counts.skills, 'Skills', names('skills'), 'Review', 1, 'skill'],
    [counts.routines, 'Routines', [names('routines'), counts.routines ? 'Paused until you turn them on.' : ''].filter(Boolean).join('. '), 'Review', 1, 'routine'],
    [counts.mcp_servers, 'MCP servers', [names('mcp_servers'), `${verified} verified, ${saved} saved but not checked, ${attention} need sign-in`].filter(Boolean).join(' · '), attention ? 'Sign in' : '', 2],
    [counts.rules, 'Rules', names('rules'), '', 1],
    [counts.plugins + counts.connections, 'Plugins and connections', names('plugins', 'connections'), '', 1],
    [connected, 'Agent clients seen', 'Based on recorded requests.', 'Connect', 3]
  ];
  $('welcome-summary').replaceChildren(...rows.map(([count, title, sub, label, step, group]) => {
    const row = document.createElement('tr'); if (!count) row.className = 'dimrow';
    const name = element('td', ''); name.append(element('span', title), document.createTextNode(' · ' + count)); if (sub) { const line = element('span', sub); line.className = 'sub'; name.append(line); }
    const next = document.createElement('td'); next.className = 'r';
    if (label) { const button = element('button', label); button.className = label === 'Sign in' ? '' : 'text-link'; button.onclick = () => go(step, group); next.append(button); }
    row.append(name, next); return row;
  }));
  $('welcome-discovery').hidden = !ready.length;
  $('welcome-import').disabled = !ready.length;
  $('welcome-found').textContent = `${foundTitle(state.report)}. ${summary(ready)} are new and ready to import.`;
  $('welcome-health').textContent = state.status.check === 'Passed' ? `Everything checked out at ${state.checkedAt}.` : state.status.check;
  $('welcome-health-panel').hidden = false;
  updateMeta();
}
export function init({api, state}) {
  on('welcome-import', async () => { await importItems(state.report.items.filter(eligible).map(i => i.id)); inventory(); });
}
export async function render({api, state}) {
  $('welcome-lede').textContent = 'Scanning your computer…'; $('welcome-lede').className = 'lede scanning';
  $('welcome-import').disabled = true; $('welcome-discovery').hidden = true;
  $('welcome-health-panel').hidden = !state.checkedAt;
  try { if (!state.report) await scan(); else { state.status = await api('status'); } }
  catch (error) { $('welcome-lede').textContent = 'Scan failed. Use Scan again to retry.'; $('welcome-lede').className = 'lede'; throw error; }
  state.checkedAt = new Date().toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
  state.calls = await api('calls');
  inventory();
  const meta = document.querySelector('[data-meta="welcome"]'); if (meta) meta.textContent = state.checkedAt;
}
