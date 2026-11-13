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
const ancestors = (root, target, path = []) => root === target ? path : root.children.reduce((found, child) => found || ancestors(child, target, [...path, root]), null);
const document = {getElementById: get, createElement: tag => new Element(tag), createTextNode: value => new Element('#text', value), querySelector: get};
const calls = [];
const state = {savedAt: {}, current: 2};
const api = async (route, data) => { calls.push({route, data}); return {}; };
const context = vm.createContext({document, state, api, $: get, element: (tag, value) => new Element(tag, value), refresh: async () => {}, action: async (button, run) => run(), message() {}, Date, console});
const shared = fs.readFileSync(`${__dirname}/../ui/api.js`, 'utf8');
for (const name of ['authKind', 'pendingAuth']) vm.runInContext(shared.match(new RegExp(`^export (function ${name}.*)$`, 'm'))[1], context);
const source = fs.readFileSync(`${__dirname}/../ui/signin.js`, 'utf8').replace(/^import .*;$/gm, '').replace(/export /g, '');
vm.runInContext(`globalThis.signin = (() => { ${source}; return {renderAuth}; })()`, context);
const render = auth => { state.status = {auth}; vm.runInContext('signin.renderAuth()', context); };
const btn = label => all(get('auth')).find(node => node.tag === 'button' && node.textContent === label);
const passwords = () => all(get('auth')).filter(node => node.type === 'password');
const submit = () => {
  const form = ancestors(get('auth'), passwords()[0]).reverse().find(node => node.tag === 'form');
  let prevented = 0;
  const result = form.onsubmit({preventDefault() { prevented += 1; }});
  return {result, prevented};
};
(async () => {
  render([{name: 'beta', state: 'saved', headers: ['Authorization'], saved_at: 100}]);
  assert.match(text(get('auth')), /Token saved on this computer/);
  assert.doesNotMatch(text(get('auth')), /\.hub-store\//);

  render([{name: 'alpha', state: 'needs_signin', headers: ['Authorization']}]);
  await btn('Enter token').onclick();
  assert.equal(passwords().length, 1);
  for (const input of passwords()) {
    assert.ok(ancestors(get('auth'), input).some(node => node.tag === 'form'), 'token input has no form ancestor');
    assert.equal(input.autocomplete, 'off');
  }
  passwords()[0].value = 'fixture-token';
  calls.length = 0;
  const sent = submit();
  await sent.result;
  assert.equal(sent.prevented, 1);
  assert.deepEqual(calls.map(call => call.route), ['auth/bearer']);
  assert.equal(calls[0].data.value, 'fixture-token');

  render([{name: 'gamma', state: 'needs_signin', secrets: ['FX_API_KEY']}]);
  await btn('Set FX_API_KEY').onclick();
  assert.equal(passwords().length, 1);
  for (const input of passwords()) {
    assert.ok(ancestors(get('auth'), input).some(node => node.tag === 'form'), 'secret input has no form ancestor');
    assert.equal(input.autocomplete, 'off');
  }
  passwords()[0].value = 'fixture-secret';
  calls.length = 0;
  const secret = submit();
  await secret.result;
  assert.equal(secret.prevented, 1);
  assert.deepEqual(calls.map(call => call.route), ['auth/secret']);
  assert.equal(calls[0].data.value, 'fixture-secret');
  console.log('Sign-in forms: saved copy names no path, password inputs sit in forms, submit saves once without navigating.');
})().catch(error => { console.error(error); process.exitCode = 1; });
