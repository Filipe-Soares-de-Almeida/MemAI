/* Floating chrome: toasts, the hover tip, modals, the context menu. All of it lives outside
   #view, so it survives a view swap and is dismissed deliberately (see lifecycle.ts). */

import { $, esc } from './dom.ts';
import { t } from '../i18n.ts';
import { icon } from './icons.js';

/* ─── toasts: kind '' neutral, 'ok', 'warn' (partial, `detail` says what), 'bad' (sticky alert).
   opts: detail (second line), action {label, run} (extends the timer), sticky. */

const MARK = { ok: 'confirmed', warn: 'unverified', bad: 'contradicted' };
const LIFE = { '': 3200, ok: 3200, warn: 5000 };
const VISIBLE_MAX = 3;

/* arrivals past VISIBLE_MAX wait here rather than pushing the oldest off the
   top of a container that does not scroll */
const waiting = [];
let escWired = false;
let stackSize = null;

/* The stack publishes its height so .view and the record grid reserve that much bottom padding,
   and a toast lands on scrollable padding instead of over the last content. */
function publishStackHeight(host) {
  const h = host.children.length ? host.offsetHeight + 10 : 0;
  document.documentElement.style.setProperty('--toast-h', `${h}px`);
}

export function toast(msg, kind = '', opts = {}) {
  const host = $('#toasts');
  /* Collapse a repeat instead of stacking it: a batch of applies fires identical toasts. */
  const newest = host.lastElementChild;
  if (newest && !newest.dataset.going && !opts.action
      && newest.dataset.msg === msg && newest.dataset.kind === kind) {
    const n = Number(newest.dataset.count || 1) + 1;
    newest.dataset.count = String(n);
    const tally = newest.querySelector('.toast-n');
    tally.textContent = `×${n}`;
    tally.hidden = false;
    arm(newest);
    return newest;
  }
  if (host.children.length >= VISIBLE_MAX) { waiting.push([msg, kind, opts]); return null; }
  return mount(host, msg, kind, opts);
}

function mount(host, msg, kind, opts) {
  const el = document.createElement('div');
  el.className = `toast${kind ? ` ${kind}` : ''}`;
  el.dataset.msg = msg;
  el.dataset.kind = kind;
  if (opts.sticky ?? kind === 'bad') el.dataset.sticky = '1';
  if (opts.action) el.dataset.acting = '1';
  /* The container is aria-live="polite"; a failure needs role="alert" to interrupt. */
  el.setAttribute('role', kind === 'bad' ? 'alert' : 'status');

  el.innerHTML = `${MARK[kind] ? `<span class="toast-mark">${icon(MARK[kind])}</span>` : ''}
    <div class="toast-text">
      <div class="toast-line"><span class="toast-msg"></span><span class="toast-n" hidden></span></div>
      ${opts.detail ? '<span class="toast-detail"></span>' : ''}
    </div>
    ${opts.action ? '<button type="button" class="btn btn-sm btn-ghost" data-act></button>' : ''}
    <button type="button" class="icon-btn" data-close
            aria-label="${esc(t('common.close'))}">${icon('close')}</button>`;

  /* textContent, never innerHTML, for caller strings: catalog entries with <b>/<code> would print
     as literal tags. Emphasis is a node built here. */
  el.querySelector('.toast-msg').textContent = msg;
  if (opts.detail) el.querySelector('.toast-detail').textContent = opts.detail;
  if (opts.action) {
    const btn = el.querySelector('[data-act]');
    btn.textContent = opts.action.label;
    btn.addEventListener('click', () => { drop(el); opts.action.run?.(); });
  }
  el.querySelector('[data-close]').addEventListener('click', () => drop(el));

  /* focusin as well as pointerenter: someone tabbing to the Undo must not lose
     it halfway to the button */
  for (const ev of ['pointerenter', 'focusin']) el.addEventListener(ev, () => stop(el));
  for (const ev of ['pointerleave', 'focusout']) el.addEventListener(ev, () => arm(el));

  host.appendChild(el);
  publishStackHeight(host);
  if (!stackSize) {
    /* a message that wraps to two lines changes the height without changing the
       count, so observe rather than only counting */
    stackSize = new ResizeObserver(() => publishStackHeight(host));
    stackSize.observe(host);
  }
  if (!escWired) {
    escWired = true;
    /* Escape closes the toast the caret is inside, and only that one, so it
       never competes with the drawer or a modal for the same key. */
    host.addEventListener('keydown', e => {
      if (e.key !== 'Escape') return;
      const hit = e.target.closest?.('.toast');
      if (!hit) return;
      e.stopPropagation();
      drop(hit);
    });
  }
  arm(el);
  return el;
}

