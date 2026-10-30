import { $, state, api, message, element, table, refresh, action, on, plural } from './api.js';
export function eligible(item) { return item.importable && ['new', 'connect', 'changed'].includes(item.status) && !state.imported.has(item.id); }
export function found(report) { return (report?.items || []).filter(i => i.status !== 'duplicate' && !i.source_error); }
export function summary(items) {
  const count = kind => items.filter(i => i.kind === kind).length;
  return `${plural(count('skill'), 'skill')}, ${plural(count('routine'), 'routine')}, ${plural(count('mcp_server'), 'MCP server')}, ${plural(count('rule'), 'rule')} and ${plural(count('plugin') + count('connection'), 'plugin/connection', 'plugins/connections')}`;
}
const nouns = [['skill', 'skill', 'skills'], ['routine', 'routine', 'routines'], ['mcp_server', 'MCP server', 'MCP servers'], ['plugin', 'plugin', 'plugins'], ['connection', 'connection', 'connections'], ['rule', 'rule', 'rules']];
function counted(count, one, many) { return `${count} ${count === 1 ? one : many}`; }
export function confirmation(items) {
  const parts = nouns.map(([kind, one, many]) => [items.filter(i => i.kind === kind).length, one, many]).filter(([count]) => count).map(([count, one, many]) => counted(count, one, many));
  return `Import ${parts.length > 1 ? parts.slice(0, -1).join(', ') + ' and ' + parts.at(-1) : parts[0]}?`;
}
export function importResult(imported, rejected) { return `Imported ${counted(imported, 'item', 'items')}. ${rejected} could not be imported.`; }
export function foundTitle(report) { return `Found ${plural(found(report).length, 'item')} on this computer`; }
export function risky(item) { return item.status === 'needs_review' && !state.imported.has(item.id); }
function blocked(item) { return (item.review || []).some(finding => finding.blocking); }
function pickable(item) { return eligible(item) || (risky(item) && !blocked(item)); }
function selected() { return [...document.querySelectorAll('#scan-items input:checked')].map(input => input.value); }
function updateSelection() {
  const count = selected().length;
  $('selection-count').textContent = '';
  $('import-all').textContent = count ? `Import ${counted(count, 'item', 'items')}` : 'Nothing selected';
  $('import-all').disabled = !count || Boolean(state.scanning);
}
export function routineDetail({runner, schedule, timezone}) {
  const when = {cron: s => s.expression, interval: s => `every ${s.every_seconds} seconds`, fixed_time: s => s.weekdays ? `${s.time_of_day} on days ${s.weekdays.join(', ')}` : s.time_of_day, webhook: () => 'webhook'}[schedule?.type];
  return [runner, when?.(schedule), timezone, 'imported disabled'].filter(Boolean).join(' · ');
}
function displayPath(source) {
  return source?.startsWith('~/') || source?.startsWith('<') ? source : '~/' + (source || '').split(/[\\/]/).pop();
}
function note(parent, text, className = 'sub') { const node = element('span', text); node.className = className; parent.append(node); }
function missingFiles() { return (state.report.errors || []).filter(error => error.path); }
const agentLabels = {claude: 'Claude Code', codex: 'Codex', cursor: 'Cursor', opencode: 'OpenCode', agents: 'Shared agents', grok: 'Grok', t3: 'T3 Code', system: 'System'};
const kindLabels = {skill: 'Skill', routine: 'Routine', mcp_server: 'MCP server', plugin: 'Plugin', connection: 'Connection', rule: 'Rule'};
const openAgents = new Set();
const selections = new Map();
let selectionReport;
function vendorStatus(item) {
  if (item.plugin) return `Managed by the ${item.plugin} plugin`;
  return item.ownership === 'vendor' && item.agent ? `Built in to ${item.agent[0].toUpperCase()}${item.agent.slice(1)}` : 'Managed by a plugin';
}
function renderScan() {
  if (selectionReport !== state.report) { selections.clear(); selectionReport = state.report; }
  const items = state.report.items;
  const kinds = Object.keys(kindLabels);
  const keys = kinds;
  $('scan-items').replaceChildren();
  for (const key of keys) {
    const rows = items.filter(item => item.kind === key && !item.source_error);
    const missing = key === 'mcp_server' ? missingFiles() : [];
    if (!rows.length && !missing.length) continue;
    const group = document.createElement('details'); group.className = 'scan-group'; group.open = !openAgents.has(key); group.setAttribute('data-scan-group', key);
    group.ontoggle = () => group.open ? openAgents.delete(key) : openAgents.add(key);
    const heading = element('summary', `${({skill: 'Skills', routine: 'Routines', mcp_server: 'MCP servers', plugin: 'Plugins', connection: 'Connections', rule: 'Rules'})[key]} · ${rows.filter(item => item.status !== 'duplicate').length} found`);
    const grid = document.createElement('table'); grid.className = 'scan-table';
    const body = document.createElement('tbody'); grid.append(body);
    rows.forEach(item => {
      const row = document.createElement('tr'); row.className = risky(item) ? 'reviewrow' : eligible(item) ? '' : 'dimrow';
      const cell = document.createElement('td'); cell.className = 'cbcell';
      const box = document.createElement('input'); box.type = 'checkbox'; box.value = item.id; box.disabled = !pickable(item);
      box.checked = !box.disabled && (selections.get(item.id) ?? eligible(item));
      box.onchange = () => { selections.set(item.id, box.checked); updateSelection(); };
      box.setAttribute('aria-label', risky(item) ? `Import ${item.name} anyway` : `Import ${item.name}`); const target = document.createElement('label'); target.className = 'checkbox-target'; target.append(box); cell.append(target);
      const name = element('td', item.name); note(name, agentLabels[item.agent] || item.agent || kindLabels[item.kind], 'kind'); note(name, displayPath(item.source), 'sub mono');
      const imported = state.imported.has(item.id);
      let status = imported ? 'Imported' : {already_in_locker: 'Already in the locker · same contents', changed: 'Changed since last import', vendor: vendorStatus(item), skipped: 'Needs manual review', needs_review: 'Needs review', conflict: 'Name conflict. Review the file.', duplicate: 'Same contents. Imported once.', connect: 'Connect to the existing tool'}[item.status] || 'New';
      if (item.kind === 'routine' && eligible(item)) status = 'Imports paused';
      if (item.status === 'conflict' && item.reason?.includes('changed since')) status = 'Changed since import · review update instructions';
      const copies = items.filter(copy => copy.duplicate_of === item.id);
      if (item.kind === 'mcp_server' && copies.length && !imported) status = `Same server in ${new Set([item.source, ...copies.map(copy => copy.source)]).size} files, imported once`;
      const detail = element('td', status);
      if (item.kind === 'routine' && eligible(item)) note(detail, item.enabled_in_file === true ? 'Enabled in the file. Imports paused; nothing runs until you enable it.' : item.enabled_in_file === false ? 'Disabled in the file.' : 'Turn it on after import.');
      if (item.kind === 'routine' && item.runner) note(detail, routineDetail(item));
      const credentials = item.secret_names?.length ? item.secret_names.join(', ') : item.secrets ? `${item.secrets} credential${item.secrets === 1 ? '' : 's'}` : '';
      if (credentials && !risky(item)) note(detail, item.secret_names?.length ? item.secret_names.map(name => `${name} moves to the store`).join(' · ') : `${credentials} move to the store`);
      const explained = !imported && item.review?.length && ['needs_review', 'skipped'].includes(item.status);
      if (explained) {
        row.classList.add('reviewrow');
        const reasons = document.createElement('ul'); reasons.className = 'reasons';
        item.review.forEach(finding => { const reason = document.createElement('li'); reason.textContent = finding.reason; note(reason, finding.location, 'sub mono'); reasons.append(reason); });
        detail.append(reasons);
        if (blocked(item)) note(detail, 'Remove the secret from the source file. It cannot be imported.');
        else if (risky(item)) note(detail, 'Not imported unless you tick it.' + (credentials ? ` Ticking it also moves ${credentials} to the store.` : ''));
      }
      if (!eligible(item) && !imported && item.reason && (['skipped', 'conflict'].includes(item.status) || item.flags?.includes('needs-manual'))) { note(detail, item.reason); if (item.next_step) note(detail, item.next_step); }
      else if (!eligible(item) && !imported && !blocked(item) && item.flags?.includes('needs-manual')) note(detail, 'Choose the file to review it, or skip it.');
      const end = document.createElement('td'); end.className = 'r';
      row.append(cell, name, detail, end); body.append(row);
    });
    missing.forEach(error => {
      const row = document.createElement('tr'), blank = document.createElement('td'), cell = document.createElement('td'); cell.colSpan = 2;
      const text = element('span', "Couldn't find "); text.className = 'err'; note(text, displayPath(error.path), 'mono');
      const fix = element('p', `${error.agent || 'This tool'} keeps its config there by default. If you use a different path, `); fix.className = 'fix';
      const choose = element('button', 'choose the file'); choose.className = 'text-link'; choose.onclick = () => { $('manual').open = true; $('mcp-root').closest('details').open = true; $('mcp-path').value = error.path.split('/').pop(); $('mcp-root').focus(); };
      fix.append(choose, element('span', '. Otherwise skip it.')); cell.append(text, fix); row.append(blank, cell); body.append(row);
    });
    group.append(heading, grid); $('scan-items').append(group);
  }
  $('scan-title').textContent = foundTitle(state.report);
  $('scan-summary').textContent = 'We found ' + summary(found(state.report)) + '.';
  const error = missingFiles()[0]; $('skip-agent').hidden = !error; $('skip-agent').textContent = error ? `Skip ${error.agent || 'file'}` : '';
  renderScanStates(); updateSelection();
}
function chooseFile(path = '') {
  $('manual').open = true; $('mcp-root').closest('details').open = true;
  if (path) $('mcp-path').value = path.split(/[\\/]/).pop();
  $('mcp-root').focus();
}
function renderScanStates() {
  const errors = (state.report.items || []).filter(item => item.source_error && item.reason);
  $('scan-errors').replaceChildren(...errors.map(item => {
    const section = element('section', ''); section.className = 'scan-error group';
    const heading = element('h2', "Couldn't read " ); heading.append(element('code', displayPath(item.source)));
    const reason = element('p', item.reason); reason.className = 'fix err';
    const remedy = element('p', 'Check the filename, permissions and configuration syntax in your editor, then scan again. This file was left unchanged.'); remedy.className = 'fix';
    const actions = element('div', ''); actions.className = 'actions';
    const rescan = element('button', 'Rescan'); rescan.className = 'subtle'; rescan.onclick = () => action(rescan, scan);
    const choose = element('button', 'Choose another file'); choose.className = 'text-link'; choose.onclick = () => chooseFile(item.source);
    actions.append(rescan, choose); section.append(heading, reason, remedy, actions); return section;
  }));
  const empty = !state.report.items.length; $('scan-empty').hidden = !empty;
  if (empty) {
    $('scan-title').textContent = 'Nothing to import yet';
    const checked = state.report.searched || [];
    if (checked.length) {
      const details = element('details', ''); details.append(element('summary', 'Scanned locations'));
      checked.forEach(path => { const line = element('p', path); line.className = 'mono'; details.append(line); });
      $('scan-errors').append(details);
    }
  } else if (errors.length) $('scan-title').textContent = foundTitle(state.report) + `, ${plural(errors.length, 'file')} could not be read`;
}
let scanning;
export async function scan() {
  if (scanning) return scanning;
  scanning = (async () => {
    state.scanning = true;
    $('scan-title').textContent = 'Looking for skills, routines and servers';
    $('scan-summary').textContent = 'Reading local configuration. Nothing is copied until you choose what to import.'; $('scan-summary').className = 'scanning';
    $('scan-errors').replaceChildren(); $('scan-empty').hidden = true;
    $('scan-progress').hidden = false; $('scan-items').setAttribute('aria-busy', 'true');
    $('import-all').disabled = true; $('scan-again').disabled = true;
    try { state.report = await api('scan', {}); renderScan(); await refresh(); updateMeta(); }
    catch (error) { state.report = null; $('scan-items').replaceChildren(); $('scan-title').textContent = 'Scan could not finish'; $('scan-summary').textContent = 'Scan failed. Use Scan again to retry, or choose a folder or file below.'; throw error; }
    finally { state.scanning = false; updateSelection(); $('scan-summary').className = ''; $('scan-progress').hidden = true; $('scan-items').setAttribute('aria-busy', 'false'); $('scan-again').disabled = false; }
  })();
  try { await scanning; } finally { scanning = null; }
}

