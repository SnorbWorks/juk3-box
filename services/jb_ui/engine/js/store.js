// Live system state, and who's watching which part of it.

const values = new Map();
const watchers = new Map();  // key -> Set of {el, fn}

export const store = {
  get(key) {
    return values.get(key);
  },

  set(key, value) {
    values.set(key, value);
    for (const w of watchers.get(key) || []) {
      // Blocks belong to a screen; once it's gone, so is the watcher.
      if (!w.el.isConnected) {
        watchers.get(key).delete(w);
        continue;
      }
      w.fn(value);
    }
  },

  // Call fn with the current value now and on every change, for as long
  // as el is on the page. el may not be on the page yet when this is
  // called (blocks are built before their screen is shown).
  watch(el, key, fn) {
    if (!watchers.has(key)) watchers.set(key, new Set());
    watchers.get(key).add({ el, fn });
    fn(values.get(key));
  },
};
