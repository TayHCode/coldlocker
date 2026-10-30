import { $, state, api, element, refresh, action, message, authKind, pendingAuth } from './api.js';

const time = value => value ? new Date(value * 1000).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}) : 'earlier';
const oauth = new Map();
const consent = new Set();
const sub = text => { const node = element('span', text); node.className = 'sub'; return node; };
function button(text, run, style = 'subtle') {
  const node = element('button', text); node.className = style;
  node.onclick = () => action(node, run); return node;
}
function credentialForm(fields, run) {
  const form = element('form', ''); form.className = 'auth-editor';
  const save = element('button', 'Save'); save.type = 'submit';
  form.onsubmit = event => { event.preventDefault(); return action(save, run); };
  form.append(...fields, save);
  return form;
}
function editor(item, cell) {
  const header = document.createElement('select'); header.setAttribute('aria-label', item.name + ' credential header');
  (item.headers || []).forEach(name => { const option = element('option', name); option.value = name; header.append(option); });
  const scheme = document.createElement('select'); scheme.setAttribute('aria-label', item.name + ' credential type');
  [['bearer', 'Bearer token'], ['raw', 'API key / complete header']].forEach(([value, text]) => { const option = element('option', text); option.value = value; scheme.append(option); });
  const input = document.createElement('input'); input.type = 'password'; input.autocomplete = 'off'; input.spellcheck = false;
  input.setAttribute('data-lpignore', 'true'); input.setAttribute('data-1p-ignore', 'true'); input.setAttribute('aria-label', item.name + ' token or API key'); input.placeholder = 'Token or API key';
  const form = credentialForm([header, scheme, input], async () => {
    await api('auth/bearer', {name: item.name, header: header.value, scheme: scheme.value, value: input.value});
    state.savedAt[item.name] = new Date().toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
    state.status.auth = state.status.auth.map(row => row.name === item.name ? {...row, state: 'saved', saved_at: Date.now() / 1000} : row);
    message('Credential saved for ' + item.name + '. Not checked yet.');
    renderAuth();
    input.value = '';
    await refresh();
  });
  cell.append(form); input.focus();
}
function secretEditor(item, key, cell) {
  const input = document.createElement('input'); input.type = 'password'; input.autocomplete = 'off'; input.spellcheck = false;
  input.setAttribute('data-lpignore', 'true'); input.setAttribute('data-1p-ignore', 'true'); input.setAttribute('aria-label', item.name + ' ' + key); input.placeholder = key;
  const form = credentialForm([input], async () => {
    await api('auth/secret', {name: item.name, key, value: input.value});
    state.savedAt[item.name] = new Date().toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
    state.status.auth = state.status.auth.map(row => row.name === item.name ? {...row, state: 'saved', saved_at: Date.now() / 1000, copied_from: null} : row);
    message('Credential saved for ' + item.name + '. Not checked yet.');
    renderAuth();
    input.value = '';
    await refresh();
  });
  cell.append(form); input.focus();
}
function removeSecret(item, key) {
  return button('Remove ' + key, async () => {
    if (!window.confirm('Remove the stored ' + key + ' for ' + item.name + '? It will need to be set again before agents can use this server.')) return;
    await api('auth/secret/remove', {name: item.name, key});
    state.status.auth = state.status.auth.map(row => row.name === item.name ? {...row, state: 'needs_signin', copied_from: null} : row);
    renderAuth();
    await refresh();
  }, 'auth-link');
}
function secretButtons(item, verb, actions) {
  (item.secrets || []).forEach(key => {
    actions.append(button(verb + ' ' + key, async () => secretEditor(item, key, actions), 'auth-link'));
    if (verb === 'Replace') actions.append(removeSecret(item, key));
  });
}
function gatewayNotice(gateway) {
  if (gateway?.status !== 'stopped' || !gateway.install) return null;
  const notice = element('p', ''); notice.className = 'notice';
  if (gateway.startable) {
    notice.append(document.createTextNode("The gateway isn't running, so checks can't reach your servers. "));
    notice.append(button('Start gateway', async () => { await api('gateway/start', {}); await refresh(); }));
  } else {
    notice.append(document.createTextNode("The gateway isn't running or installed. Install it once with "), element('code', gateway.install), document.createTextNode(', then start it here.'));
  }
  return notice;
}
function failure(item, status) {
  status.append(element('span', 'Check failed'), sub(item.checked_at ? 'Checked ' + time(item.checked_at) : 'Not verified'));
  if (item.check === 'no_tools') {
    status.append(element('span', 'The gateway is running but this server lists no tools.'), sub('Its upstream may be down or still starting.'));
  } else if (item.check === 'unreachable') {
    status.append(element('span', item.error || "The gateway isn't running."));
  } else status.append(element('span', item.error || item.detail || 'Live tool check failed.'));
}
function legacyFailure(item, status) {
  const marker = element('span', 'Cannot be used'); marker.className = 'needs failing';
  const command = element('code', item.next_step); command.className = 'cmd';
  const fix = element('span', ''); fix.className = 'sub';
  fix.append(document.createTextNode("Agents won't see it until you replace it. Run "), command, document.createTextNode('.'));
  status.append(marker, element('span', item.detail[0].toUpperCase() + item.detail.slice(1) + '.'), fix);
}
function renderAuth() {
  const items = state.status.auth;
  const gateway = state.status.gateway;
  const offer = gateway?.status === 'stopped' && gateway.startable;
  const table = element('table', ''); table.className = 'au';
  const head = element('thead', ''); const headings = element('tr', '');
  ['Server', 'Status', ''].forEach(text => headings.append(element('th', text))); head.append(headings); table.append(head);
  const body = element('tbody', '');
  items.forEach(item => {
    const outcome = item.oauth_result;
    if (outcome && outcome.status !== 'saved') consent.delete(item.name);
    if (consent.has(item.name) && item.state !== 'needs_signin') { consent.delete(item.name); message('Signed in to ' + item.name + '. Check it to confirm it works.'); }
    const row = element('tr', ''); const name = element('td', item.name); name.append(sub(item.description || (item.headers ? 'Bearer token / OAuth' : 'stdio')));
    const status = element('td', ''); const actions = element('td', ''); actions.className = 'r';
    const kind = authKind(item);
    const check = () => button(offer ? 'Start gateway and check' : kind === 'check_failed' ? 'Check again' : 'Check now', async () => {
      if (offer) await api('gateway/start', {});
      await api('auth/check', {server: item.name}); await refresh();
    });
    if (outcome && outcome.status !== 'saved') {
      const failed = element('span', 'Sign-in failed ' + time(outcome.at)); failed.className = 'err';
      const explanation = {denied: 'Access was declined. Try again and approve access, or use a token.', expired: 'The sign-in request expired. Try again.', failed: 'The provider exchange failed. Try again, or use a token.'}[outcome.status] || 'Try signing in again.';
      status.append(failed, sub(explanation), sub('No new credentials were saved.'));
    }
    if (kind === 'verified') {
      status.append(element('span', 'Verified ' + time(item.verified_at)), sub('Listed ' + item.tools + (item.tools === 1 ? ' tool' : ' tools')));
      actions.append(button(item.headers ? 'Replace token' : 'Edit', async () => item.headers ? editor(item, actions) : message('Edit the server definition, then check it again.'), 'auth-link'));
      secretButtons(item, 'Replace', actions);
    } else if (kind === 'saved') {
      status.append(element('span', 'Saved ' + (state.savedAt[item.name] || time(item.saved_at))), sub('Not checked yet'));
      const saved = element('div', (item.headers ? 'Token' : 'Credential') + ' saved on this computer. Check it to confirm ' + item.name + ' accepts it.'); saved.className = 'saved'; saved.setAttribute('role', 'status');
      status.append(saved);
      if (item.copied_from) status.append(sub('Copied from ' + item.copied_from + ' config'));
      actions.append(check());
      secretButtons(item, 'Replace', actions);
    } else if (item.legacy_credential) {
      legacyFailure(item, status);
    } else if (kind === 'check_failed') {
      failure(item, status); actions.append(check());
      if (item.headers) actions.append(button('Replace token', async () => editor(item, actions), 'auth-link'));
      secretButtons(item, 'Replace', actions);
    } else if (kind === 'no_credentials') {
      row.className = 'dimrow'; status.textContent = 'No credentials needed';
    } else {
      const needed = element('span', 'Needs sign-in'); needed.className = 'needs'; status.append(needed);
      if (item.headers) {
        const offered = oauth.get(item.name) || item.refreshable;
        if (consent.has(item.name)) status.append(sub('Waiting for approval in the consent tab. This updates when you return here.'));
        else if (oauth.get(item.name) === false && !item.refreshable) status.append(sub('OAuth sign-in was not discovered. Use a token, or retry discovery when the server is available.'));
        if (offered) {
          const client = document.createElement('input'); client.placeholder = 'Public OAuth client ID (optional)'; client.setAttribute('aria-label', item.name + ' OAuth client ID');
          actions.append(button(outcome && outcome.status !== 'saved' ? 'Try again' : 'Sign in to ' + item.name, async () => {
            const tab = window.open('about:blank', '_blank'); if (!tab) throw new Error('Allow a consent tab, then try again.'); tab.opener = null;
            try { tab.location = (await api('auth/oauth', {name: item.name, client_id: client.value})).url; }
            catch (error) { tab.close(); throw error; }
            delete item.oauth_result; consent.add(item.name); renderAuth(); message('Approve access for ' + item.name + ' in the consent tab, then return here.');
          }, ''));
          const details = element('details', ''); details.append(element('summary', 'Use a token or existing OAuth client'), client);
          details.append(button('Enter token', async () => editor(item, details))); actions.append(details);
        } else actions.append(button('Enter token', async () => editor(item, actions), ''));
      } else secretButtons(item, 'Set', actions);
    }
    row.append(name, status, actions); body.append(row);
  });
  table.append(body); $('auth').replaceChildren(...[gatewayNotice(gateway), table].filter(Boolean));
  const pending = pendingAuth();
  const note = $('auth-note'); note.textContent = pending.length ? `You can sign in to ${pending.map(item => item.name).join(', ')} later. Agents just won't see it until you do.` : ''; note.hidden = !pending.length;
  if (!items.length) $('auth').append(element('p', 'No MCP servers in your locker yet. Import a configuration on MCP servers, or continue to the read-only catalog.'));
  const meta = document.querySelector('[data-meta="signin"]');
  if (meta) meta.textContent = pending.length ? pending.length + ' need sign-in' : items.some(item => authKind(item) !== 'no_credentials') ? `${items.filter(item => authKind(item) === 'verified').length} verified` : '';
}
async function discover() {
  const unknown = state.status.auth.filter(item => item.headers && item.state === 'needs_signin' && oauth.get(item.name) !== true);
  await Promise.all(unknown.map(async item => { try { oauth.set(item.name, (await api('auth/discover', {name: item.name})).oauth); } catch (error) { oauth.delete(item.name); } }));
  renderAuth();
}
export function init() {
  state.renderAuth = renderAuth;
  window.addEventListener('focus', () => { if (consent.size && state.current === 2) refresh().catch(error => message(error.message, true)); });
}
export async function render() { await refresh(); await discover(); }
