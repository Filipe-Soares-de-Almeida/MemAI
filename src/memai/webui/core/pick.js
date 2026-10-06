/* A <select> replacement whose rows can carry marks and rails: a button and a listbox popover.
   Combobox ARIA with aria-activedescendant; one panel at a time, on <body>, dismissed on next act. */

import { esc } from './dom.ts';
import { onTeardown } from './lifecycle.ts';
import { t } from '../i18n.ts';

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
  btn.classList.remove('drop-below', 'drop-above', 'drop-right');
}

/* The control. `value` rides on the button as data-v for pickerValue; `label` is its text and
   `html` the same with markup. */
export const pickerHTML = ({ id, value = '', label = '', html = '', ariaLabel = '',
                             cls = '', disabled = false, title = '' }) => `
  <button type="button" class="pick${cls ? ` ${cls}` : ''}" id="${id}" data-v="${esc(value)}"
          aria-haspopup="listbox" aria-expanded="false"${disabled ? ' disabled' : ''}
          aria-label="${esc(ariaLabel || label)}" title="${esc(title || label)}">
    <span class="pick-value">${html || esc(label)}</span>
  </button>`;

/* A button for a value from a fixed list, showing that row's face, mark included. */
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

/* `items(query)` returns { value, label, html?, cls?, style?, title? } rows; `onPick` gets the
   value and item. `anchor` places the panel against an ancestor; `align` is 'left' or 'right'. */
export function wirePicker(root, { id, items, onPick, search = 'auto',
                                   minWidth = 180, panelCls = '', keepLabel = false,
                                   anchor = '', align = 'left' }) {
  const btn = root.querySelector(`#${id}`);
  if (!btn) return;
  /* a view swap drops the button but not a panel parented to <body> */
  onTeardown(closePicker);
  const open = () =>
    openPanel(btn, { items, onPick, search, minWidth, panelCls, keepLabel, anchor, align });
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

function openPanel(btn, { items, onPick, search, minWidth, panelCls, keepLabel, anchor, align }) {
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
    /* A form-field picker shows its new value; an action picker (`keepLabel`) keeps its verb. */
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

  /* Placed, measured, then clamped vertically, below unless more room is above. drop-below /
     drop-above square the shared corner (admin.css) only while the panel touches the button. */
  const place = () => {
    const r = ((anchor && btn.closest(anchor)) || btn).getBoundingClientRect();
    panel.style.width = `${Math.min(Math.max(r.width, minWidth), innerWidth - 16)}px`;
    const h = panel.offsetHeight;
    const w = panel.offsetWidth;
    const room = innerHeight - r.bottom - 8;
    const below = h <= room || r.top - 8 < room;
    const seam = below ? r.bottom : r.top - h;
    const top = Math.max(8, Math.min(seam, innerHeight - h - 8));
    const x = align === 'right' ? r.right - w : r.left;
    panel.style.top = `${top}px`;
    panel.style.left = `${Math.max(8, Math.min(x, innerWidth - w - 8))}px`;
    const joined = Math.abs(top - seam) < 1;
    panel.classList.toggle('drop-below', joined && below);
    panel.classList.toggle('drop-above', joined && !below);
    panel.classList.toggle('drop-right', align === 'right');
    /* The button's half of the seam, only when measured against the button and not an `anchor`. */
    btn.classList.toggle('drop-below', joined && below && !anchor);
    btn.classList.toggle('drop-above', joined && !below && !anchor);
    btn.classList.toggle('drop-right', align === 'right');
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

/* The common case: a fixed list with a plain substring filter over the labels. */
export const fixedItems = list => query => {
  const needle = query.trim().toLowerCase();
  return needle
    ? list.filter(it => (it.label || '').toLowerCase().includes(needle))
    : list;
};
