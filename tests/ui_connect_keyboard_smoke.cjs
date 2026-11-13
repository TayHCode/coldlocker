const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Element {
  constructor(tag, text = '') { this.tag = tag; this.textContent = text; this.children = []; this.attrs = {}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(key, value) { this.attrs[key] = value; }
  focus() { document.activeElement = this; }
}
const nodes = new Map();
const all = node => [node, ...node.children.flatMap(all)];
const get = id => {
  const dynamic = [...nodes.values()].flatMap(all).find(node => node.id === id);
  if (dynamic) return dynamic;
  if (!nodes.has(id)) nodes.set(id, new Element('div'));
  return nodes.get(id);
};
const document = {createElement: tag => new Element(tag), createTextNode: text => new Element('#text', text), querySelector: () => all(get('connect-side')).find(node => node.id === 'catalog-fallback')};
const state = {connection: {agents: ['codex', 'cursor', 'other'].map(agent => ({agent, label: agent, display_path: '<fixture>/' + agent, snippet: agent + '-config', catalog_snippet: agent + '-catalog'}))}};
const context = vm.createContext({document, state, $: get, element: (tag, text) => new Element(tag, text)});
vm.runInContext(fs.readFileSync(`${__dirname}/../ui/connect.js`, 'utf8').replace(/^import .*;$/gm, '').replace(/export /g, ''), context);
const render = () => vm.runInContext('renderAgent()', context);
const tabs = () => get('agent-tabs').children;
render();
const original = tabs()[0]; original.onclick();
assert.notEqual(tabs()[0], original);
assert.equal(document.activeElement, tabs()[0], 'click preserves focus on the new tab node');
let prevented = 0;
const press = key => document.activeElement.onkeydown({key, preventDefault() { prevented += 1; }});
press('ArrowRight'); assert.equal(state.connectAgent, 'cursor'); assert.equal(document.activeElement, tabs()[1]);
assert.equal(get('agent-config').attrs['aria-labelledby'], 'agent-tab-cursor');
assert.ok(all(get('catalog-fallback')).some(node => node.textContent === 'cursor-catalog'));
press('End'); assert.equal(state.connectAgent, 'other');
press('ArrowRight'); assert.equal(state.connectAgent, 'codex');
press('ArrowLeft'); assert.equal(state.connectAgent, 'other');
press('Home'); assert.equal(state.connectAgent, 'codex');
assert.equal(tabs().filter(tab => tab.tabIndex === 0).length, 1);
assert.equal(prevented, 5);
console.log('Client tabs retain click focus; arrow, Home and End keys update selection, panel and catalog fallback.');