export function updateMeta() {
  for (const [name, kind] of [['skills', 'skill'], ['routines', 'routine'], ['mcp', 'mcp_server']]) {
    const meta = document.querySelector(`[data-meta="${name}"]`);
    if (meta) meta.textContent = state.report ? String(found(state.report).filter(item => item.kind === kind).length) : '';
  }
}
export async function importItems(ids, approved = []) {
  const chosen = new Set(ids);
  if (!window.confirm(confirmation(state.report.items.filter(item => chosen.has(item.id))))) return;
  const result = await api('scan/import', {ticket: state.report.ticket, ids, approved});
  result.imported.forEach(id => state.imported.add(id));
  if (result.imported.length) state.lastImport = new Date().toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
  await scan(); message(importResult(result.imported.length, result.rejected.length));
}
async function importSelection() {
  const ids = selected(), flagged = new Set(state.report.items.filter(risky).map(item => item.id));
  await importItems(ids, ids.filter(id => flagged.has(id)));
}
function gate(name, items, button) {
  const found = items.flatMap(item => item.review || []), lock = found.some(finding => finding.blocking);
  $(`approve-${name}`).checked = false;
  $(`approve-${name}-row`).hidden = !found.length || lock;
  $(`approve-${name}-reasons`).textContent = found.length ? found.map(finding => finding.reason).join('; ') + '.' : '';
  const update = () => { button.disabled = lock || (found.length > 0 && !$(`approve-${name}`).checked); };
  $(`approve-${name}`).onchange = update; update();
  if (lock) message('Not importable: it contains a secret. Remove it first.');
}
export function init({api, state}) {
  state.updateSelection = updateSelection;
  on('scan-again', scan);
  on('import-all', importSelection);
  on('skip-agent', () => { state.report.errors = []; renderScan(); });
  on('scan', async () => {
    delete state.tickets.skills; $('import-skills').hidden = true;
    const result = await api('skills/preview', {root: $('skill-root').value}); state.tickets.skills = result.ticket;
    $('skill-preview').className = 'card';
    $('skill-preview').replaceChildren(...result.items.map(item => {
      const label = element('label', ''); label.className = 'skill-row'; const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = true; checkbox.value = item.path;
      label.append(checkbox, element('span', item.name), element('small', item.path)); return label;
    }));
    if (!result.items.length) $('skill-preview').textContent = 'No skills found. Choose a skill folder or a folder containing skills.';
    $('import-skills').hidden = !result.items.length; gate('skills', result.items, $('import-skills'));
  });
  on('import-skills', async () => {
    const result = await api('import', {ticket: state.tickets.skills, approved: $('approve-skills').checked, selected: [...document.querySelectorAll('#skill-preview input:checked')].map(input => input.value)});
    $('import-skills').hidden = true; $('skill-preview').textContent = 'Imported'; await scan(); message(`Added ${result.imported} skill(s) to your locker.`);
  });
  async function previewRoutine(data) {
    delete state.tickets.routine; $('import-routine').hidden = true;
    const result = await api('routines/preview', data); state.tickets.routine = result.ticket;
    table('routine-preview', ['ROUTINE', 'RUNNER', 'STATUS'], result.items.map(item => [item.name, item.runner, item.status])); $('import-routine').hidden = false; gate('routine', result.items, $('import-routine'));
  }
  on('preview-routine', async () => { const text = $('routine-text').value; $('routine-text').value = ''; await previewRoutine({text}); });
  on('preview-routine-file', async () => previewRoutine({root: $('routine-root').value, path: $('routine-path').value}));
  $('routine-upload').onchange = async () => {
    const file = $('routine-upload').files[0];
    if (!file) return;
    await action($('preview-routine'), async () => { if (file.size > 1024 * 1024) throw new Error('Choose a file smaller than 1 MB.'); await previewRoutine({text: await file.text()}); });
    $('routine-upload').value = '';
  };
  on('import-routine', async () => { await api('import', {ticket: state.tickets.routine, approved: $('approve-routine').checked}); $('import-routine').hidden = true; $('routine-preview').textContent = 'Imported · disabled'; await scan(); message('Routine imported and disabled. No schedule was created.'); });
  on('preview-mcp', async () => {
    delete state.tickets.mcp; $('import-mcp').hidden = true;
    const result = await api('mcp/preview', {root: $('mcp-root').value, path: $('mcp-path').value}); state.tickets.mcp = result.ticket;
    table('mcp-preview', ['SERVER', 'TRANSPORT', 'CREDENTIALS'], result.items.map(item => [item.name, item.transport, item.credentials])); $('import-mcp').hidden = false; gate('mcp', result.items, $('import-mcp'));
  });
  on('import-mcp', async () => { const result = await api('import', {ticket: state.tickets.mcp, approved: $('approve-mcp').checked}); $('import-mcp').hidden = true; $('mcp-preview').textContent = 'Imported'; await scan(); message(`Added ${result.imported} server(s). Credentials saved privately.`); });
}
export async function render({api, state}) {
  if (!state.report) await scan(); else renderScan();
  const group = document.querySelector(`[data-scan-group="${state.scanGroup || 'skill'}"]`);
  if (group) group.open = true;
}
