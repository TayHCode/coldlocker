const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const nodes = new Map();
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ''; this.hidden = false; this.classList = {add() {}}; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute() {}
  focus() {}
}
const get = id => { if (!nodes.has(id)) nodes.set(id, new Element('div')); return nodes.get(id); };
const document = {getElementById: get, createElement: tag => new Element(tag), createTextNode: text => { const node = new Element('#text'); node.textContent = text; return node; }, querySelectorAll: selector => {
  if (selector === '#scan-items input:checked') {
    const all = node => [node, ...node.children.flatMap(all)];
    return all(get('scan-items')).filter(n => n.tag === 'input' && n.checked);
  }
  return [];
}, querySelector: selector => selector === '[data-meta="skills"]' ? get('scan-meta') : new Element('h1')};
const context = vm.createContext({document, window: {addEventListener() {}}, fetch: () => new Promise(() => {}), console});
for (const name of ['api', 'scan']) {
  const source = fs.readFileSync(require('node:path').join(__dirname, '../ui/' + name + '.js'), 'utf8')
    .replace(/^import .*;$/gm, '').replace(/export /g, '');
  vm.runInContext(source, context);
}
vm.runInContext('init({api, state});', context);
vm.runInContext(`
state.report = {agents: {claude: {}, codex: {}}, items: [
 {id:'a', name:'<img onerror=alert(1)>', agent:'claude', kind:'skill', status:'new', importable:true, source:'~/skill'},
 {id:'b', name:'copy', agent:'codex', kind:'skill', status:'duplicate', importable:false, duplicate_of:'a', source:'~/copy'},
 {id:'c', name:'plugin', agent:'codex', kind:'plugin', status:'vendor', importable:false, source:'~/vendor'},
 {id:'d', name:'live', agent:'claude', kind:'connection', status:'connect', importable:true, upstream:{kind:'executor'}, source:'~/live'}]};
renderScan();
`, context);
assert.equal(get('scan-items').children.length, 3);
const everything = node => [node, ...node.children.flatMap(everything)];
const agentGroups = () => everything(get('scan-items')).filter(n => n.tag === 'details');
assert.deepEqual(agentGroups().map(n => n.children[0].textContent), ['Skills · 1 found', 'Plugins · 1 found', 'Connections · 1 found']);
assert.ok(agentGroups().every(n => n.children[0].tag === 'summary' && n.open));
assert.ok(agentGroups().every(n => everything(n).some(c => c.tag === 'input')));
const rowKinds = n => everything(n).filter(c => c.className === 'kind').map(c => c.textContent);
assert.deepEqual(rowKinds(agentGroups()[0]), ['Claude Code', 'Codex']);
vm.runInContext("state.status = {counts: {skills: 3, routines: 1, mcp_servers: 2}}; updateMeta();", context);
assert.equal(get('scan-meta').textContent, '1');
assert.equal(vm.runInContext('selected().length', context), 2);
vm.runInContext("state.imported.add('a'); renderScan();", context);
assert.equal(vm.runInContext('selected().length', context), 1);
assert.equal(get('import-all').textContent, 'Import 1 item');
const all = node => [node, ...node.children.flatMap(all)];
const text = () => all(get('scan-items')).map(n => n.textContent).join(' ');
vm.runInContext(`state.report.items.push(
 {id:'e',name:'existing',kind:'skill',status:'already_in_locker',importable:false,source:'~/existing'},
 {id:'f',name:'changed',kind:'skill',status:'changed',importable:true,source:'~/changed'},
 {id:'g',name:'routine',kind:'routine',status:'new',importable:true,source:'~/routine.json',enabled_in_file:true},
 {id:'h',name:'paused',kind:'routine',status:'new',importable:true,source:'~/paused.json',enabled_in_file:false});
state.report.errors = [{path:'~/.config/sample/config.json',status:'missing',agent:'Sample'}]; renderScan();`, context);
for (const word of ['Already in the locker', 'Changed since last import', 'Imports paused', 'Enabled in the file. Imports paused; nothing runs until you enable it.', 'Disabled in the file.', "Couldn't find", 'config.json', 'Otherwise skip it.']) assert.ok(text().includes(word), word);
assert.ok(!text().includes('account'));
vm.runInContext(`state.report.items.push(
 {id:'c1',name:'cron-1',agent:'system',kind:'routine',status:'new',importable:true,source:'<system:crontab>',runner:'cron',schedule:{type:'cron',expression:'0 9 * * 1'},timezone:'America/Chicago'},
 {id:'c2',name:'cron-2',agent:'system',kind:'routine',status:'already_in_locker',importable:false,source:'<system:crontab>',runner:'cron',schedule:{type:'cron',expression:'0 9 * * 1'},timezone:'America/Chicago'},
 {id:'c3',name:'timer',agent:'system',kind:'routine',status:'new',importable:true,source:'~/timer',runner:'systemd-timer',schedule:{type:'fixed_time',time_of_day:'09:00',weekdays:[1,3]},timezone:'UTC'},
 {id:'c4',name:'poll',agent:'system',kind:'routine',status:'new',importable:true,source:'~/poll',runner:'t3-scheduler',schedule:{type:'interval',every_seconds:3600},timezone:'UTC'},
 {id:'c5',name:'hook',agent:'system',kind:'routine',status:'new',importable:true,source:'~/hook',runner:'n8n',schedule:{type:'webhook'},timezone:'UTC'}); renderScan();`, context);
