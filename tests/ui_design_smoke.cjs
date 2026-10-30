const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Element {
  constructor() { this.attrs = {}; this.hidden = false; this.dataset = {}; this.classes = new Set(); this.classList = {remove: name => this.classes.delete(name), toggle: (name, on) => on ? this.classes.add(name) : this.classes.delete(name)}; }
  setAttribute(key, value) { this.attrs[key] = value; }
  getAttribute(key) { return this.attrs[key]; }
  removeAttribute(key) { delete this.attrs[key]; }
  focus() { document.activeElement = this; }
  scrollIntoView() {}
  querySelector() { return this.button; }
}
const nodes = new Map();
const get = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
const groups = ['skill', 'routine', 'mcp_server'];
const buttons = [0, 1, 1, 1, 2, 3].map((step, index) => { const button = new Element(); button.dataset = {go: String(step), group: groups[index - 1]}; return button; });
const nav = buttons.map(button => { const item = new Element(); item.button = button; return item; });
const sections = Array.from({length: 4}, () => new Element());
const document = {documentElement: get('html'), querySelectorAll: selector => ({'[data-screen]': sections, '[data-nav]': nav, '[data-go]': buttons})[selector] || [], querySelector: selector => get(selector)};
const state = {current: 0};
const renders = [];
const screen = name => ({init() {}, async render() { renders.push(name); }});
const storage = new Map([['agentlocker-theme', 'light']]);
const context = vm.createContext({document, state, $: get, api: async () => ({token: 'fixture'}), message() {}, action: async (button, run) => run(), on: (id, run) => { get(id).onclick = run; }, localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)}, window: {addEventListener() {}}, welcome: screen('welcome'), scan: screen('scan'), signin: screen('signin'), connect: screen('connect')});
vm.runInContext(fs.readFileSync(`${__dirname}/../ui/app.js`, 'utf8').replace(/^import .*;$/gm, '').replace(/export /g, ''), context);
(async () => {
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(state.token, 'fixture');
  assert.equal(get('progress').textContent, 'Welcome · 1 of 6');
  assert.equal(get('html').dataset.theme, 'light');
  for (const index of [1, 2, 3]) {
    await buttons[index].onclick();
    assert.equal(state.current, 1);
    assert.equal(state.scanGroup, groups[index - 1]);
    assert.equal(nav[index].button.attrs['aria-current'], 'step');
    assert.equal(nav.filter(item => item.button.attrs['aria-current']).length, 1);
  }
  assert.equal(renders.filter(name => name === 'scan').length, 3);
  await get('next').onclick(); assert.equal(state.current, 2); assert.equal(get('progress').textContent, 'Sign in · 5 of 6');
  await get('next').onclick(); assert.equal(state.current, 3); assert.equal(get('next').hidden, true);
  await get('back').onclick(); assert.equal(state.current, 2);
  get('progress').onclick(); assert.equal(get('progress').attrs['aria-expanded'], 'true'); assert.ok(get('nav').classes.has('menu-open'));
  get('progress').onclick(); assert.equal(get('progress').attrs['aria-expanded'], 'false');
  get('theme-toggle').onclick(); assert.equal(storage.get('agentlocker-theme'), 'system');
  get('theme-toggle').onclick(); assert.equal(storage.get('agentlocker-theme'), 'dark');
  assert.equal(get('html').dataset.theme, 'dark');
  console.log('Six destinations share four screens, current labels and focus update, mobile navigation toggles, theme persists.');
})().catch(error => { console.error(error); process.exitCode = 1; });
