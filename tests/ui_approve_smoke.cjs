const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const nodes = new Map();
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ''; this.hidden = false; this.disabled = false; this.checked = false; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute() {}
  focus() {}
  scrollIntoView() {}
}
const get = id => { if (!nodes.has(id)) nodes.set(id, new Element('div')); return nodes.get(id); };
const document = {getElementById: get, createElement: tag => new Element(tag), createTextNode: text => { const node = new Element('#text'); node.textContent = text; return node; }, querySelectorAll: () => [], querySelector: () => new Element('h1')};
const calls = [];
const answer = (url, options) => {
  const route = url.replace('/api/', '');
  if (options) calls.push([route, JSON.parse(options.body)]);
  const body = route === 'mcp/preview' ? {ticket: 't', items: [{name: 'server', transport: 'stdio', credentials: 'None declared', review: JSON.parse(context.review)}]}
    : route === 'import' ? {imported: 1} : {items: [], errors: [], agents: {}};
  return Promise.resolve({ok: true, json: async () => body});
};
const context = vm.createContext({document, window: {addEventListener() {}}, fetch: answer, console});
for (const name of ['api', 'scan']) {
  const source = fs.readFileSync(path.join(__dirname, '../ui/' + name + '.js'), 'utf8').replace(/^import .*;$/gm, '').replace(/export /g, '');
  vm.runInContext(source, context);
}
vm.runInContext('init({api, state});', Object.assign(context, {review: '[]'}));
const click = async id => get(id).onclick && await get(id).onclick();
(async () => {
  context.review = JSON.stringify([{reason: 'Unpinned package', blocking: false}]);
  get('mcp-root').value = '/x'; get('mcp-path').value = 'mcp.json';
  await click('preview-mcp');
  assert.equal(get('approve-mcp-row').hidden, false);
  assert.match(get('approve-mcp-reasons').textContent, /Unpinned package/);
  assert.equal(get('import-mcp').disabled, true);
  get('approve-mcp').checked = true; get('approve-mcp').onchange();
  assert.equal(get('import-mcp').disabled, false);
  await click('import-mcp');
  assert.equal(calls.find(call => call[0] === 'import')[1].approved, true);

  context.review = JSON.stringify([{reason: 'Embedded secret', blocking: true}]);
  await click('preview-mcp');
  assert.equal(get('approve-mcp-row').hidden, true);
  assert.equal(get('approve-mcp').checked, false);
  assert.equal(get('import-mcp').disabled, true);

  context.review = '[]';
  await click('preview-mcp');
  assert.equal(get('approve-mcp-row').hidden, true);
  assert.equal(get('import-mcp').disabled, false);
  console.log('Approve checkbox gating passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
