const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');
const nodes = new Map();
class Element {
  constructor(tag, text = '') { this.tag = tag; this.children = []; this.textContent = text; this.hidden = false; this.className = ''; this.attrs = {}; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute(key, value) { this.attrs[key] = value; }
  click() {}
  focus() {}
}
const get = id => { if (!nodes.has(id)) nodes.set(id, new Element('div')); return nodes.get(id); };
const document = {getElementById: get, createElement: tag => new Element(tag), createTextNode: text => new Element('#text', text),
  querySelectorAll: () => [], querySelector: selector => get(selector)};
const context = vm.createContext({document, window: {addEventListener() {}}, fetch: () => new Promise(() => {}), console});
for (const name of ['api', 'scan', 'signin', 'welcome']) {
  const source = fs.readFileSync(path.join(__dirname, '../ui/' + name + '.js'), 'utf8')
    .replace(/^import .*;$/gm, '').replace(/export /g, '');
  vm.runInContext(source, context);
}
const run = source => vm.runInContext(source, context);
const all = node => [node, ...node.children.flatMap(all)];
const text = node => all(node).map(n => n.textContent).join(' ');
const row = title => get('welcome-summary').children.find(tr => all(tr).some(n => n.textContent === title));

const items = [];
const add = (count, kind, status, importable, prefix = kind) => { for (let i = 0; i < count; i++) items.push({id: `${prefix}-${status}-${i}`, name: `${prefix}-${status}-${i}`, kind, status, importable, source: '~/x'}); };
add(4, 'skill', 'new', true); add(8, 'skill', 'already_in_locker', false);
add(8, 'mcp_server', 'new', true); add(5, 'mcp_server', 'already_in_locker', false);
add(6, 'connection', 'connect', true); add(1, 'rule', 'skipped', false);
items.push({id: 'dup', name: 'dup', kind: 'mcp_server', status: 'duplicate', importable: false, duplicate_of: 'mcp_server-new-0', source: '~/y'});
const auth = [
  {name: 'a', state: 'needs_signin', headers: ['Authorization']}, {name: 'b', state: 'needs_signin', headers: ['Authorization']},
  {name: 'c', state: 'needs_signin', headers: ['Authorization']}, {name: 'd', state: 'verified', verified_at: 1},
  {name: 'e', state: 'no_credentials'}
];
const status = {
  counts: {skills: 8, routines: 1, mcp_servers: 9, rules: 2, plugins: 2, connections: 1},
  inventory: {
    skills: {imported: ['s1', 's2', 's3', 's4', 's5'], examples: 3},
    routines: {imported: [], examples: 1},
    mcp_servers: {imported: ['m1', 'm2', 'm3', 'm4', 'm5', 'm6', 'm7', 'm8'], examples: 1},
    rules: {imported: ['r1'], examples: 1},
    plugins: {imported: ['p1', 'p2'], examples: 0},
    connections: {imported: ['c1'], examples: 0}
  },
  auth, check: 'Passed'
};
run(`state.status = ${JSON.stringify(status)}; state.report = {items: ${JSON.stringify(items)}}; state.calls = {first_call: {}}; state.checkedAt = '9:00 AM';
  state.imported = new Set(); renderScan(); inventory(); state.renderAuth = renderAuth; renderAuth();`);

assert.equal(get('scan-title').textContent, 'Found 32 items on this computer');
assert.match(get('scan-summary').textContent, /^We found 12 skills, 0 routines, 13 MCP servers, 1 rule and 6 plugins\/connections\.$/);
assert.equal(get('[data-meta="skills"]').textContent, '12');
assert.ok(get('welcome-found').textContent.startsWith('Found 32 items on this computer.'), get('welcome-found').textContent);
assert.ok(get('welcome-found').textContent.includes('4 skills, 0 routines, 8 MCP servers, 0 rules and 6 plugins/connections are new and ready to import'));

assert.equal(get('welcome-title').textContent, 'Your locker has 23 things in it');
assert.ok(get('welcome-lede').textContent.includes('Includes 6 built-in examples'), get('welcome-lede').textContent);
assert.ok(get('welcome-lede').textContent.includes('3 servers need a sign-in'), get('welcome-lede').textContent);
const skills = text(row('Skills'));
assert.ok(skills.includes('s1, s2, s3, s4, s5'), skills);
assert.ok(skills.includes('3 built-in examples'), skills);
assert.equal(all(row('Skills'))[3].textContent, ' · 8');
assert.ok(text(row('MCP servers')).includes('1 verified, 0 saved but not checked, 3 need sign-in'));
assert.ok(text(row('Routines')).includes('1 built-in example'));
assert.ok(!text(row('Routines')).includes('1 built-in examples'));
const plugins = row('Plugins and connections');
assert.equal(all(plugins)[3].textContent, ' · 3');
assert.ok(text(plugins).includes('p1, p2, c1'), text(plugins));
const listed = get('welcome-summary').children.filter(tr => !all(tr).some(n => n.textContent === 'Agent clients seen')).reduce((sum, tr) => sum + Number(all(tr)[3].textContent.replace(' · ', '')), 0);
assert.equal(listed, 23);
assert.equal(get('[data-meta="signin"]').textContent, '3 need sign-in');
assert.ok(text(get('auth')).split('Needs sign-in').length - 1 === 3);

run("state.status.auth = state.status.auth.map(i => i.name === 'a' ? {...i, state: 'saved'} : i); renderAuth(); inventory();");
assert.equal(get('[data-meta="signin"]').textContent, '2 need sign-in');
assert.ok(get('welcome-lede').textContent.includes('2 servers need a sign-in'));
console.log('Found, locker, example and sign-in counts agree across screens.');
