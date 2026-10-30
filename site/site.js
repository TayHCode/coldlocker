'use strict';
const captions = {
  welcome: 'Welcome — see what is in your locker and what needs attention.',
  scan: 'Scan & import — review discovered files and choose what to copy. Originals stay in place.',
  signin: 'Sign in — save credentials locally and review which servers still need attention.',
  connect: 'Connect — choose an agent and copy its connection settings.'
};
let current = 'welcome';
let theme = 'system';
try { theme = localStorage.getItem('agentlocker-site-theme') || 'system'; } catch {}
if (!['system', 'dark', 'light'].includes(theme)) theme = 'system';
function renderPreview() {
  const dark = theme === 'dark' || (theme === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.querySelector('#preview-dark').srcset = `/screens/${current}-${dark ? 'dark' : 'light'}.png`;
  document.querySelector('#preview-image').src = `/screens/${current}-${dark ? 'dark' : 'light'}.png`;
  document.querySelector('#preview-image').alt = `Example ${current} screen in the agentlocker setup wizard`;
  document.querySelector('#preview-caption').textContent = captions[current] + ' Design preview with example content.';
}
function applyTheme() {
  if (theme === 'system') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  document.querySelector('#theme').textContent = `Theme: ${theme}`;
  renderPreview();
}
document.querySelector('#theme').addEventListener('click', () => {
  theme = ['system', 'dark', 'light'][(['system', 'dark', 'light'].indexOf(theme) + 1) % 3];
  try { localStorage.setItem('agentlocker-site-theme', theme); } catch {}
  applyTheme();
});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', renderPreview);
const tabs = [...document.querySelectorAll('[data-screen]')];
function activate(tab) {
  current = tab.dataset.screen;
  tabs.forEach(t => { t.setAttribute('aria-selected', String(t === tab)); t.tabIndex = t === tab ? 0 : -1; });
  document.querySelector('#preview-panel').setAttribute('aria-labelledby', tab.id);
  renderPreview();
}
tabs.forEach((tab, index) => {
  tab.addEventListener('click', () => activate(tab));
  tab.addEventListener('keydown', event => {
    let next;
    if (event.key === 'ArrowRight') next = tabs[(index + 1) % tabs.length];
    if (event.key === 'ArrowLeft') next = tabs[(index + tabs.length - 1) % tabs.length];
    if (event.key === 'Home') next = tabs[0];
    if (event.key === 'End') next = tabs.at(-1);
    if (next) { event.preventDefault(); activate(next); next.focus(); }
  });
});
document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
  const text = document.getElementById(button.dataset.copy).textContent;
  try {
    await navigator.clipboard.writeText(text);
    document.querySelector('#copy-status').textContent = 'Copied.';
    const previous = button.textContent; button.textContent = 'Copied';
    setTimeout(() => { button.textContent = previous; }, 1800);
  } catch {
    const selection = getSelection(); const range = document.createRange();
    range.selectNodeContents(document.getElementById(button.dataset.copy));
    selection.removeAllRanges(); selection.addRange(range);
    document.querySelector('#copy-status').textContent = 'Text selected. Copy it with your browser.';
  }
}));
document.querySelector('#connection-form').addEventListener('submit', event => {
  event.preventDefault();
  const error = document.querySelector('#connection-error');
  const result = document.querySelector('#connection-result');
  error.textContent = ''; result.hidden = true;
  try {
    const url = new URL(document.querySelector('#catalog-url').value.trim());
    if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || !/^\/[A-Za-z0-9_./~-]*$/.test(url.pathname)) throw new Error();
    document.querySelector('#connection-prompt').textContent = `Add this MCP server: ${url.href}\n\nKeep my existing connections. Complete the private sign-in flow, then list the available skills and routines. Do not activate any schedules.`;
    result.hidden = false;
  } catch { error.textContent = 'Use your HTTPS MCP URL, without credentials, query parameters, or a fragment.'; }
});
fetch('/downloads/release.json').then(response => {
  if (!response.ok) throw new Error();
  return response.json();
}).then(release => {
  document.querySelector('#release-detail').textContent = `Preview build ${release.source_sha256.slice(0, 12)} · includes the v4 setup wizard · ${release.file_count} files`;
}).catch(() => {});
applyTheme();
