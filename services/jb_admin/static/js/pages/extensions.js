// Extensions: everything Juk3box runs or ships that isn't its own code --
// other people's software, fonts, icons, web services and pictures -- with
// each one's license and what that license asks. (Modules are Juk3box's
// own add-ons; they have their own page.) The list is deploy/extensions.json;
// the versions are what's installed on this jukebox.

import { get } from '../api.js';
import { clear, h, icon, pageHeader } from '../ui.js';

const ORDER = ['service', 'media', 'system', 'python', 'bundled', 'firmware', 'dev'];
const DUTY_LABEL = {
  notice: 'Keep the notice',
  'copyleft-source': 'Offer source',
  agreement: 'Needs an agreement',
  review: 'To confirm',
  none: 'Not shipped',
};

export async function show(main) {
  const data = await get('/api/extensions');
  const items = data.items || [];
  const attention = items.filter(i => i.duty === 'agreement' || i.duty === 'review');
  const byKind = {};
  for (const item of items) (byKind[item.kind] ||= []).push(item);

  clear(main,
    pageHeader('Extensions', 'Software, fonts, services and pictures Juk3box uses that aren\'t its own, with their licenses. Modules are on their own page.'),
    data.problem ? h('div.notice.danger', icon('circle-alert'), h('span', data.problem)) : null,
    attention.length ? h('div.notice.warn', icon('circle-alert'), h('div',
      h('strong', 'Before selling Juk3box: '),
      `${attention.length} need an agreement or a check.`,
      h('ul.ext-attention', attention.map(i => h('li', h('b', i.name), ' — ', i.note || data.duties?.[i.duty] || ''))))) : null,
    ...ORDER.filter(k => byKind[k]).map(kind => h('div.card',
      h('div.card-head', h('h2', data.kinds?.[kind] || kind), h('span.muted', `${byKind[kind].length}`)),
      h('div.ext-list', byKind[kind].map(item => row(item, data.duties || {}))))),
    h('p.muted.ext-foot', `List updated ${data.updated || '—'}. What each kind of license asks: `,
      ...Object.entries(data.duties || {}).map(([k, v]) => h('span.ext-duty-help', h('b', DUTY_LABEL[k] || k), `: ${v} `))));
}

export function leave() {}

function row(item, duties) {
  return h('div.ext-row',
    h('div.ext-main',
      h('div.ext-name', item.url ? h('a', { href: item.url, target: '_blank', rel: 'noopener' }, item.name) : item.name),
      h('div.ext-purpose', item.purpose || ''),
      h('div.ext-meta', [item.used_by ? `Used by ${item.used_by}` : null, item.installed ? `Installed: ${item.installed}` : null]
        .filter(Boolean).join(' · '))),
    h('div.ext-side',
      h('span.ext-license', item.license),
      h(`span.ext-duty.duty-${item.duty}`, { title: duties[item.duty] || '' }, DUTY_LABEL[item.duty] || item.duty)));
}