/* The headline is a translated sentence naming the action that failed; err.message goes to the
   detail line. 'bad' waits to be read, and `opts.action` can carry a Retry. */
export const failed = (key, err, opts = {}) =>
  toast(t(key), 'bad', { detail: err?.message || '', ...opts });

function stop(el) { clearTimeout(Number(el.dataset.timer)); }

function arm(el) {
  stop(el);
  if (el.dataset.sticky || el.dataset.going) return;
  const ms = el.dataset.acting ? 6000 : (LIFE[el.dataset.kind] ?? 3200);
  el.dataset.timer = String(setTimeout(() => drop(el), ms));
}

function drop(el) {
  if (el.dataset.going) return;
  stop(el);
  el.dataset.going = '1';
  el.classList.add('out');
  setTimeout(() => {
    const host = el.parentElement;
    el.remove();
    const next = waiting.shift();
    if (next && host) mount(host, ...next);
    else if (host) publishStackHeight(host);
  }, 300);
}

/* ─── hover tip: positioned in an animation frame and measured only when its content changes,
   since both canvases call it on every pointermove. */

let tipHtml = '', tipBox = null, tipFrame = 0;
const tipAt = { x: 0, y: 0 };

export function tipShow(html, x, y) {
  const tip = $('#tip');
  tipAt.x = x; tipAt.y = y;
  if (html !== tipHtml) {
    tip.innerHTML = html;
    tipHtml = html;
    tipBox = null;
  }
  tip.hidden = false;
  if (tipFrame) return;
  tipFrame = requestAnimationFrame(() => {
    tipFrame = 0;
    if (tip.hidden) return;
    if (!tipBox) {
      const r = tip.getBoundingClientRect();
      tipBox = { w: r.width, h: r.height };
    }
    /* clamp both ends: a wrapped tip can be tall enough that pushing it up
       to fit would otherwise take it off the top of the window */
    tip.style.left = `${Math.max(10, Math.min(tipAt.x + 14, innerWidth - tipBox.w - 10))}px`;
    tip.style.top = `${Math.max(10, Math.min(tipAt.y + 14, innerHeight - tipBox.h - 10))}px`;
  });
}

export function tipHide() {
  $('#tip').hidden = true;
  tipHtml = '';
  tipBox = null;
}

export function copyText(text, message) {
  /* no clipboard access (a non-secure origin other than loopback, or a
     browser that withholds it) has to say so rather than do nothing */
  if (!navigator.clipboard) { toast(t('toast.copyUnavailable'), 'bad'); return; }
  navigator.clipboard.writeText(text)
    .then(() => toast(message, 'ok'))
    .catch(() => toast(t('toast.copyUnavailable'), 'bad'));
}

export const copyUid = uid => copyText(uid, t('toast.uidCopied', { uid }));

export const copyCode = text => copyText(text, t('toast.codeCopied'));

/* ─── a keyboard shortcut, each key a keycap, then what it does */

const MAC = /Mac|iPhone|iPad/.test(navigator.userAgentData?.platform || navigator.platform || '');
export const MOD_KEY = MAC ? '⌘' : 'Ctrl';

export const keysHTML = (keys, action) => `<span class="key-hint">${keys
  .map(k => `<kbd>${esc(k)}</kbd>`).join('<span class="key-plus" aria-hidden="true">+</span>')}
  <span class="key-what">${esc(action)}</span></span>`;