for (const word of ['cron · 0 9 * * 1 · America/Chicago · imported disabled', 'systemd-timer · 09:00 on days 1, 3 · UTC · imported disabled', 't3-scheduler · every 3600 seconds · UTC · imported disabled', 'n8n · webhook · UTC · imported disabled']) assert.ok(text().includes(word), word);
assert.equal(text().split('cron · 0 9 * * 1 · America/Chicago · imported disabled').length, 3);
vm.runInContext("state.report.items.splice(-5); renderScan();", context);
vm.runInContext(`state.report.items.push({id:'x',name:'agents',kind:'rule',status:'conflict',importable:false,source:'~/AGENTS.md',reason:'same name as sc-other (codex) with different content',next_step:'Import one with python3 hub.py import-rule <path> --name <new>'}); renderScan();`, context);
for (const word of ['same name as sc-other (codex) with different content', 'import-rule <path> --name <new>']) assert.ok(text().includes(word), word);
assert.ok(all(get('scan-items')).find(n => n.tag === 'input' && n.value === 'e').disabled);
assert.equal(get('import-all').textContent, 'Import 4 items');
vm.runInContext(`state.report.items.push(
 {id:'s',name:'big',kind:'skill',status:'skipped',importable:false,flags:['needs-manual'],source:'~/big',reason:'package too large',next_step:'Keep each file under 1 MiB and the package under 1000 files, then scan again.'});
renderScan();`, context);
for (const word of ['package too large', 'Keep each file under 1 MiB']) assert.ok(text().includes(word), word);
assert.ok(!text().includes('Choose the file to review it'));
vm.runInContext("state.report.items.pop(); renderScan();", context);
const checked = all(get('scan-items')).find(n => n.tag === 'input' && n.value === 'f'); checked.checked = false; checked.onchange();
assert.equal(get('import-all').textContent, 'Import 3 items');
all(get('scan-items')).filter(n => n.tag === 'input' && n.checked).forEach(n => { n.checked = false; n.onchange(); });
assert.equal(get('import-all').textContent, 'Nothing selected');
assert.equal(get('import-all').disabled, true);
vm.runInContext(`state.report.items = [
 {id:'r1',name:'jev',kind:'mcp_server',status:'needs_review',importable:false,secrets:2,secret_names:['JEV_API_KEY','JEV_TOKEN'],source:'~/.codex/config.toml',review:[{reason:'Runs npx without pinning',location:'definition',blocking:false}]},
 {id:'r2',name:'plain',kind:'mcp_server',status:'needs_review',importable:false,secrets:0,source:'~/.mcp.json',review:[{reason:'Runs npx without pinning',location:'definition',blocking:false}]},
 {id:'r3',name:'tokened',kind:'mcp_server',status:'new',importable:true,secrets:1,source:'~/.cursor/mcp.json'},
 {id:'v1',name:'bundled',kind:'skill',agent:'claude',status:'vendor',ownership:'plugin_cache',plugin:'bundle',importable:false,source:'~/.claude/plugins/cache/market/bundle/1/skills/bundled'},
 {id:'v2',name:'skill-creator',kind:'skill',agent:'codex',status:'vendor',ownership:'vendor',importable:false,source:'~/.codex/skills/.system/skill-creator'}];
renderScan();`, context);
const rowText = id => { const input = all(get('scan-items')).find(n => n.tag === 'input' && n.value === id); const row = all(get('scan-items')).find(n => n.tag === 'tr' && all(n).includes(input)); return all(row).map(n => n.textContent).join(' '); };
assert.match(rowText('r1'), /Not imported unless you tick it\. Ticking it also moves JEV_API_KEY, JEV_TOKEN to the store\./);
assert.doesNotMatch(rowText('r1'), /move to the store|moves to the store/);
assert.doesNotMatch(rowText('r2'), /Ticking it also moves/);
assert.match(rowText('r3'), /1 credential move to the store/);
assert.match(rowText('v1'), /Managed by the bundle plugin/);
assert.match(rowText('v2'), /Built in to Codex/);
vm.runInContext("state.report.items[0].secret_names = []; renderScan();", context);
assert.match(rowText('r1'), /Ticking it also moves 2 credentials to the store\./);
const css = fs.readFileSync(require('node:path').join(__dirname, '../ui/style.css'), 'utf8') + fs.readFileSync(require('node:path').join(__dirname, '../ui/welcome.css'), 'utf8');
const mobile = [...css.matchAll(/@media\(max-width:600px\)\{((?:[^{}]|\{[^{}]*\})*)\}/g)].map(m => m[1]).join('');
const minHeight = selector => new RegExp('(^|[,}])\\s*' + selector.replace(/[[\]().]/g, '\\$&') + '[^{}]*\\{[^}]*min-height:44px').test(mobile);
for (const selector of ['button', 'summary', 'select', 'textarea', 'input']) assert.ok(minHeight(selector), selector + ' needs min-height:44px on mobile');
assert.match(mobile, /input\[type=checkbox\][^{}]*\{[^}]*height:24px/);
assert.match(css, /\.sum \.r button\{[^}]*white-space:nowrap/);
console.log('Kind groups, paused routine copy, missing-file fix, disabled exclusions and live selection passed.');
vm.runInContext(`state.report.items = [
 {id:'bad-file',name:'mcp',kind:'mcp_server',status:'skipped',source_error:true,importable:false,source:'~/.cursor/mcp.json',reason:'Invalid JSON'},
 {id:'paused-server',name:'paused-server',kind:'mcp_server',status:'skipped',importable:false,source:'~/.claude.json',reason:'disabled server'}]; renderScan();`, context);
