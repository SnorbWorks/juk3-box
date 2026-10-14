// The Settings panel: the selected block's settings, drawn from the block
// schema (services/jb_ui/block_schema.json), and what you can do with it.

import { button, clear, field, h, icon, iconButton, switchControl } from '../ui.js';
import { pickPicture } from '../pages/media.js';
import { at, canDelete, canDuplicate, describe, isContainer } from './model.js';

export function createInspector(b) {
  // b: tree(), schema, layout(), theme(), selected(), select(path),
  // update(path, key, value, coalesce), moveBy(path, delta), duplicate(path),
  // remove(path), wrap(path), unwrap(path)
  const el = h('div.inspector');

  function render() {
    const tree = b.tree();
    const path = b.selected();
    const node = path && at(tree, path);
    if (!node) {
      clear(el, h('div.inspector-empty', icon('mouse-pointer-click', 'big'),
        h('p', 'Select a block in the preview or in Structure to change its settings.')));
      return;
    }
    const isRoot = path.length === 0;
    const d = isRoot ? { label: b.rootLabel(), icon: 'frame' } : describe(b.schema, node);
    const def = node.block ? b.schema.blocks[node.block] : null;
    const crumbs = [];
    for (let i = 0; i < path.length; i++) {
      const p = path.slice(0, i);
      const n = at(tree, p);
      crumbs.push(h('button.crumb', { type: 'button', onclick: () => b.select(p) }, p.length ? describe(b.schema, n).label : b.rootLabel()));
      crumbs.push(h('span.crumb-sep', '›'));
    }
    const parent = path.length ? at(tree, path.slice(0, -1)) : null;
    const index = path[path.length - 1];
    // Width only means something inside a row or grid.
    const inRow = /(^|\s)jb-u-(row|grid-[234])(\s|$)/.test(parent?.class || '');

    clear(el,
      crumbs.length ? h('nav.crumbs', { 'aria-label': 'Where this block is' }, crumbs) : null,
      h('div.inspector-head', h('span.inspector-icon', icon(d.icon)),
        h('div', h('h2', d.label), def?.description ? h('p.muted', def.description) : isContainer(node) ? h('p.muted', b.schema.container.description) : null)),
      isRoot ? null : h('div.inspector-actions',
        iconButton('arrow-up-left', 'Select the container it\'s in', { onclick: () => b.select(path.slice(0, -1)) }),
        iconButton('chevron-up', 'Move up', { disabled: index === 0, onclick: () => b.moveBy(path, -1) }),
        iconButton('chevron-down', 'Move down', { disabled: index >= parent.children.length - 1, onclick: () => b.moveBy(path, 1) }),
        iconButton('copy', 'Duplicate', { disabled: !canDuplicate(tree, b.schema, b.layout(), path), onclick: () => b.duplicate(path) }),
        iconButton('group', 'Wrap in a container', { onclick: () => b.wrap(path) }),
        isContainer(node) ? iconButton('square-dashed', 'Unwrap: keep what\'s inside, remove the container', { onclick: () => b.unwrap(path) }) : null,
        h('span.spacer'),
        iconButton('trash-2', 'Delete', { class: 'danger', disabled: !canDelete(tree, b.schema, b.layout(), path), onclick: () => b.remove(path) })),
      d.unknown ? h('div.notice.warn', icon('circle-alert'), h('span', `This engine doesn't know the "${node.block}" block, so the cabinet skips it.`)) : null,
      h('div.inspector-body',
        inRow ? control(node, path, 'span', b.schema.common.span)
          : isRoot ? null
          : field('Width', h('div.width-wrap',
            h('span.muted', 'Its width comes from where it sits. Put it in a row to choose 1/2, 1/3, 2/3…'),
            button('Put it in a row', { variant: 'small', onclick: () => b.wrap(path, 'jb-u-row') }, 'columns-3'))),
        isContainer(node) ? containerForm(node, path) : blockForm(node, path, def)));
  }

  function blockForm(node, path, def) {
    if (!def) return null;
    const fields = [];
    for (const [key, spec] of Object.entries(def.settings)) {
      if (spec.only_when && !Object.entries(spec.only_when).every(([k, v]) => node[k] === v)) continue;
      fields.push(control(node, path, key, spec));
    }
    if (!fields.length) fields.push(h('p.muted', 'This block has no settings. Your theme decides how it looks.'));
    return [fields, advanced([control(node, path, 'class', b.schema.common.class)])];
  }

  function containerForm(node, path) {
    const helpers = b.schema.container.helpers;
    const classes = () => (at(b.tree(), path).class || '').split(/\s+/).filter(Boolean);
    const setClasses = list => b.update(path, 'class', list.join(' ') || undefined);
    const group = g => helpers.filter(x => x.group === g);
    const exclusive = (label, g, noneLabel) => {
      const opts = group(g);
      const now = opts.find(o => classes().includes(o.value))?.value || '';
      const choices = [{ value: '', label: noneLabel }, ...opts.map(o => ({ value: o.value, label: shortLabel(o.label) }))];
      const pick = v => setClasses([...classes().filter(c => !opts.some(o => o.value === c)), ...(v ? [v] : [])]);
      if (choices.length <= 4) return field(label, segmented(choices, now, pick));
      const sel = h('select', { onchange: () => pick(choices[sel.selectedIndex].value) },
        choices.map(c => h('option', { selected: c.value === now }, c.value ? c.label : `${c.label} (as the theme styles it)`)));
      return field(label, sel);
    };
    const toggles = h('div.chips', helpers.filter(x => x.group === 'size' || x.group === 'align').map(o => h('button.chip', {
      type: 'button', 'aria-pressed': String(classes().includes(o.value)),
      onclick: () => setClasses(classes().includes(o.value) ? classes().filter(c => c !== o.value) : [...classes(), o.value]),
    }, o.label)));
    const helperValues = new Set(helpers.map(x => x.value));
    const own = h('input', { type: 'text', value: classes().filter(c => !helperValues.has(c)).join(' '), spellcheck: false,
      oninput: () => {
        const mine = own.value.split(/\s+/).filter(c => /^-?[A-Za-z_][A-Za-z0-9_-]*$/.test(c));
        b.update(path, 'class', [...mine, ...classes().filter(c => helperValues.has(c))].join(' ') || undefined, `class:${path}`, true);
      } });
    const settings = b.schema.container.settings;
    return [
      exclusive('Arrange', 'arrange', 'Theme'),
      exclusive('Gap', 'gap', 'Normal'),
      exclusive('Padding', 'padding', 'None'),
      field('Size and alignment', toggles),
      field('Theme classes', own, 'Classes from your theme\'s CSS, e.g. the theme\'s own sidebar or row styles.'),
      control(node, path, 'focus', settings.focus),
      advanced([control(node, path, 'focus-flow', settings['focus-flow'])]),
    ];
  }

  function advanced(children) {
    return h('details.advanced', h('summary', 'Advanced'), h('div.advanced-body', children));
  }

  function control(node, path, key, spec) {
    const value = node[key];
    const set = (v, coalesce, quiet) => b.update(path, key, v, coalesce, quiet);
    const help = spec.help;
    switch (spec.type) {
      case 'text':
      case 'class': {
        const input = h('input', { type: 'text', value: value ?? '', spellcheck: spec.type !== 'class',
          placeholder: spec.default ?? '', oninput: () => set(input.value === '' ? undefined : input.value, `${key}:${path}`, true) });
        return field(spec.label, input, help);
      }
      case 'longtext': {
        const ta = h('textarea', { rows: 4, oninput: () => set(ta.value === '' ? undefined : ta.value, `${key}:${path}`, true) });
        ta.value = value ?? '';
        return field(spec.label, ta, help);
      }
      case 'int': {
        const input = h('input', { type: 'number', min: spec.min, max: spec.max, step: 1, value: value ?? '', inputMode: 'numeric',
          placeholder: spec.default !== undefined ? String(spec.default) : 'Usual', style: { maxWidth: '140px' },
          oninput: () => {
            if (input.value === '') return set(undefined, `${key}:${path}`, true);
            const n = Math.round(Number(input.value));
            if (Number.isFinite(n) && n >= (spec.min ?? -Infinity) && n <= (spec.max ?? Infinity)) set(n, `${key}:${path}`, true);
          } });
        return field(spec.label, input, help);
      }
      case 'bool': {
        const on = value === undefined ? spec.default !== false && spec.default !== undefined ? true : false : value !== false && value !== 'false';
        return h('div.field', switchControl(on, v => {
          const def = spec.default === true;
          set(v === def ? undefined : v);
        }, spec.label), help ? h('span.field-help', help) : null);
      }
      case 'enum': {
        const opts = spec.options;
        const current = value ?? spec.default ?? (spec.required ? opts[0].value : '');
        const pick = v => set(v === '' || (!spec.required && v === spec.default && value === undefined) ? undefined : v);
        if (opts.length <= 3 && opts.every(o => o.label.length <= 12)) return field(spec.label, segmented(opts, current, pick), help);
        const sel = h('select', { onchange: () => pick(opts[sel.selectedIndex].value) },
          opts.map(o => h('option', { selected: o.value === current }, o.label)));
        return field(spec.label, sel, help);
      }
      case 'multi': {
        const chosen = String(value ?? spec.default ?? '').split(',').filter(Boolean);
        const chips = h('div.chips', spec.options.map(o => {
          const n = chosen.indexOf(o.value);
          return h('button.chip', { type: 'button', 'aria-pressed': String(n >= 0), onclick: () => {
            const next = n >= 0 ? chosen.filter(v => v !== o.value) : [...chosen, o.value];
            if (!next.length) return;
            set(next.join(','));
          } }, n >= 0 ? h('span.chip-n', String(n + 1)) : null, o.label);
        }));
        return field(spec.label, chips, help || 'Cards show in the order you pick them.');
      }
      case 'picture': {
        const src = value ? String(value) : '';
        const url = !src ? null : src.startsWith('/media/') ? src : `/themes/${b.theme()}/${src}`;
        return field(spec.label, h('div.picture-field',
          h('div.picture-thumb', url ? h('img', { src: url, alt: '' }) : icon('image')),
          h('div.picture-buttons',
            button(src ? 'Change' : 'Choose', { variant: 'small', onclick: async () => {
              const got = await pickPicture(b.theme(), src);
              if (got) set(got);
            } }, 'image'),
            src ? button('Remove', { variant: 'small ghost', onclick: () => set(undefined) }) : null)), help);
      }
      default:
        return null;
    }
  }

  return { el, render };
}

function segmented(options, current, onPick) {
  return h('div.segmented', { role: 'group' }, options.map(o => h('button', {
    type: 'button', 'aria-pressed': String(o.value === current), onclick: () => onPick(o.value),
  }, o.label)));
}

function shortLabel(label) {
  return label.replace('Grid, ', 'Grid ').replace(' across', '').replace(' gap', '').replace(' padding', '');
}