export const saveKeysHTML = action => keysHTML([MOD_KEY, 'Enter'], action);

/* ─── toggle state: a pressed .btn also sets aria-pressed; a .seg button handles its own. */

export const setPressed = (el, on) => {
  if (!el) return;
  el.setAttribute('aria-pressed', on ? 'true' : 'false');
  el.classList.toggle('btn-solid', !!on);
};

/* Takes the app bar and view out of tab order and the accessibility tree while a modal is open.
   `inert`, not aria-hidden, because it also stops clicks. */
function inertBackground(on) {
  for (const sel of ['.appbar', '.frame']) {
    const el = document.querySelector(sel);
    if (el) el.toggleAttribute('inert', !!on);
  }
}

/* ─── modal machinery: modals STACK; openModal pushes, closeModal and Escape pop one level, and
   only the top modal is live, with one keydown listener for the whole stack. */

const FOCUSABLE = ['a[href]', 'button:not([disabled])', 'input:not([disabled])',
                   'textarea:not([disabled])', 'select:not([disabled])',
                   'details > summary', '[tabindex]:not([tabindex="-1"])'].join(',');

const modals = [];        /* [{ scrim, opener }] -- innermost last */
let trapWired = false;

const topModal = () => modals[modals.length - 1] || null;

const focusInto = dialog => {
  const first = dialog.querySelector('input, textarea, select, button');
  (first || dialog).focus();
};

export function openModal({ title, bodyHTML, footHTML, ariaLabel,
                            wide = false, tall = false }) {
  /* Whatever had the caret, including a control in the modal underneath, gets it back. */
  const opener = document.activeElement;
  /* A hover tip would otherwise float over the dialog; whatever opens now owns the screen. */
  tipHide();

  const scrim = document.createElement('div');
  scrim.className = `modal-scrim${modals.length ? ' stacked' : ''}`;
  /* `title` may carry markup, so the accessible name prefers ariaLabel */
  scrim.innerHTML = `<div class="modal${wide ? ' modal-wide' : ''}${tall ? ' modal-tall' : ''}"
       role="dialog" aria-modal="true" aria-label="${esc(ariaLabel || title)}" tabindex="-1">
    <div class="modal-head">${title}</div>
    <div class="modal-body">${bodyHTML}</div>
    <div class="modal-foot">${footHTML || ''}</div>
  </div>`;
  scrim.addEventListener('mousedown', e => { if (e.target === scrim) closeModal(); });
  $('#modalRoot').appendChild(scrim);
  if (!modals.length) inertBackground(true);
  modals.push({ scrim, opener: opener instanceof HTMLElement ? opener : null });

  if (!trapWired) {
    trapWired = true;
    /* Tab wraps inside the topmost dialog, recomputed per keypress since a modal body can
       gain and lose controls while open. */
    addEventListener('keydown', e => {
      const top = topModal();
      if (e.key !== 'Tab' || !top) return;
      const dialog = top.scrim.querySelector('.modal');
      const items = [...dialog.querySelectorAll(FOCUSABLE)].filter(el => el.offsetParent !== null);
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      const inside = dialog.contains(document.activeElement);
      if (e.shiftKey && (!inside || document.activeElement === first)) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && (!inside || document.activeElement === last)) {
        e.preventDefault(); first.focus();
      }
    }, true);
  }
  focusInto(scrim.querySelector('.modal'));
  return scrim;
}

/* Closes the top level only. */
export function closeModal() {
  const top = modals.pop();
  if (!top) return;
  top.scrim.remove();
  const back = topModal();
  /* released before the focus call below: an inert subtree cannot take
     focus, so restoring it first would silently do nothing */
  if (!back) inertBackground(false);
  /* the opener may have been re-rendered away; then the form underneath takes the caret */
  if (top.opener && document.contains(top.opener)) top.opener.focus();
  else if (back) focusInto(back.scrim.querySelector('.modal'));
}

export const modalOpen = () => modals.length > 0;