assert.equal(get('scan-title').textContent, 'Found 1 item on this computer, 1 file could not be read');
assert.ok(text().includes('paused-server'));
assert.ok(!text().includes('Invalid JSON'));
assert.ok(all(get('scan-errors')).some(node => node.textContent === 'Invalid JSON'));
assert.ok(!all(get('scan-errors')).some(node => node.textContent === 'disabled server'));
console.log('Unreadable files are not counted as discovered servers; disabled servers remain ordinary skipped rows.');
const signinSource = fs.readFileSync(require('node:path').join(__dirname, '../ui/signin.js'), 'utf8')
  .replace(/^import .*;$/gm, '').replace(/export /g, '');
vm.runInContext(`(function() { ${signinSource}; init({api, state}); })()`, context);
vm.runInContext(`
state.status = {auth: [{name: 'sample', status: 'needs-auth'}]};
state.renderAuth();
state.status.auth[0].status = 'connected';
state.savedAt.sample = '4:58 PM';
state.renderAuth();
`, context);
const textOf = node => node.textContent + node.children.map(textOf).join(' ');
assert.match(textOf(get('auth')), /Saved 4:58 PM/);
assert.match(textOf(get('auth')), /Not checked yet/);
assert.doesNotMatch(textOf(get('auth')), /Verified/);
console.log('Sign-in module preserves stored credential state without claiming verification.');
const welcomeSource = fs.readFileSync(require('node:path').join(__dirname, '../ui/welcome.js'), 'utf8')
  .replace(/^import .*;$/gm, '').replace(/export /g, '');
