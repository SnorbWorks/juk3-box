// Modules: what's installed and running (the platform's module registry).

import { get } from '../api.js';
import { clear, h, pageHeader } from '../ui.js';

const ROLE_LABELS = { source: 'Source', analyzer: 'Analyzer', input: 'Input', output: 'Output', service: 'Service' };
let timer = null;

export async function show(main) {
  const body = h('div.card');
  clear(main, pageHeader('Modules', 'The parts that make up your jukebox. Each declares what it does; the dot shows whether it\'s running.'), body);
  const refresh = async () => render(body, await get('/api/status'));
  await refresh();
  timer = setInterval(() => refresh().catch(() => {}), 5000);
}

export function leave() {
  clearInterval(timer);
}

function render(body, s) {
  clear(body, s.modules.map(m => {
    const state = m.up === true ? 'up' : m.up === false ? 'down' : '';
    const how = { heartbeat: 'heartbeat', status: 'status', systemd: 'system service' }[m.up_from] || 'unknown';
    return h('div.module-row',
      h(`span.dot${state ? '.' + state : ''}`, { title: m.up === true ? `Running (${how})` : m.up === false ? `Not running (${how})` : 'Unknown' }),
      h('div', h('div.module-name', m.name || m.id), h('div.module-id', m.id, m.version ? ` · v${m.version}` : '')),
      h('div.roles', m.undeclared ? h('span.badge.warn', 'Undeclared') : null,
        m.essential ? h('span.badge', 'Essential') : null,
        (m.roles || []).map(r => h('span.badge.accent', ROLE_LABELS[r] || r))),
      m.description || m.problem ? h('p.module-desc', m.problem || m.description) : null,
      (m.publishes?.length || m.subscribes?.length) ? h('details.module-topics',
        h('summary', 'Bus topics'),
        m.publishes?.length ? h('div', 'Publishes: ', m.publishes.join(', ')) : null,
        m.subscribes?.length ? h('div', 'Listens to: ', m.subscribes.join(', ')) : null) : null);
  }));
}
