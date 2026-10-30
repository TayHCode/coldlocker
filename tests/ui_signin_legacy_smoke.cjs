const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const nodes = new Map();
class Element {
  constructor(tag, text = '') { this.tag = tag; this.textContent = text; this.children = []; this.attrs = {}; this.value = ''; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.textContent = ''; this.children = children; }
  setAttribute(key, value) { this.attrs[key] = value; }
  focus() {}
}
const get = id => { if (!nodes.has(id)) nodes.set(id, new Element('div')); return nodes.get(id); };
const all = node => [node, ...node.children.flatMap(all)];
const text = node => node.textContent + node.children.map(text).join(' ');
const document = {getElementById: get, createElement: tag => new Element(tag), createTextNode: value => new Element('#text', value), querySelector: get};
const state = {savedAt: {}, current: 2};
const api = async () => ({});
const context = vm.createContext({document, state, api, $: get, element: (tag, value) => new Element(tag, value), refresh: async () => {}, action: async (button, run) => run(), message() {}, Date, console});
const shared = fs.readFileSync(`${__dirname}/../ui/api.js`, 'utf8');
for (const name of ['authKind', 'pendingAuth']) vm.runInContext(shared.match(new RegExp(`^export (function ${name}.*)$`, 'm'))[1], context);
const source = fs.readFileSync(`${__dirname}/../ui/signin.js`, 'utf8').replace(/^import .*;$/gm, '').replace(/export /g, '');
vm.runInContext(`globalThis.signin = (() => { ${source}; return {renderAuth}; })()`, context);
const render = auth => { state.status = {auth}; vm.runInContext('signin.renderAuth()', context); };
const buttons = node => all(node).filter(item => item.tag === 'button').map(item => item.textContent);
const legacy = {name: 'fx-legacy', state: 'check_failed', label: 'Check failed', description: 'stdio', legacy_credential: true,
  detail: 'stored copy has a credential in its args', error: 'stored copy has a credential in its args. Run python3 hub.py scan --import',
  next_step: 'python3 hub.py scan --import'};
const needs = {name: 'fx-needs', state: 'needs_signin', status: 'needs-auth', headers: ['Authorization']};
const failed = {name: 'fx-failed', state: 'check_failed', description: 'stdio', error: 'Live tool check failed'};
const rowOf = name => all(get('auth')).filter(node => node.tag === 'tr').find(node => text(node).includes(name));
(async () => {
  render([legacy, needs, failed]);
  const row = rowOf('fx-legacy');
  assert.match(text(row), /Cannot be used/);
  assert.match(text(row), /Stored copy has a credential in its args/);
  assert.doesNotMatch(text(row), /stored copy has a credential in its args\. Run/);
  assert.ok(all(row).some(node => /\bfailing\b/.test(node.className || '')), 'legacy row has no failing marker');
  const code = all(row).find(node => node.tag === 'code');
  assert.ok(code, 'command is not in a code element');
  assert.equal(code.textContent, 'python3 hub.py scan --import');
  assert.ok(/\bcmd\b/.test(code.className || ''), 'command code element is not the non-wrapping class');
  assert.deepEqual(buttons(row), []);
  assert.deepEqual(buttons(rowOf('fx-failed')), ['Check again']);
  assert.match(get('auth-note').textContent, /sign in to fx-legacy, fx-needs later/);
  assert.equal(get('auth-note').hidden, false);
  assert.equal(get('[data-meta="signin"]').textContent, '2 need sign-in');

  render([legacy]);
  assert.equal(get('[data-meta="signin"]').textContent, '1 need sign-in');
  assert.match(get('auth-note').textContent, /fx-legacy/);

  render([failed]);
  assert.equal(get('[data-meta="signin"]').textContent, '0 verified');
  console.log('Legacy credential row: failing marker, capitalised sentence, command in code, no Check again, counted in nav and footer.');
})().catch(error => { console.error(error); process.exitCode = 1; });
