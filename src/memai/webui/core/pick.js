/* A <select> replacement: a button, and a listbox in a popover.

   Not for the sake of a different widget. A native <option> renders as text
   and nothing else -- it takes no mark, no second line, no rail -- so every
   choice this app offers that is more than a word had to be flattened into
   one: a domain lost the tree it is, a memory type lost the colour the rest
   of the UI identifies it by, a confidence lost its ring. The popup was also
   the one surface in the app the stylesheet could not reach, so it arrived in
   the browser's own idea of a dark theme next to controls that were not.

   So the shell lives here -- button, panel, placement, the caret, the keys,
   dismissal -- and a caller supplies only what goes INSIDE a row. Nothing
   about a domain, a type or a shape is known at this level; see
   core/domain-picker.js for the row shape that made this worth extracting.

   ARIA follows the combobox pattern: the button owns aria-expanded, the
   panel is a listbox of options, and the caret is aria-activedescendant on
   whichever element has focus -- never focus itself, which has to stay in the
   filter field while the caret moves.

   One panel at a time, parented to <body> (a toolbar clips, a modal stacks)
   and dismissed by whatever you do next. */

import { esc } from './dom.js';
import { onTeardown } from './lifecycle.js';
import { t } from '../i18n.js';

/* Past this many rows the panel gets a filter field. Below it, the list is
   shorter than the field would be tall. */
const SEARCH_FROM = 9;

/* the open panel, or nothing. One at a time: two would be two carets. */
let live = null;

export function closePicker() {
  if (!live) return;
  const { panel, btn, drop } = live;
  live = null;
  drop();
  panel.remove();
  btn.setAttribute('aria-expanded', 'false');
}

export const pickerOpen = () => Boolean(live);

/* The control. `value` is what it currently means and rides on the button as
   data-v: the panel opens onto it, and a form reads it back with pickerValue
   -- by then the caller's own state object is long out of reach.

   `label` is what that value looks like, `html` the same thing with markup
   (a type dot, a confidence ring) for the callers whose rows carry one. */
export const pickerHTML = ({ id, value = '', label = '', html = '', ariaLabel = '',
                             cls = '', disabled = false, title = '' }) => `
  <button type="button" class="pick${cls ? ` ${cls}` : ''}" id="${id}" data-v="${esc(value)}"
          aria-haspopup="listbox" aria-expanded="false"${disabled ? ' disabled' : ''}
          aria-label="${esc(ariaLabel || label)}" title="${esc(title || label)}">
    <span class="pick-value">${html || esc(label)}</span>
  </button>`;

/* The button for a value out of a fixed list: the control opens already
   showing the row it is about to highlight, mark and all, instead of the
   caller restating that row's face a second time and getting it half right. */
export const pickerFor = ({ id, value = '', items, ariaLabel = '', cls = '',
                            disabled = false }) => {
  const it = items.find(x => x.value === value) || items[0] || {};
  return pickerHTML({ id, value: it.value ?? '', label: it.label ?? '',
                      html: it.html ?? '', title: it.title, ariaLabel, cls, disabled });
};

/* What a picker currently means, for a form that reads its fields at submit
   -- the stand-in for `select.value`. */
export const pickerValue = (root, id) => root.querySelector(`#${id}`)?.dataset.v ?? '';

/* Write a value in from outside (a form being reset, a value the server
   corrected). Same three things a pick does, minus the callback. */
export function setPickerValue(btn, { value, label = '', html = '', title = '' }) {
  if (!btn) return;
  btn.dataset.v = value;
  btn.title = title || label || '';
  btn.querySelector('.pick-value').innerHTML = html || esc(label);
}

/* `items` is a function of the filter text, not an array: what a query leaves
   is the caller's business -- the domain picker keeps the ancestors of a match
   so its rails still have something to hang off, which no generic filter would
   have thought to do.

   Each item: { value, label, html?, cls?, style?, title? }. `label` is the
   text the filter matches and what the button shows once it is picked; `html`
   is the row's insides when they are more than that text.

   `search` defaults to a row count -- see SEARCH_FROM. `onPick` gets the
   value; a picker that stands for a form field also repaints the button,
   which is why the item is handed over with it.

   `anchor` is a selector for an ANCESTOR of the button, and the panel is then
   measured and placed against that element instead of the button: a control
   that is only the right-hand half of a row opens over the whole row, at the
   row's width. It is resolved when the panel opens, so a repaint of the row
   leaves no stale element behind. */
export function wirePicker(root, { id, items, onPick, search = 'auto',
                                   minWidth = 180, panelCls = '', keepLabel = false,
                                   anchor = '' }) {
  const btn = root.querySelector(`#${id}`);
  if (!btn) return;
  /* a view swap drops the button but not a panel parented to <body> */
  onTeardown(closePicker);
  const open = () =>
    openPanel(btn, { items, onPick, search, minWidth, panelCls, keepLabel, anchor });
  btn.addEventListener('click', () => {
    if (live && live.btn === btn) closePicker();
    else open();
  });
  /* the two keys that open a listbox without choosing anything */
  btn.addEventListener('keydown', e => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault();
    if (!live || live.btn !== btn) open();
  });
}

