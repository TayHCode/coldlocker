const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');
const nodes = new Map();
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ''; this.hidden = false; this.disabled = false; this.className = ''; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute() {}
  click() {}
  focus() {}
}
const get = id => { if (!nodes.has(id)) nodes.set(id, new Element('div')); return nodes.get(id); };
const document = {getElementById: get, createElement: tag => new Element(tag), createTextNode: text => { const node = new Element('#text'); node.textContent = text; return node; },
  querySelectorAll: () => [], querySelector: () => new Element('div')};
const replies = {
  scan: {ticket: 't', items: [{id: 'a', name: 'alpha', kind: 'skill', status: 'new', importable: true, source: '~/alpha'}]},
  status: {counts: {skills: 0, routines: 0, mcp_servers: 0, rules: 0, plugins: 0, connections: 0},
    inventory: Object.fromEntries(['skills', 'routines', 'mcp_servers', 'rules', 'plugins', 'connections'].map(kind => [kind, {imported: [], examples: 0}])), auth: [], check: 'Passed'},
  calls: {first_call: {}}
};
let releaseScan;
const scanDelay = new Promise(resolve => { releaseScan = resolve; });
const fetch = async url => {
  const route = url.replace('/api/', '');
  if (route === 'scan') await scanDelay;
  return {ok: true, json: async () => replies[route]};
};
const context = vm.createContext({document, window: {addEventListener() {}}, fetch, console});
for (const name of ['api', 'scan', 'welcome']) {
  const source = fs.readFileSync(path.join(__dirname, '../ui/' + name + '.js'), 'utf8')
    .replace(/^import .*;$/gm, '').replace(/export /g, '');
  vm.runInContext(source, context);
}
const rendering = vm.runInContext('render({api, state})', context);
const settle = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  await settle();
  assert.match(get('welcome-lede').textContent, /Scanning your computer/, 'scanning text is visible while /api/scan is in flight');
  assert.match(get('welcome-lede').className, /scanning/, 'scanning indicator styling is applied');
  assert.equal(get('welcome-import').disabled, true, 'Import all is disabled while scanning');
  assert.equal(get('welcome-discovery').hidden, true, 'Import all and Review are hidden while scanning');
  assert.equal(get('welcome-health-panel').hidden, true, 'Health heading is hidden until a result exists');
  assert.match(fs.readFileSync(path.join(__dirname, '../ui/index.html'), 'utf8'), /<aside id="welcome-health-panel"[^>]* hidden/, 'Health panel starts hidden in the page');
  releaseScan();
  await rendering;
  assert.doesNotMatch(get('welcome-lede').textContent, /Scanning/);
  assert.doesNotMatch(get('welcome-lede').className, /scanning/);
  assert.equal(get('welcome-import').disabled, false, 'Import all is enabled once results exist');
  assert.equal(get('welcome-discovery').hidden, false);
  assert.equal(get('welcome-health-panel').hidden, false, 'Health shows once a result exists');
  assert.equal(get('welcome-health').textContent.includes('hub.py'), false, 'healthy copy has no command name');
  console.log('Welcome: scanning state shown and Import all disabled until /api/scan returns.');
})().catch(error => { console.error(error); process.exit(1); });
