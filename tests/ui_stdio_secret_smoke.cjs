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
const document = {getElementById: get, createElement: tag => new Element(tag), createTextNode: text => new Element('#text', text), querySelector: get};
const calls = [];
let failSave = false;
let confirmed = false;
const prompts = [];
const state = {savedAt: {}, current: 3};
const api = async (route, data) => { calls.push({route, data}); if (failSave) throw new Error('Save failed'); return {}; };
const context = vm.createContext({document, window: {confirm: question => { prompts.push(question); return confirmed; }}, state, api, $: get, element: (tag, text) => new Element(tag, text), refresh: async () => {}, action: async (button, run) => run(), message() {}, Date, console});
const shared = fs.readFileSync(`${__dirname}/../ui/api.js`, 'utf8');
for (const name of ['authKind', 'pendingAuth']) vm.runInContext(shared.match(new RegExp(`^export (function ${name}.*)$`, 'm'))[1], context);
const source = fs.readFileSync(`${__dirname}/../ui/signin.js`, 'utf8').replace(/^import .*;$/gm, '').replace(/export /g, '');
vm.runInContext(`globalThis.signin = (() => { ${source}; return {init, render, renderAuth}; })()`, context);
const btn = label => all(get('auth')).find(node => node.tag === 'button' && node.textContent === label);
const save = () => all(get('auth')).find(node => node.tag === 'form').onsubmit({preventDefault() {}});
const password = () => all(get('auth')).find(node => node.type === 'password');
(async () => {
  state.status = {auth: [{name: 'copied', state: 'saved', saved_at: 100, secrets: ['FX_API_KEY'], copied_from: 'Cursor'}]};
  vm.runInContext('signin.renderAuth()', context);
  assert.match(text(get('auth')), /Copied from Cursor config/);
  assert.doesNotMatch(text(get('auth')), /CLI/);
  await btn('Replace FX_API_KEY').onclick();
  password().value = 'fixture-value';
  failSave = true; await assert.rejects(save()); assert.equal(password().value, 'fixture-value');
  failSave = false; await save();
  assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1))), {route: 'auth/secret', data: {name: 'copied', key: 'FX_API_KEY', value: 'fixture-value'}});
  assert.equal(state.status.auth[0].copied_from, null);
  assert.doesNotMatch(text(get('auth')), /Copied from/);

  const before = calls.length;
  state.status = {auth: [{name: 'gone', state: 'saved', saved_at: 100, secrets: ['FX_API_KEY']}]};
  vm.runInContext('signin.renderAuth()', context);
  assert.equal(get('auth').children.length > 0 && !!btn('Replace FX_API_KEY'), true);
  await btn('Remove FX_API_KEY').onclick();
  assert.equal(prompts.length, 1);
  assert.match(prompts[0], /FX_API_KEY/);
  assert.match(prompts[0], /gone/);
  assert.equal(calls.length, before);
  assert.equal(state.status.auth[0].state, 'saved');
  confirmed = true; await btn('Remove FX_API_KEY').onclick();
  assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1))), {route: 'auth/secret/remove', data: {name: 'gone', key: 'FX_API_KEY'}});
  assert.equal(state.status.auth[0].state, 'needs_signin');
  assert.match(text(get('auth')), /Needs sign-in/);
  assert.ok(btn('Set FX_API_KEY'));
  assert.equal(btn('Remove FX_API_KEY'), undefined);
  assert.equal(get('[data-meta="signin"]').textContent, '1 need sign-in');
  confirmed = false;

  state.status = {auth: [{name: 'empty', state: 'needs_signin', secrets: ['FX_API_KEY']}]};
  vm.runInContext('signin.renderAuth()', context);
  assert.doesNotMatch(text(get('auth')), /CLI/);
  await btn('Set FX_API_KEY').onclick();
  assert.equal(password().autocomplete, 'off');
  password().value = 'typed-value'; await save();
  assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1))), {route: 'auth/secret', data: {name: 'empty', key: 'FX_API_KEY', value: 'typed-value'}});
  assert.equal(state.status.auth[0].state, 'saved');
  console.log('Stdio secrets: copied source, Set/Replace field, confirmed Remove, save failure and success passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
