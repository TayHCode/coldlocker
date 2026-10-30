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
const calls = [], messages = [], opened = [];
let discovery = {};
let oauthUrl = 'https://provider.example/authorize?state=fixture';
const state = {savedAt: {}, current: 2};
const api = async (route, data) => {
  calls.push({route, data});
  if (route === 'auth/discover') return {oauth: discovery[data.name] === true};
  if (route === 'auth/oauth') return {url: oauthUrl};
  return {};
};
const consent = {location: '', close() {}};
const window = {open: (url, target) => { opened.push({url, target}); return consent; }};
const context = vm.createContext({document, window, state, api, $: get, element: (tag, value) => new Element(tag, value), refresh: async () => {}, action: async (button, run) => run(), message: (value, error) => messages.push({value, error: !!error}), Date, console});
const shared = fs.readFileSync(`${__dirname}/../ui/api.js`, 'utf8');
for (const name of ['authKind', 'pendingAuth']) vm.runInContext(shared.match(new RegExp(`^export (function ${name}.*)$`, 'm'))[1], context);
const source = fs.readFileSync(`${__dirname}/../ui/signin.js`, 'utf8').replace(/^import .*;$/gm, '').replace(/export /g, '');
vm.runInContext(`globalThis.signin = (() => { ${source}; return {init, render, renderAuth, discover}; })()`, context);
const run = value => vm.runInContext(value, context);
const labels = () => all(get('auth')).filter(node => node.tag === 'button').map(node => node.textContent);
const btn = label => all(get('auth')).find(node => node.tag === 'button' && node.textContent === label);
const server = name => ({name, state: 'needs_signin', status: 'needs-auth', headers: ['Authorization'], refreshable: false});
(async () => {
  discovery = {'fx-oauth': true, 'fx-http-bearer': false, 'loopback-remote': false};
  state.status = {auth: [server('fx-oauth'), server('fx-http-bearer'), server('loopback-remote')]};
  await run('signin.discover()');
  run('signin.renderAuth()');
  const rows = all(get('auth')).filter(node => node.tag === 'tr').slice(1);
  const row = name => rows.find(node => text(node).includes(name));
  const rowLabels = name => all(row(name)).filter(node => node.tag === 'button').map(node => node.textContent);

  assert.deepEqual(rowLabels('fx-oauth').slice(0, 1), ['Sign in to fx-oauth']);
  assert.ok(rowLabels('fx-oauth').includes('Enter token'));
  assert.deepEqual(rowLabels('fx-http-bearer'), ['Enter token']);
  assert.deepEqual(rowLabels('loopback-remote'), ['Enter token']);
  assert.doesNotMatch(text(row('fx-http-bearer')), /Sign in with|OAuth client/);
  assert.doesNotMatch(text(row('loopback-remote')), /Sign in with|OAuth client/);
  assert.match(text(row('fx-http-bearer')), /OAuth sign-in was not discovered/);
  assert.match(text(row('loopback-remote')), /OAuth sign-in was not discovered/);
  assert.doesNotMatch(text(get('auth')), /Fx-|Loopback-/);

  state.status.auth = [{...server('refreshable'), refreshable: true}];
  run('signin.renderAuth()');
  assert.ok(labels().includes('Sign in to refreshable'));

  state.status.auth = [server('fx-oauth')];
  run('signin.renderAuth()');
  await btn('Sign in to fx-oauth').onclick();
  assert.deepEqual(opened.at(-1), {url: 'about:blank', target: '_blank'});
  assert.equal(consent.location, oauthUrl);
  assert.match(text(get('auth')), /Waiting for approval in the consent tab/);
  assert.match(messages.at(-1).value, /consent tab/);

  state.status.auth = [{...server('fx-oauth'), state: 'saved', status: 'connected', saved_at: 100}];
  run('signin.renderAuth()');
  assert.doesNotMatch(text(get('auth')), /Waiting for approval/);
  assert.match(messages.at(-1).value, /Signed in to fx-oauth/);
  assert.doesNotMatch(text(get('auth')), /Fx-oauth/);
  console.log('Sign-in kinds: OAuth only when discovery succeeds, token otherwise, ids as written, pending and complete feedback passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