function openPanel(btn, { items, onPick, search, minWidth, panelCls, keepLabel, anchor }) {
  closePicker();
  const current = btn.dataset.v || '';
  const all = items('');
  const withSearch = search === 'auto' ? all.length >= SEARCH_FROM : Boolean(search);
  const listId = `pickList-${btn.id}`;

  const panel = document.createElement('div');
  panel.className = `pick-pop${panelCls ? ` ${panelCls}` : ''}`;
  panel.innerHTML = `
    ${withSearch ? `<div class="pick-head">
      <input type="search" class="pick-q" role="combobox" aria-expanded="true"
             aria-controls="${listId}" aria-autocomplete="list" autocomplete="off"
             spellcheck="false" placeholder="${t('pick.filter')}" aria-label="${t('pick.filter')}">
    </div>` : ''}
    <div class="pick-list" id="${listId}" role="listbox"${withSearch ? '' : ' tabindex="-1"'}
         aria-label="${esc(btn.getAttribute('aria-label') || '')}"></div>`;
  document.body.appendChild(panel);

  const q = panel.querySelector('.pick-q');
  const list = panel.querySelector('.pick-list');
  /* the keys land on the field when there is one, on the list when there is
     not -- either way one handler, and the caret is never the focus */
  const keyHost = q || list;
  let shown = [];         /* the items as drawn, so the caret is an index */
  let at = 0;

  const mark = () => {
    let active = null;
    list.querySelectorAll('.pick-row').forEach(el => {
      const on = Number(el.dataset.i) === at;
      el.classList.toggle('active', on);
      if (on) active = el;
    });
    keyHost.setAttribute('aria-activedescendant', active ? active.id : '');
    active?.scrollIntoView({ block: 'nearest' });
  };

  const paint = () => {
    shown = items(q ? q.value : '');
    list.innerHTML = shown.map((it, i) => `
      <div class="pick-row${it.cls ? ` ${it.cls}` : ''}" role="option"
           id="${listId}-${i}" data-i="${i}" data-v="${esc(it.value)}"
           ${it.style ? `style="${it.style}"` : ''}${it.title ? ` title="${esc(it.title)}"` : ''}
           aria-selected="${it.value === current}">
        ${it.html || `<span class="pick-label">${esc(it.label)}</span>`}
      </div>`).join('')
      || `<div class="pick-empty">${t('pick.none')}</div>`;
    /* opens on what is chosen now, so Enter alone changes nothing */
    at = Math.max(shown.findIndex(it => it.value === current), 0);
    mark();
    list.querySelectorAll('.pick-row').forEach(el =>
      el.addEventListener('click', () => pick(shown[Number(el.dataset.i)])));
  };

  const pick = it => {
    if (!it) return;
    closePicker();
    /* A picker standing for a form field says what it now holds; one that
       stands for an action ("Set confidence…") keeps its own wording, because
       the row was a verb and not a state. */
    if (!keepLabel) setPickerValue(btn, it);
    btn.focus();
    onPick(it.value, it);
  };

  keyHost.addEventListener('keydown', e => {
    const step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
    if (step) {
      e.preventDefault();
      at = Math.min(Math.max(at + step, 0), shown.length - 1);
      mark();
    } else if (e.key === 'Home' || e.key === 'End') {
      e.preventDefault();
      at = e.key === 'Home' ? 0 : shown.length - 1;
      mark();
    } else if (e.key === 'Enter' || (!q && e.key === ' ')) {
      e.preventDefault();
      pick(shown[at]);
    } else if (e.key === 'Escape') {
      /* stopped here: the same key closes a modal, and a panel opened from
         inside one must not take the dialog with it */
      e.preventDefault();
      e.stopPropagation();
      closePicker();
      btn.focus();
    } else if (e.key === 'Tab') {
      closePicker();
    }
  });
  q?.addEventListener('input', paint);

  /* Placed after it is in the document and measured, then clamped both ends
     -- the same reason the tip and the context menu do it that way. Below the
     button unless it does not fit and there is more room above. */
  const place = () => {
    const r = ((anchor && btn.closest(anchor)) || btn).getBoundingClientRect();
    panel.style.width = `${Math.min(Math.max(r.width, minWidth), innerWidth - 16)}px`;
    const h = panel.offsetHeight;
    const room = innerHeight - r.bottom - 8;
    const top = h <= room || r.top - 8 < room ? r.bottom + 4 : r.top - h - 4;
    panel.style.top = `${Math.max(8, Math.min(top, innerHeight - h - 8))}px`;
    panel.style.left = `${Math.max(8, Math.min(r.left, innerWidth - panel.offsetWidth - 8))}px`;
  };

  const away = e => {
    if (!panel.contains(e.target) && !btn.contains(e.target)) closePicker();
  };
  /* the list scrolls inside itself; anything else scrolling moves the button
     out from under the panel, so the panel goes */
  const scrolled = e => { if (!panel.contains(e.target)) closePicker(); };
  addEventListener('pointerdown', away, true);
  addEventListener('scroll', scrolled, true);
  addEventListener('resize', place);

  live = {
    panel, btn,
    drop: () => {
      removeEventListener('pointerdown', away, true);
      removeEventListener('scroll', scrolled, true);
      removeEventListener('resize', place);
    },
  };
  btn.setAttribute('aria-expanded', 'true');
  paint();
  place();
  keyHost.focus();
}

/* The common case: a fixed list of {value, label, html?} and a plain
   substring filter over the labels. Callers with nothing special to say
   about filtering should reach for this rather than write the closure. */
export const fixedItems = list => query => {
  const needle = query.trim().toLowerCase();
  return needle
    ? list.filter(it => (it.label || '').toLowerCase().includes(needle))
    : list;
};
