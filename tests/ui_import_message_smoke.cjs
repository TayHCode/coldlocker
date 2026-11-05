const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');
const nodes = new Map();
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ''; this.hidden = false; this.className = ''; this.scrolled = 0; }
  append(...kids) { this.children.push(...kids); }
  replaceChildren(...kids) { this.children = kids; }
  setAttribute() {}
  focus() {}
  scrollIntoView() { this.scrolled += 1; }
}
const get = id => { if (!nodes.has(id)) nodes.set(id, new Element('div')); return nodes.get(id); };
const all = node => [node, ...node.children.flatMap(all)];
const document = {getElementById: get, createElement: tag => new Element(tag), querySelectorAll: selector => selector === '#scan-items input:checked' ? all(get('scan-items')).filter(n => n.tag === 'input' && n.checked) : [], querySelector: () => new Element('h1')};
const posted = [];
const prompts = [];
let answer = true;
let rejected = 0;
let heldScan = null;
const items = [
  {id: 's1', name: 'one', kind: 'skill', status: 'new', importable: true, source: '~/one'},
  {id: 's2', name: 'two', kind: 'skill', status: 'new', importable: true, source: '~/two'},
  {id: 'r1', name: 'routine', kind: 'routine', status: 'new', importable: true, source: '~/routine.json'},
  {id: 'm1', name: 'server', kind: 'mcp_server', status: 'new', importable: true, source: '~/mcp.json'}
];
const fetch = async (url, init) => {
  const route = url.replace('/api/', '');
  if (route === 'scan') { if (heldScan) await heldScan; return {ok: true, json: async () => ({ticket: 't', items, errors: []})}; }
  if (route === 'status') return {ok: true, json: async () => ({counts: {skills: 0, routines: 0, mcp_servers: 0}, auth: []})};
  const body = JSON.parse(init.body);
  posted.push(body.ids);
  return {ok: true, json: async () => ({imported: body.ids.slice(rejected), rejected: body.ids.slice(0, rejected)})};
};
const window = {addEventListener() {}, confirm: text => { prompts.push(text); return answer; }};
const context = vm.createContext({document, window, fetch, console});
for (const name of ['api', 'scan']) {
  const source = fs.readFileSync(path.join(__dirname, '../ui/' + name + '.js'), 'utf8').replace(/^import .*;$/gm, '').replace(/export /g, '');
  vm.runInContext(source, context);
}
const run = code => vm.runInContext(code, context);

assert.equal(run("importResult(1, 0)"), 'Imported 1 item. 0 could not be imported.');
assert.equal(run("importResult(18, 1)"), 'Imported 18 items. 1 could not be imported.');
assert.equal(run("confirmation([{kind:'skill'}])"), 'Import 1 skill?');
assert.equal(run("confirmation([{kind:'skill'},{kind:'skill'},{kind:'routine'},{kind:'mcp_server'}])"), 'Import 2 skills, 1 routine and 1 MCP server?');

(async () => {
  run("init({api, state})");
  await run("scan()");
  assert.equal(get('import-all').textContent, 'Import 4 items');
  let releaseScan; heldScan = new Promise(resolve => { releaseScan = resolve; });
  const scanning = run('scan()');
  await new Promise(resolve => setImmediate(resolve));
  all(get('scan-items')).find(node => node.tag === 'input').onchange();
  assert.equal(get('import-all').disabled, true, 'selection changes cannot enable import while a scan is in flight');
  releaseScan(); await scanning; heldScan = null;
  assert.equal(get('import-all').disabled, false, 'fresh selections become available after the scan finishes');

  answer = false;
  await get('import-all').onclick();
  assert.equal(prompts.at(-1), 'Import 2 skills, 1 routine and 1 MCP server?');
  assert.deepEqual(posted, [], 'declining the confirmation must not import');
  assert.equal(get('message').textContent, '');

  answer = true;
  await get('import-all').onclick();
  assert.deepEqual(posted, [['s1', 's2', 'r1', 'm1']]);
  assert.equal(get('message').textContent, 'Imported 4 items. 0 could not be imported.');
  assert.ok(get('message').scrolled > 0, 'the result message must be scrolled into view');

  const before = get('message').scrolled;
  run("state.imported.clear()");
  await run("scan()");
  [...all(get('scan-items'))].filter(n => n.tag === 'input' && n.value !== 's1').forEach(n => { n.checked = false; n.onchange(); });
  await get('import-all').onclick();
  assert.equal(prompts.at(-1), 'Import 1 skill?');
  assert.equal(get('message').textContent, 'Imported 1 item. 0 could not be imported.');
  assert.ok(get('message').scrolled > before);
  run("state.imported.clear()");
  items.push({id: 'review', name: 'review', kind: 'mcp_server', status: 'needs_review', importable: false, source: '~/review'});
  await run('scan()');
  const checkbox = id => all(get('scan-items')).find(node => node.tag === 'input' && node.value === id);
  const choose = (id, checked) => { const input = checkbox(id); input.checked = checked; input.onchange(); };
  choose('s1', false);
  choose('review', true);
  for (const group of ['routine', 'mcp_server', 'skill']) {
    run(`state.scanGroup = '${group}'`);
    await run('render({api, state})');
    assert.equal(checkbox('s1').checked, false, 'navigation must preserve explicit opt-outs');
    assert.equal(checkbox('review').checked, true, 'navigation must preserve reviewed opt-ins');
    assert.equal(get('import-all').textContent, 'Import 4 items');
  }
  await get('import-all').onclick();
  assert.deepEqual(posted.at(-1), ['s2', 'r1', 'm1', 'review'], 'import must use the choices made before navigation');
  await run('render({api, state})');
  for (const id of posted.at(-1)) {
    assert.equal(checkbox(id).checked, false, 'imported items must remain excluded');
    assert.equal(checkbox(id).disabled, true);
  }
  run('state.imported.clear()');
  await run('scan()');
  assert.equal(checkbox('s1').checked, true, 'a fresh scan initializes new default selections');
  assert.equal(checkbox('review').checked, false, 'review approval must not carry into a new scan');
  console.log('Navigation preserves scan choices; fresh scans reset approvals and imported rows stay excluded.');
  console.log('Import confirmation, singular/plural result and scroll-into-view passed.');
})().catch(error => { console.error(error); process.exit(1); });