export function confirmModal({ title, body, okLabel = t('common.confirm'), danger = false }) {
  return new Promise(resolve => {
    const m = openModal({
      title,
      bodyHTML: `<div>${body}</div>`,
      footHTML: `<button class="btn" data-x>${t('common.cancel')}</button>
                 <button class="btn ${danger ? 'btn-danger' : 'btn-solid'}" data-ok>${esc(okLabel)}</button>`,
    });
    m.querySelector('[data-x]').onclick = () => { closeModal(); resolve(false); };
    m.querySelector('[data-ok]').onclick = () => { closeModal(); resolve(true); };
  });
}

export function promptModal({ title, body = '', label, placeholder = '', value = '',
                             okLabel = t('common.confirm'), danger = false }) {
  return new Promise(resolve => {
    const m = openModal({
      title,
      bodyHTML: `${body ? `<div>${body}</div>` : ''}
        <div class="field"><label>${esc(label)}</label>
        <input type="text" data-in value="${esc(value)}" placeholder="${esc(placeholder)}"></div>`,
      footHTML: `<button class="btn" data-x>${t('common.cancel')}</button>
                 <button class="btn ${danger ? 'btn-danger' : 'btn-solid'}" data-ok>${esc(okLabel)}</button>`,
    });
    const input = m.querySelector('[data-in]');
    input.focus();
    input.addEventListener('keydown', e => { if (e.key === 'Enter') m.querySelector('[data-ok]').click(); });
    m.querySelector('[data-x]').onclick = () => { closeModal(); resolve(null); };
    m.querySelector('[data-ok]').onclick = () => { const v = input.value; closeModal(); resolve(v); };
  });
}

/* Confirms an irreversible act: the button stays disabled until the field holds the printed
   phrase exactly. Resolves true on the button, false on Cancel. */
export function typedConfirmModal({ title, bodyHTML = '', phrase, okLabel }) {
  return new Promise(resolve => {
    const m = openModal({
      title,
      bodyHTML: `${bodyHTML}
        <div class="dz-type">${t('dz.typeThis', { phrase: `<code>${esc(phrase)}</code>` })}</div>
        <div class="dz-row">
          <input type="text" data-phrase aria-label="${esc(t('dz.phrase.aria'))}" autocomplete="off">
        </div>
        <div class="dz-state" data-state role="status"></div>`,
      footHTML: `<button class="btn" data-x>${t('common.cancel')}</button>
                 <button class="btn btn-danger" data-ok disabled>${esc(okLabel)}</button>`,
    });
    const field = m.querySelector('[data-phrase]');
    const state = m.querySelector('[data-state]');
    const ok = m.querySelector('[data-ok]');
    field.focus();
    field.addEventListener('input', () => {
      const match = field.value === phrase;
      ok.disabled = !match;
      state.className = `dz-state${match ? ' armed' : ''}`;
      state.textContent = match ? t('dz.armed') : field.value ? t('dz.mismatch') : '';
    });
    m.querySelector('[data-x]').onclick = () => { closeModal(); resolve(false); };
    ok.onclick = () => { closeModal(); resolve(true); };
  });
}

/* ─── menu of actions: no scrim and no focus trap, so a click elsewhere dismisses it. Items are
   `{label, run, danger}` or `{sep: true}`; openCtxMenu lands at a pointer, openDropMenu at a control. */

let ctxMenu = null, ctxDrop = null;

export function closeCtxMenu() {
  ctxDrop?.();
  ctxDrop = null;
  ctxMenu?.remove();
  ctxMenu = null;
}

/* At a point -- a right-click, or a canvas the pointer is over. */
export const openCtxMenu = (x, y, items) => openMenu(items, { x, y });

/* Under the button that opened it, flipped when it does not fit, right-aligned when `align`
   says so for a control at the end of a row. */
export function openDropMenu(btn, items, { align = 'left' } = {}) {
  return openMenu(items, { btn, align });
}

