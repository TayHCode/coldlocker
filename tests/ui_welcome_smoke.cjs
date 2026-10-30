const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');
const nodes = new Map();
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ''; this.hidden = false; this.className = ''; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute() {}
  click() {}
  focus() {}
}
const get = id => { if (!nodes.has(id)) nodes.set(id, new Element('div')); return nodes.get(id); };
const document = {getElementById: get, createElement: tag => new Element(tag), createTextNode: text => { const node = new Element('#text'); node.textContent = text; return node; },
  querySelectorAll: () => [], querySelector: () => new Element('div')};
const context = vm.createContext({document, window: {addEventListener() {}}, fetch: () => new Promise(() => {}), console});
for (const name of ['api', 'scan', 'welcome']) {
  const source = fs.readFileSync(path.join(__dirname, '../ui/' + name + '.js'), 'utf8')
    .replace(/^import .*;$/gm, '').replace(/export /g, '');
  vm.runInContext(source, context);
}
const all = node => [node, ...node.children.flatMap(all)];
const mcpRow = () => get('welcome-summary').children.find(row => all(row).some(n => n.textContent === 'MCP servers'));
const labels = () => all(mcpRow()).filter(n => n.tag === 'button').map(n => n.textContent);
const render = (servers, auth) => {
  vm.runInContext(`state.status = {counts: {skills: 0, routines: 0, mcp_servers: ${servers}, rules: 0, plugins: 0, connections: 0}, inventory: {skills: {imported: [], examples: 0}, routines: {imported: [], examples: 0}, mcp_servers: {imported: [], examples: ${servers}}, rules: {imported: [], examples: 0}, plugins: {imported: [], examples: 0}, connections: {imported: [], examples: 0}}, auth: ${JSON.stringify(auth)}, check: 'Passed'};
    state.report = {items: []}; state.calls = {first_call: {}}; state.imported = new Set(); state.checkedAt = '9:00 AM'; inventory();`, context);
};
render(0, []);
assert.deepEqual(labels(), [], 'no Sign in button when there are no servers');
render(2, [{name: 'alpha', status: 'connected', state: 'verified'}, {name: 'beta', status: 'No credentials declared', state: 'no_credentials'}]);
assert.deepEqual(labels(), [], 'no Sign in button when nothing needs sign-in');
render(2, [{name: 'alpha', status: 'needs-auth', state: 'needs_signin'}, {name: 'beta', status: 'connected', state: 'verified'}]);
assert.deepEqual(labels(), ['Sign in'], 'Sign in button appears when a server needs sign-in');
vm.runInContext(`state.status = {counts: {skills: 0, routines: 1, mcp_servers: 0, rules: 0, plugins: 0, connections: 0}, inventory: {skills: {imported: [], examples: 0}, routines: {imported: ['cron-1', 'old'], examples: 0, details: {'cron-1': {runner: 'cron', schedule: {type: 'cron', expression: '0 9 * * 1'}, timezone: 'America/Chicago'}}}, mcp_servers: {imported: [], examples: 0}, rules: {imported: [], examples: 0}, plugins: {imported: [], examples: 0}, connections: {imported: [], examples: 0}}, auth: [], check: 'Passed'};
  state.report = {items: []}; state.calls = {first_call: {}}; state.imported = new Set(); state.checkedAt = '9:00 AM'; inventory();`, context);
const routineRow = get('welcome-summary').children.find(row => all(row).some(n => n.textContent === 'Routines'));
assert.ok(all(routineRow).some(n => n.textContent.includes('cron-1 (cron · 0 9 * * 1 · America/Chicago · imported disabled), old')), 'Welcome routine row shows runner, schedule and timezone');
console.log('Welcome: Sign in action appears only when a server needs sign-in.');
