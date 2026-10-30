import { $, state, api, message, action, on } from './api.js';
import * as welcome from './welcome.js';
import * as scan from './scan.js';
import * as signin from './signin.js';
import * as connect from './connect.js';

const screens = [welcome, scan, signin, connect];
const names = ['welcome', 'scan', 'signin', 'connect'];
const destinations = [
  {step: 0, label: 'Welcome'}, {step: 1, group: 'skill', label: 'Skills'},
  {step: 1, group: 'routine', label: 'Routines'}, {step: 1, group: 'mcp_server', label: 'MCP servers'},
  {step: 2, label: 'Sign in'}, {step: 3, label: 'Connect'}
];
const context = {api, state};
let navigation = 0;
export async function show(step, group) {
  const destination = destinations.findIndex(item => item.step === step && (!group || item.group === group));
  state.current = step; state.nav = destination; state.scanGroup = group || 'skill';
  const revision = ++navigation;
  document.querySelectorAll('[data-screen]').forEach((section, index) => section.hidden = index !== step);
  document.querySelectorAll('[data-nav]').forEach((item, index) => {
    item.className = index === destination ? 'cur' : index < destination ? 'done' : '';
    const button = item.querySelector('button');
    if (index === destination) button.setAttribute('aria-current', 'step');
    else button.removeAttribute('aria-current');
  });
  $('progress').textContent = `${destinations[destination].label} · ${destination + 1} of 6`;
  $('progress').setAttribute('aria-expanded', 'false');
  document.querySelector('nav').classList.remove('menu-open');
  $('back').hidden = destination === 0;
  $('next').hidden = destination === 5;
  message('');
  await screens[step].render(context);
  if (revision !== navigation) return;
  const heading = step === 1 ? document.querySelector(`[data-scan-group="${state.scanGroup}"] summary`) || $('scan-title') : document.querySelector(`[data-screen="${names[step]}"] h1`);
  heading.setAttribute('tabindex', '-1'); heading.focus();
  if (step === 1 && heading !== $('scan-title')) heading.scrollIntoView({block: 'nearest'});
}
function navigate(index) { const item = destinations[Math.max(0, Math.min(5, index))]; return show(item.step, item.group); }
function setTheme(theme) {
  if (theme === 'system') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.dataset.theme = theme;
  $('theme-toggle').textContent = 'Theme: ' + theme;
  $('theme-toggle').setAttribute('aria-label', 'Color theme: ' + theme + '. Switch color theme');
}
let theme = 'system';
try { theme = localStorage.getItem('agentlocker-theme') || 'system'; } catch {}
if (!['system', 'dark', 'light'].includes(theme)) theme = 'system';
setTheme(theme);
$('theme-toggle').onclick = () => {
  theme = ['system', 'dark', 'light'][(['system', 'dark', 'light'].indexOf(theme) + 1) % 3];
  setTheme(theme);
  try { localStorage.setItem('agentlocker-theme', theme); } catch {}
};
$('progress').onclick = () => {
  const expanded = $('progress').getAttribute('aria-expanded') !== 'true';
  $('progress').setAttribute('aria-expanded', String(expanded));
  document.querySelector('nav').classList.toggle('menu-open', expanded);
};
screens.forEach(screen => screen.init(context));
on('next', () => navigate((state.nav || 0) + 1));
on('back', () => navigate((state.nav || 0) - 1));
document.querySelectorAll('[data-go]').forEach(button => {
  button.onclick = () => action(button, () => show(Number(button.dataset.go), button.dataset.group));
});
async function start() {
  $('retry-setup').hidden = true;
  try { state.token = (await api('session')).token; await show(state.current); }
  catch (error) { message(error.message, true); $('retry-setup').hidden = false; }
}
on('retry-setup', start);
start();
window.addEventListener('focus', () => {
  if (state.current === 2) signin.render(context).catch(error => message(error.message, true));
});
