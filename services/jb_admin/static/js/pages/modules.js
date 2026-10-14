// Modules: what's installed and running (the platform's module registry),
// managing each one (restart, turn off/on, its log and settings), and adding
// modules from a zip: checked, reviewed, installed in a sandbox
// (docs/contracts/extensions.md).

import { del, get, post, put, upload } from '../api.js';
import { button, clear, confirmDialog, field, h, icon, modal, pageHeader, showError, switchControl, toast } from '../ui.js';

const ROLE_LABELS = { source: 'Source', analyzer: 'Analyzer', input: 'Input', output: 'Output', service: 'Service' };
let timer = null;

export async function show(main) {
  const body = h('div.card');
  const input = h('input', { type: 'file', accept: '.zip,application/zip', hidden: true,
    onchange: () => { const f = input.files[0]; input.value = ''; if (f) addModule(f, refresh); } });
  clear(main, pageHeader('Modules', 'The parts that make up your jukebox. Each declares what it does; the dot shows whether it\'s running.',
    button('Add module', { variant: 'primary', onclick: () => input.click() }, 'upload'), input), body);
  const refresh = async () => render(body, await get('/api/status'), refresh);
  await refresh();
  timer = setInterval(() => refresh().catch(() => {}), 5000);
}

export function leave() {
  clearInterval(timer);
}

function render(body, s, refresh) {
  clear(body, s.modules.map(m => {
    const state = m.up === true ? 'up' : m.up === false ? 'down' : '';
    const how = { heartbeat: 'heartbeat', status: 'status', systemd: 'system service' }[m.up_from] || 'unknown';
    return h('div.module-row',
      h(`span.dot${state ? '.' + state : ''}`, { title: m.up === true ? `Running (${how})` : m.up === false ? `Not running (${how})` : 'Unknown' }),
      h('div', h('div.module-name', m.name || m.id), h('div.module-id', m.id, m.version ? ` · v${m.version}` : '')),
      h('div.roles', m.undeclared ? h('span.badge.warn', 'Undeclared') : null,
        m.installed ? h('span.badge.ok', 'Installed') : null,
        m.essential ? h('span.badge', 'Essential') : null,
        (m.roles || []).map(r => h('span.badge.accent', ROLE_LABELS[r] || r)),
        m.undeclared ? null : button('Manage', { variant: 'small', onclick: () => manage(m.id, refresh) }, 'settings')),
      m.description || m.problem ? h('p.module-desc', m.problem || m.description) : null);
  }));
}

// One module: overview, settings, log, and what can be done with it.
async function manage(id, refresh) {
  let m;
  try { m = await get(`/api/modules/${encodeURIComponent(id)}`); } catch (e) { showError(e); return; }
  const panes = { overview: overview(m), settings: settingsForm(m), log: logPane(m) };
  const tabs = h('div.segmented');
  const holder = h('div.module-pane');
  const showTab = name => {
    for (const b of tabs.children) b.setAttribute('aria-pressed', String(b.dataset.tab === name));
    clear(holder, panes[name]);
    if (name === 'log') panes.log.load();
  };
  for (const [k, label] of [['overview', 'Overview'], ['settings', 'Settings'], ['log', 'Log']]) {
    tabs.append(h('button', { type: 'button', dataset: { tab: k }, onclick: () => showTab(k) }, label));
  }
  const running = m.state === 'active';
  const act = async (action, ask) => {
    if (ask && !await confirmDialog(...ask)) return false;
    try {
      await post(`/api/modules/${encodeURIComponent(id)}/${action}`);
      toast({ restart: 'Restarted.', stop: 'Turned off.', start: 'Turned on.' }[action], 'success');
      dlg.close();
      refresh();
    } catch (e) { showError(e); }
  };
  const essentialAsk = verb => m.essential ? [`${verb} ${m.name}?`, `The jukebox needs ${m.name} to work; it'll be unavailable until it's back.`, { confirm: verb, danger: true }] : null;
  const dlg = modal({
    title: m.name || m.id, wide: true,
    body: h('div.stack', tabs, holder),
    actions: [
      ...(m.installed ? [{ label: 'Remove', variant: 'danger', onclick: async () => {
        if (!await confirmDialog(`Remove ${m.name}?`, 'It stops, and its files and settings are deleted. Blocks of it on your screens show nothing.', { confirm: 'Remove', danger: true })) return;
        try { await del(`/api/modules/${encodeURIComponent(id)}`); toast(`${m.name} removed.`, 'success'); dlg.close(); refresh(); } catch (e) { showError(e); }
      } }] : []),
      ...(m.id === 'jb-admin' ? [] : [running
        ? { label: 'Turn off', onclick: () => act('stop', m.essential ? essentialAsk('Turn off') : null) }
        : { label: 'Turn on', onclick: () => act('start') }]),
      { label: 'Restart', onclick: () => act('restart', essentialAsk('Restart')) },
      { label: 'Close', variant: 'primary' },
    ],
  });
  showTab('overview');
}

