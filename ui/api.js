'use strict';
export const $ = id => document.getElementById(id);
export const state = {current: 0, token: null, status: null, report: null, imported: new Set(), authCards: new Set(), tickets: {}, savedAt: {}};
export function message(text, error = false) { $('message').textContent = text; $('message').className = error ? 'error' : ''; if (text) $('message').scrollIntoView({block: 'nearest'}); }
export async function api(route, data) {
  const response = await fetch('/api/' + route, data === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': state.token}, body: JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Setup request failed.');
  return result;
}
export const plural = (n, one, many = one + 's') => `${n} ${n === 1 ? one : many}`;
export function element(tag, text) { const node = document.createElement(tag); node.textContent = text; return node; }
export function table(target, headers, rows) {
  const table = document.createElement('table');
  const head = document.createElement('tr');
  headers.forEach(text => head.append(element('th', text)));
  const thead = document.createElement('thead'); thead.append(head); table.append(thead);
  const body = document.createElement('tbody');
  rows.forEach(row => { const tr = document.createElement('tr'); row.forEach(text => tr.append(element('td', text))); body.append(tr); });
  table.append(body); $(target).className = 'card'; $(target).replaceChildren(table);
}

export function authKind(item) { return item.state || (item.status === 'connected' ? 'saved' : item.status === 'No credentials declared' ? 'no_credentials' : 'needs_signin'); }
export function pendingAuth() { return state.status.auth.filter(item => authKind(item) === 'needs_signin' || item.legacy_credential); }
export async function refresh() {
  state.status = await api('status');
  $('locker-counts').textContent = 'In your locker: ' + Object.entries(state.status.counts).map(([kind, n]) => `${n} ${kind.replaceAll('_', ' ')}`).join(', ');
  state.renderAuth?.();
}
export async function action(button, run) {
  button.disabled = true; message('');
  try { await run(); } catch (error) { message(error.message, true); } finally { button.disabled = false; if (state.report) state.updateSelection?.(); }
}
export function on(id, run) { $(id).onclick = () => action($(id), run); }