vm.runInContext(`(function() { ${welcomeSource}; globalThis.welcomeInventory = inventory; })()`, context);
vm.runInContext(`
state.status = {counts:{skills:2,routines:1,mcp_servers:2,rules:0,plugins:0,connections:0},
 inventory:{skills:{imported:['one','two'],examples:0},routines:{imported:['daily'],examples:0},mcp_servers:{imported:['live','saved'],examples:0},rules:{imported:[],examples:0},plugins:{imported:[],examples:0},connections:{imported:[],examples:0}},
 auth:[{name:'live',state:'verified',verified_at:1},{name:'saved',state:'saved',status:'connected'}],check:'Passed'};
state.calls = {first_call:{'sample-agent':{method:'initialize'}}}; state.checkedAt = '4:58 PM';
welcomeInventory();`, context);
assert.equal(get('welcome-title').textContent, 'Your locker has 5 things in it');
const welcomeText = all(get('welcome-summary')).map(n => n.textContent).join(' ');
assert.ok(welcomeText.includes('1 verified, 1 saved but not checked, 0 need sign-in'));
assert.ok(welcomeText.includes('Agent clients seen'));
assert.equal(get('welcome-health').textContent, 'Everything checked out at 4:58 PM.');
assert.doesNotMatch(all(get('welcome-health')).map(n => n.textContent).join(''), /hub\.py/);
assert.equal(get('welcome-health-panel').hidden, false);
vm.runInContext("state.status.check = 'Needs attention — run python3 hub.py check'; welcomeInventory();", context);
assert.equal(get('welcome-health').textContent, 'Needs attention — run python3 hub.py check');
assert.equal(get('welcome-health-panel').hidden, false);
vm.runInContext("state.status.check = 'Passed'; welcomeInventory();", context);
vm.runInContext(`state.status.counts = {skills:1,routines:0,mcp_servers:0,rules:0,plugins:0,connections:0}; state.status.inventory = {skills:{imported:['one'],examples:0},routines:{imported:[],examples:0},mcp_servers:{imported:[],examples:0},rules:{imported:[],examples:0},plugins:{imported:[],examples:0},connections:{imported:[],examples:0}}; welcomeInventory();`, context);
assert.equal(get('welcome-title').textContent, 'Your locker has 1 thing in it');
vm.runInContext(`state.status.counts = {skills:0,routines:0,mcp_servers:0,rules:0,plugins:0,connections:0}; state.status.inventory = {skills:{imported:[],examples:0},routines:{imported:[],examples:0},mcp_servers:{imported:[],examples:0},rules:{imported:[],examples:0},plugins:{imported:[],examples:0},connections:{imported:[],examples:0}}; welcomeInventory();`, context);
assert.equal(get('welcome-title').textContent, "Let's fill your locker");
console.log('Welcome inventory, empty state, live verification and health passed.');
