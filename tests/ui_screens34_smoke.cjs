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
const document = {getElementById: get, createElement: tag => new Element(tag), createTextNode: text => new Element('#text', text), querySelector: selector => selector === '#connect-side #catalog-fallback' ? all(get('connect-side')).find(node => node.id === 'catalog-fallback') : get(selector)};
let copied, calls = [], failSave = false, connectResult = null;
const state = {savedAt: {}, current: 3};
const api = async (route, data) => { calls.push({route, data}); if (route === 'auth/bearer' && failSave) throw new Error('Save failed'); if (route === 'connect') return connectResult; if (route === 'calls') return {calls: []}; return {}; };
const context = vm.createContext({document, state, api, $: get, element: (tag, text) => new Element(tag, text), refresh: async () => {}, action: async (button, run) => run(), message() {}, navigator: {clipboard: {writeText: async value => { copied = value; }}}, setInterval() {}, Date, console});
const shared = fs.readFileSync(`${__dirname}/../ui/api.js`, 'utf8');
for (const name of ['authKind', 'pendingAuth']) vm.runInContext(shared.match(new RegExp(`^export (function ${name}.*)$`, 'm'))[1], context);
for (const name of ['signin', 'connect']) {
  const source = fs.readFileSync(`${__dirname}/../ui/${name}.js`, 'utf8').replace(/^import .*;$/gm, '').replace(/export /g, '');
  vm.runInContext(`globalThis.${name} = (() => { ${source}; return {init, render${name === 'signin' ? ', renderAuth' : ', renderAgent, renderCalls, renderGateway'}}; })()`, context);
}
const run = source => vm.runInContext(source, context);
const btn = label => all(get('auth')).find(node => node.tag === 'button' && node.textContent === label);
const save = () => all(get('auth')).find(node => node.tag === 'form').onsubmit({preventDefault() {}});
(async () => {
  state.status = {auth: [
    {name: 'alpha', state: 'needs_signin', headers: ['Authorization']},
    {name: 'beta', state: 'saved', headers: ['Authorization'], saved_at: 100},
    {name: 'gamma', state: 'verified', verified_at: 100, tools: 4},
    {name: 'delta', state: 'no_credentials'},
    {name: 'epsilon', state: 'check_failed', error: 'Start the gateway first'}
  ]};
  run('signin.renderAuth()');
  for (const phrase of ['Needs sign-in', 'Saved', 'Not checked yet', 'Token saved on this computer', 'Verified', 'Listed 4 tools', 'No credentials needed', 'Start the gateway first', 'Check again']) assert.ok(text(get('auth')).includes(phrase), phrase);
  state.status.auth = [state.status.auth[1]];
  run('signin.renderAuth()'); assert.doesNotMatch(text(get('auth')), /Verified/);
  await btn('Check now').onclick(); assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1))), {route: 'auth/check', data: {server: 'beta'}});
  state.status.auth[0].state = 'needs_signin'; run('signin.renderAuth()');
  await btn('Enter token').onclick();
  const input = all(get('auth')).find(node => node.type === 'password'); input.value = 'fixture-value';
  assert.equal(input.autocomplete, 'off');
  failSave = true; await assert.rejects(save()); assert.equal(input.value, 'fixture-value');
  failSave = false; await save(); assert.equal(input.value, ''); assert.match(text(get('auth')), /Token saved on this computer/); assert.doesNotMatch(text(get('auth')), /Verified/);
  state.status.auth = [{name: 'public', state: 'no_credentials'}]; run('signin.renderAuth()'); assert.equal(get('auth-note').hidden, true); assert.match(text(get('auth')), /No credentials needed/);
  state.connection = {agents: [{agent: 'codex', display_path: '~/.codex/config.toml', snippet: '[mcp_servers.agentlocker]\ncommand = "python3"'}], gateway: {status: 'stopped'}};
  run('connect.renderAgent(); connect.renderGateway()');
  assert.match(text(get('agent-config')), /# ~\/.codex\/config.toml/);
  await all(get('agent-config')).find(node => node.textContent === 'Copy').onclick(); assert.equal(copied, state.connection.agents[0].snippet);
  assert.match(text(get('connect-side')), /Not running/);
  run(`connect.renderCalls({calls:[{ts:1,client:'a',method:'initialize'},{ts:2,client:'a',method:'tools/list'},{ts:3,client:'b',method:'initialize'}],first_call:{a:{ts:1,method:'initialize'},b:{ts:3,method:'initialize'}}})`);
  assert.equal(all(get('call-log')).filter(node => node.className === 'live').length, 2);
  run(`connect.renderCalls({calls:[{ts:4,client:'a',method:'tools/list'}],first_call:{a:{ts:1,method:'initialize'}}})`);
  assert.equal(all(get('call-log')).filter(node => node.className === 'live').length, 0);
  run('connect.renderCalls({calls:[]})'); assert.match(text(get('call-log')), /Waiting for the first call/);
  run(`connect.renderCalls({calls:[],setup_checks:1,older:3,first_call:{}})`);
  assert.equal(all(get('call-log')).filter(node => node.className === 'live').length, 0);
  assert.match(text(get('call-log')), /Waiting for the first call/); assert.match(text(get('call-log')), /3 earlier calls not shown/); assert.match(text(get('call-log')), /1 setup check\b/);
  run(`connect.renderCalls({calls:[{ts:7,client:'claude-code-probe',method:'initialize'},{ts:8,client:'codex-probe',method:'initialize'}],setup_checks:20,older:0,first_call:{'claude-code-probe':{ts:7,method:'initialize'},'codex-probe':{ts:8,method:'initialize'}}})`);
  for (const phrase of ['claude-code-probe', 'codex-probe', '20 setup checks']) assert.ok(text(get('call-log')).includes(phrase), phrase);
  assert.doesNotMatch(text(get('call-log')), /Waiting for the first call/);
  run(`connect.renderCalls({calls:[{ts:6,client:'a',method:'initialize'}],first_call:{a:{ts:6,method:'initialize'}}})`);
  assert.doesNotMatch(text(get('call-log')), /Waiting for the first call/);
  const stopped = startable => ({status: 'stopped', startable, install: 'python3 gateway.py install'});
  state.status = {auth: [{name: 'beta', state: 'saved', headers: ['Authorization'], saved_at: 100}], gateway: stopped(true)};
  run('signin.renderAuth()');
  assert.match(text(get('auth')), /gateway isn't running/);
  calls.length = 0; await btn('Start gateway').onclick(); assert.deepEqual(calls.map(call => call.route), ['gateway/start']);
  calls.length = 0; await btn('Start gateway and check').onclick(); assert.deepEqual(calls.map(call => call.route), ['gateway/start', 'auth/check']);
  state.status.gateway = stopped(false); run('signin.renderAuth()');
  assert.match(text(get('auth')), /python3 gateway.py install/); assert.equal(btn('Start gateway'), undefined); assert.equal(btn('Start gateway and check'), undefined);
  state.status = {gateway: {status: 'running'}, auth: [
    {name: 'quiet', state: 'check_failed', check: 'no_tools', error: 'Checked, listed 0 tools', headers: ['Authorization']},
    {name: 'down', state: 'check_failed', check: 'unreachable', error: "The gateway isn't running.", headers: ['Authorization']}]};
  run('signin.renderAuth()');
  assert.match(text(get('auth')), /lists no tools/); assert.doesNotMatch(text(get('auth')), /listed 0 tools/);
  assert.equal(all(get('auth')).filter(node => node.textContent === 'Check again').length, 2);
  connectResult = {agents: state.connection.agents, gateway: stopped(true)};
  state.connection.gateway = stopped(true); run('connect.renderGateway()');
  assert.match(text(get('connect-state')), /can't reach your servers until the gateway is running/);
  const start = all(get('connect-side')).find(node => node.textContent === 'Start gateway');
  calls.length = 0; await start.onclick(); assert.deepEqual(calls.map(call => call.route), ['gateway/start', 'connect', 'calls']);
  state.connection.gateway = stopped(false); run('connect.renderGateway()');
  assert.match(text(get('connect-side')), /python3 gateway.py install/); assert.equal(all(get('connect-side')).find(node => node.textContent === 'Start gateway'), undefined);
  state.connection.gateway = {status: 'running', url: 'http://127.0.0.1:43871/mcp', total: 3, served: 1, up: ['up-one'], down: [{name: 'down-one', reason: 'Needs sign-in'}, {name: 'down-two', reason: 'Lists no tools through the gateway', next_step: 'Run Check now on Sign in.'}]};
  run('connect.renderGateway()');
  for (const phrase of ['Serves 1 of 3 servers', 'up-one', 'down-one', 'Needs sign-in', 'down-two', 'Lists no tools through the gateway', 'Run Check now on Sign in.']) assert.ok(text(get('connect-side')).includes(phrase), phrase);
  assert.equal(all(get('connect-side')).filter(node => node.tag === 'li').length, 3);
  assert.match(text(get('connect-side')), /Read-only catalog/);
  assert.doesNotMatch(text(get('connect-state')), /every server/); assert.match(text(get('connect-state')), /serves 1 of 3 servers/i);
  state.connection.gateway = {status: 'running', url: 'http://127.0.0.1:43871/mcp', total: 3, served: 3, up: ['a', 'b', 'c'], down: []};
  run('connect.renderGateway()');
  assert.equal(all(get('connect-side')).filter(node => node.tag === 'li').length, 3); assert.match(text(get('connect-state')), /serves 3 of 3 servers/i);
  state.connection.gateway = {status: 'running', url: 'http://127.0.0.1:43871/mcp', total: 2, served: 0, up: [], down: [{name: 'a', reason: 'Needs sign-in'}, {name: 'b', reason: 'Needs sign-in'}]};
  run('connect.renderGateway()');
  assert.doesNotMatch(text(get('connect-state')), /every server/); assert.match(text(get('connect-state')), /no servers yet/i);
  console.log('Screens 3/4: state words, save failure/success, live check, copy, first calls and empty state passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