function overview(m) {
  const res = m.resources || {};
  return h('div.stack',
    m.description ? h('p', m.description) : null,
    h('div.kv',
      h('div', h('span.muted', 'State '), m.state === 'active' ? 'Running' : m.state || 'Unknown'),
      h('div', h('span.muted', 'Version '), m.version || '—'),
      h('div', h('span.muted', 'Roles '), (m.roles || []).map(r => ROLE_LABELS[r] || r).join(', ') || '—'),
      h('div', h('span.muted', 'Kind '), m.installed ? 'Installed: runs in its own sandbox with only what it asked for' : 'Built in'),
      m.installed ? h('div', h('span.muted', 'Limits '), `${res.cpu ?? 25}% of a core, ${res.memory_mb ?? 128} MB`) : null),
    m.review ? h('div', h('h3.small-head', 'What it\'s allowed'), h('ul.list.review', m.review.map(r => h('li', icon(r.icon === 'globe' ? 'radio' : r.icon), h('span', r.text))))) : null,
    (m.publishes?.length || m.subscribes?.length) ? h('details.module-topics',
      h('summary', 'Bus topics'),
      m.publishes?.length ? h('div', 'Publishes: ', m.publishes.join(', ')) : null,
      m.subscribes?.length ? h('div', 'Listens to: ', m.subscribes.join(', ')) : null) : null);
}

// The module's own settings, drawn from its manifest (owner_settings).
function settingsForm(m) {
  const { schema, values } = m.settings;
  if (!Object.keys(schema).length) return h('p.muted', 'This module has no settings.');
  const draft = {};
  const controls = Object.entries(schema).map(([k, spec]) => {
    const v = values[k];
    const set = x => { draft[k] = x; };
    if (spec.type === 'enum') {
      const sel = h('select', { onchange: () => set(sel.value) }, spec.options.map(o => h('option', { value: o.value, selected: o.value === v }, o.label)));
      return field(spec.label, sel, spec.help);
    }
    if (spec.type === 'toggle') return h('div.field', switchControl(!!v, x => set(x), spec.label), spec.help ? h('span.field-help', spec.help) : null);
    if (spec.type === 'number') {
      const inp = h('input', { type: 'number', value: v ?? '', min: spec.min, max: spec.max, oninput: () => set(inp.value === '' ? undefined : Number(inp.value)) });
      return field(spec.label, inp, spec.help);
    }
    if (spec.type === 'secret') {
      const inp = h('input', { type: 'password', autocomplete: 'off', placeholder: v?.set ? 'Saved (type to replace)' : 'Not set', oninput: () => set(inp.value) });
      return field(spec.label, inp, spec.help || 'Kept privately on the jukebox; never shown again.');
    }
    const inp = h('input', { type: 'text', value: v ?? '', oninput: () => set(inp.value) });
    return field(spec.label, inp, spec.help);
  });
  return h('div.stack', controls, h('div', button('Save settings', { variant: 'primary', onclick: async () => {
    try {
      await put(`/api/modules/${encodeURIComponent(m.id)}/settings`, { values: draft });
      toast(`Saved. ${m.name} picks it up now.`, 'success');
    } catch (e) { showError(e); }
  } }, 'check')));
}

function logPane(m) {
  const pre = h('pre.module-log', 'Loading…');
  const el = h('div.stack', h('div', button('Refresh', { variant: 'small', onclick: () => el.load() }, 'refresh-cw')), pre);
  el.load = async () => {
    try {
      const { lines } = await get(`/api/modules/${encodeURIComponent(m.id)}/log`);
      pre.textContent = lines.join('\n') || 'Nothing logged yet.';
      pre.scrollTop = pre.scrollHeight;
    } catch (e) { pre.textContent = e.message; }
  };
  return el;
}

// Adding: upload, review what it asks for, install (health-checked).
async function addModule(file, refresh) {
  let staged;
  try { staged = await upload('/api/modules/upload', file); } catch (e) { showError(e); return; }
  let installing = false;
  const status = h('p.muted');
  const dlg = modal({
    title: staged.update_from ? `Update ${staged.name} (v${staged.update_from} → v${staged.version})` : `Install ${staged.name}?`,
    body: h('div.stack',
      staged.description ? h('p', staged.description) : null,
      h('h3.small-head', `${staged.name} asks for`),
      h('ul.list.review', staged.review.map(r => h('li', icon(r.icon === 'globe' ? 'radio' : r.icon), h('span', r.text)))),
      h('p.muted', 'It runs in a sandbox of its own and gets only these. If it doesn\'t start properly it\'s turned off and removed (or the previous version comes back).'),
      status),
    onClose: () => { if (!installing) del(`/api/modules/staged/${staged.token}`).catch(() => {}); },
    actions: [
      { label: 'Cancel' },
      { label: staged.update_from ? 'Update' : 'Install', variant: 'primary', onclick: async () => {
        if (installing) return false;
        installing = true;
        clear(status, h('span.spinner'), ' Installing and checking it starts properly (up to a minute)…');
        try {
          const done = await post('/api/modules/install', { token: staged.token });
          toast(`${done.name} is installed and running.`, 'success', 5000);
          dlg.close();
          refresh();
        } catch (e) {
          installing = false;
          clear(status);
          dlg.close();
          showError(e);
          refresh();
        }
        return false;
      } },
    ],
  });
}
