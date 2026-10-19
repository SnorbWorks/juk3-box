// Settings: the owner settings, drawn from the server's schema.

import { get, put } from '../api.js';
import { button, clear, field, h, icon, iconButton, pageHeader, showError, toast } from '../ui.js';
import { sortable } from '../sortable.js';

let state = null;

export async function show(main) {
  const data = await get('/api/settings');
  state = { schema: data.schema, saved: structuredClone(data.values), values: structuredClone(data.values) };
  render(main);
}

export function leave() {
  // Settings save with a button; leaving with changes just drops them.
  state = null;
}

function dirty() {
  return JSON.stringify(state.values) !== JSON.stringify(state.saved);
}

function render(main) {
  const groups = {};
  for (const [key, spec] of Object.entries(state.schema)) (groups[spec.group] ||= []).push([key, spec]);
  const saveBtn = button('Save changes', { variant: 'primary', onclick: save }, 'check');
  const status = h('span.muted');
  const update = () => {
    saveBtn.disabled = !dirty();
    status.textContent = dirty() ? 'Unsaved changes' : '';
  };
  const card = h('div.card',
    Object.entries(groups).map(([name, items]) => h('section.settings-group',
      h('h2', name), items.map(([key, spec]) => control(key, spec, update)))),
    h('div.save-bar', status, saveBtn));
  clear(main, pageHeader('Settings', 'How the cabinet behaves. Changes reach the cabinet when you save.'), card);
  update();

  async function save() {
    saveBtn.disabled = true;
    try {
      const res = await put('/api/settings', { values: state.values });
      state.saved = structuredClone(res.values);
      state.values = structuredClone(res.values);
      toast('Saved. The cabinet is updating.', 'success');
      render(main);
    } catch (e) {
      showError(e);
      update();
    }
  }
}

function control(key, spec, changed) {
  const set = v => { state.values[key] = v; changed(); };
  const value = state.values[key];
  if (spec.type === 'order') {
    // Two sections: the buttons, and (when there are any) the modules.
    const lists = orderLists(spec, value, v => set(v));
    return [field(spec.label, lists.button, spec.help),
      lists.module ? field(spec.modules_label || 'Below', lists.module, spec.modules_help) : null];
  }
  if (spec.type === 'enum') {
    const sel = h('select', { onchange: () => set(spec.options[sel.selectedIndex].value) },
      spec.options.map(o => h('option', { selected: o.value === value }, o.label)));
    return field(spec.label, sel, spec.help);
  }
  if (spec.type === 'number') {
    const input = h('input', { type: 'number', min: spec.min, max: spec.max, step: spec.step, value,
      inputMode: 'numeric', style: { maxWidth: '140px' },
      oninput: () => { if (input.value !== '' && input.validity.valid) set(Number(input.value)); } });
    return field(spec.unit ? `${spec.label} (${spec.unit})` : spec.label, input, spec.help);
  }
  return h('p.muted', `${spec.label}: not editable here yet.`);
}

// An ordered set: every option, switched on or off, in the order chosen.
// The navigation bar's options come in two kinds, each its own list,
// ordered within itself: its buttons, then the modules (the Now Playing
// card, Up Next, modules' blocks: docs/contracts/installed-modules.md,
// Blocks), which always come after every button. Returns the lists.
function orderLists(spec, value, onChange) {
  const labels = Object.fromEntries(spec.options.map(o => [o.value, o.label]));
  const kindOf = Object.fromEntries(spec.options.map(o => [o.value, o.kind || 'button']));
  const on = new Set(value.filter(v => v in labels));
  const ordered = kind => [...value, ...spec.options.map(o => o.value)]
    .filter((v, i, all) => v in labels && kindOf[v] === kind && all.indexOf(v) === i);
  const lists = { button: ordered('button'), module: ordered('module') };
  const emit = () => onChange([...lists.button, ...lists.module].filter(v => on.has(v)));
  const drawn = [];
  const redraw = () => drawn.forEach(d => d());

  const section = kind => {
    const order = lists[kind];
    const list = h('ul.order-list');
    const draw = () => {
      // The knob rests on the first button shown.
      const rest = lists.button.find(b => on.has(b));
      clear(list, order.map((v, i) => {
        const box = h('input', { type: 'checkbox', checked: on.has(v), 'aria-label': `Show ${labels[v]}`, onchange: () => {
          const buttonsOn = lists.button.filter(b => on.has(b)).length;
          if (box.checked) on.add(v);
          else if (kind === 'module' || buttonsOn > 1) on.delete(v);
          else { box.checked = true; toast('Keep at least one button.', 'error'); }
          redraw(); emit();
        } });
        return h(`li.order-item${on.has(v) ? '' : '.off'}`,
          h('span.icon-btn', { dataset: { handle: '' }, style: { cursor: 'grab', touchAction: 'none' }, title: 'Drag to reorder' }, icon('grip-vertical')),
          box, h('span.order-label', labels[v]),
          v === rest ? h('span.badge.accent', 'Rest') : null,
          iconButton('chevron-up', 'Move up', { disabled: i === 0, onclick: () => move(i, i - 1) }),
          iconButton('chevron-down', 'Move down', { disabled: i === order.length - 1, onclick: () => move(i, i + 1) }));
      }));
    };
    const move = (from, to) => {
      const [item] = order.splice(from, 1);
      order.splice(to, 0, item);
      redraw(); emit();
    };
    sortable(list, move);
    drawn.push(draw);
    draw();
    return list;
  };

  return { button: section('button'), module: lists.module.length ? section('module') : null };
}