function openMenu(items, at) {
  closeCtxMenu();
  tipHide();            /* same reason as openModal */
  const live = items.filter(Boolean);
  if (!live.some(i => !i.sep)) return;
  const el = document.createElement('div');
  el.className = 'ctx-menu';
  /* an entry with a `note` is shown but cannot run: the note is its visible
     reason, and the button's description */
  el.innerHTML = live.map((it, i) => it.sep
    ? '<div class="ctx-sep"></div>'
    : it.note
      ? `<button class="ctx-item${it.danger ? ' danger' : ''}" data-i="${i}" aria-disabled="true"
                 aria-labelledby="ctxL${i}" aria-describedby="ctxN${i}" title="${esc(it.note)}">
           <span id="ctxL${i}">${esc(it.label)}</span><span class="ctx-note" id="ctxN${i}">${esc(it.note)}</span></button>`
      : `<button class="ctx-item${it.danger ? ' danger' : ''}" data-i="${i}">${esc(it.label)}</button>`
  ).join('');
  document.body.appendChild(el);
  ctxMenu = el;
  place(el, at);

  /* where focus goes when the menu closes with it inside: the button that
     opened it, else whatever held it before, if either is still on the page */
  const prior = document.activeElement;
  const holds = () => el.contains(document.activeElement);
  const restore = () => [at.btn, prior].find(n => n && n !== document.body && document.contains(n))?.focus();

  el.querySelectorAll('[data-i]').forEach(b => b.addEventListener('click', () => {
    const it = live[Number(b.dataset.i)];
    if (it.note) return;
    const held = holds();
    closeCtxMenu();
    if (held) restore();
    it.run?.();
  }));

  /* dismissed by whatever you do next -- clicking elsewhere, Escape,
     zooming the canvas. closeCtxMenu takes the listeners off with it. */
  const away = e => { if (!el.contains(e.target)) closeCtxMenu(); };
  /* a menu dropped from a button takes the keyboard: focus goes to its first
     entry, the arrows walk the entries, and Escape or Tab hands focus back */
  const entries = [...el.querySelectorAll('.ctx-item')];
  const key = e => {
    if (e.key === 'Escape') {
      const held = holds();
      closeCtxMenu();
      if (held || at.btn) restore();
      return;
    }
    if (!at.btn) return;
    const i = entries.indexOf(document.activeElement);
    const to = { ArrowDown: i + 1, ArrowUp: i < 0 ? entries.length - 1 : i - 1,
                 Home: 0, End: entries.length - 1 }[e.key];
    if (to !== undefined) {
      e.preventDefault();
      entries[(to + entries.length) % entries.length].focus();
    } else if (e.key === 'Tab') {
      e.preventDefault();
      closeCtxMenu();
      restore();
    }
  };
  const wheel = () => {
    const held = holds();
    closeCtxMenu();
    if (held) restore();
  };
  addEventListener('mousedown', away, true);
  addEventListener('keydown', key, true);
  addEventListener('wheel', wheel, true);
  if (at.btn) entries[0]?.focus();
  ctxDrop = () => {
    removeEventListener('mousedown', away, true);
    removeEventListener('keydown', key, true);
    removeEventListener('wheel', wheel, true);
  };
}

/* Measured after it is in the document, then clamped both ends -- the same
   reason tipShow does it that way. */
function place(el, { x, y, btn, align }) {
  const box = el.getBoundingClientRect();
  const at = btn ? dropPoint(btn.getBoundingClientRect(), box, align) : { x, y };
  el.style.left = `${Math.max(8, Math.min(at.x, innerWidth - box.width - 8))}px`;
  el.style.top = `${Math.max(8, Math.min(at.y, innerHeight - box.height - 8))}px`;
}

/* Below the button unless there is more room above; a 4px gap, since a menu lists actions
   rather than the control's value. */
function dropPoint(r, box, align) {
  const room = innerHeight - r.bottom - 8;
  const below = box.height <= room || r.top - 8 < room;
  return {
    x: align === 'right' ? r.right - box.width : r.left,
    y: below ? r.bottom + 4 : r.top - box.height - 4,
  };
}
