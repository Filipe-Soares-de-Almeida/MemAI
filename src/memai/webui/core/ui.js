/* Floating chrome: toasts, the hover tip, modals, the context menu.

   Everything here is parented outside #view, so it survives a view swap
   and has to be dismissed deliberately -- see the tipHide() calls at the
   top of openModal and openCtxMenu, and lifecycle.js for the general
   case. */

import { $, esc } from './dom.js';
import { t } from '../i18n.js';
import { icon } from './icons.js';

/* ─── toasts ──────────────────────────────────────────────────────────────
   Thirty-five call sites push SEVEN different shapes of message through this
   one function: a bare confirmation, a counted one, an object being named, an
   irreversible deletion, a partial failure, a raw API error, and -- until this
   round -- a form-validation nag fired four hundred pixels from the field that
   was wrong. It used to answer all seven with the same 3.6-second rectangle
   that could not be dismissed, could not be acted on, offered no Undo for any
   of the reversible things it reported, and stacked without a limit.

   What a kind means now:

     ''      nothing happened. No mark, no hue -- a neutral result is neutral,
             and two call sites used to get the BRAND colour for this.
     'ok'    it worked.
     'warn'  it partly worked; `detail` says which part did not.
     'bad'   it failed. Never auto-dismisses, because a failure is something to
             read and act on, and it already carries role="alert".

   State is carried by the MARK's shape and by the container -- never by a
   coloured strip on one edge. The three marks in core/icons.js are already a
   closed ring, an open ring and a crossed ring, so the three states are told
   apart on a greyscale monitor.

   opts:
     detail  a second line: an error message, a uid, the ids that failed. Keeps
             the headline short and stable while the specifics still ship.
     action  {label, run} -- Undo, Retry, View. Extends the timer, because an
             Undo you cannot reach in time is not an Undo.
     sticky  force no auto-dismiss ('bad' implies it).                        */

const MARK = { ok: 'confirmed', warn: 'unverified', bad: 'contradicted' };
const LIFE = { '': 3200, ok: 3200, warn: 5000 };
const VISIBLE_MAX = 3;

/* arrivals past VISIBLE_MAX wait here rather than pushing the oldest off the
   top of a container that does not scroll */
const waiting = [];
let escWired = false;
let stackSize = null;

/* The stack floats over content that scrolls, so it publishes its own height
   the way the bulk bar publishes its own: .view and .drawer-body reserve that
   much extra bottom padding, and the toast lands on padding the reader can
   scroll past rather than on the last of the content. Measured: with the record
   drawer open at 1280x800 the stack overlaps the drawer's own body by 400x50px,
   and the drawer is exactly where most toasts fire. */
function publishStackHeight(host) {
  const h = host.children.length ? host.offsetHeight + 10 : 0;
  document.documentElement.style.setProperty('--toast-h', `${h}px`);
}

export function toast(msg, kind = '', opts = {}) {
  const host = $('#toasts');
  /* Collapse a repeat instead of stacking it: applying six suggestions fires
     six identical toasts (optimization.js runs one per apply), which used to
     push the earliest out of sight with nothing to scroll. */
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
  /* A failure interrupts; a confirmation waits its turn. The container is
     aria-live="polite", which is right for "archived" and wrong for "the
     write was rejected" -- role="alert" on the node itself is assertive. */
  el.setAttribute('role', kind === 'bad' ? 'alert' : 'status');

  el.innerHTML = `${MARK[kind] ? `<span class="toast-mark">${icon(MARK[kind])}</span>` : ''}
    <div class="toast-text">
      <div class="toast-line"><span class="toast-msg"></span><span class="toast-n" hidden></span></div>
      ${opts.detail ? '<span class="toast-detail"></span>' : ''}
    </div>
    ${opts.action ? '<button type="button" class="btn btn-sm btn-ghost" data-act></button>' : ''}
    <button type="button" class="icon-btn" data-close
            aria-label="${esc(t('common.close'))}">${icon('close')}</button>`;

  /* textContent, never innerHTML, for every caller-supplied string. Five
     catalog entries carry <b>/<code>, and memories.js records a shipped bug
     where one of them printed as literal tags. The body of a toast is TEXT;
     emphasis is a node this function builds, never a string it parses. */
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

/* Every failure in this app used to say only what the API said: an un-i18n'd
   string of unbounded length, in a corner, gone in 3.6 seconds, with no way to
   re-read it and with nothing naming what had actually failed -- a pt-BR user
   got an English sentence from the server and no context for it.

   The headline is a stable translated sentence naming the ACTION that failed;
   err.message drops to the detail line, still there for whoever needs it and no
   longer the whole message. 'bad' does not auto-dismiss, so it waits to be
   read, and `opts.action` carries a Retry where the call is idempotent. */
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

/* ─── hover tip ───────────────────────────────────────────────────────
   Positioned in an animation frame, and measured only when the content
   changes. Writing innerHTML and reading getBoundingClientRect on the next
   line forces a synchronous layout, and both canvases call this on every
   pointermove -- so a slow drag across a card used to pay for a reflow per
   pixel. The size only changes when the words do. */

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

export function copyUid(uid) {
  /* no clipboard access (a non-secure origin other than loopback, or a
     browser that withholds it) has to say so rather than do nothing */
  if (!navigator.clipboard) { toast(t('toast.copyUnavailable'), 'bad'); return; }
  navigator.clipboard.writeText(uid)
    .then(() => toast(t('toast.uidCopied', { uid }), 'ok'))
    .catch(() => toast(t('toast.copyUnavailable'), 'bad'));
}

/* ─── toggle state ────────────────────────────────────────────────────
   A control that stays pressed says so in the accessibility tree too. The
   UI marked these with a class alone, so the state existed only for eyes. */

export const setPressed = (el, on) => {
  if (!el) return;
  el.setAttribute('aria-pressed', on ? 'true' : 'false');
  el.classList.toggle('btn-solid', !!on);
};

/* Everything behind the record drawer, taken out of the tab order and out of
   the accessibility tree while it is open. The drawer sits over the whole
   app behind a scrim, so tabbing into the list underneath -- which is what
   used to happen -- moves an invisible caret through covered content.

   `inert` and not aria-hidden: it does both, and it also stops a click. */
export function inertBackground(on) {
  for (const sel of ['.rail', '.frame']) {
    const el = document.querySelector(sel);
    if (el) el.toggleAttribute('inert', !!on);
  }
}

/* ─── modal machinery ───────────────────────────────────────────────────
   A modal takes the screen: it is labelled aria-modal, it keeps Tab
   inside itself, and closing it puts the caret back where it was. The
   context menu below is the deliberate opposite -- see its own note. */

const FOCUSABLE = ['a[href]', 'button:not([disabled])', 'input:not([disabled])',
                   'textarea:not([disabled])', 'select:not([disabled])',
                   'details > summary', '[tabindex]:not([tabindex="-1"])'].join(',');

let modalDrop = null, modalOpener = null;

export function openModal({ title, bodyHTML, footHTML }) {
  /* captured before closeModal, which restores focus to whatever the
     PREVIOUS modal was opened from -- and ignored if the caller was itself
     inside a modal, since that element is about to be removed */
  const opener = document.activeElement;
  closeModal();
  /* A hover tip outlives the pointer that summoned it -- open a modal from
     the canvas (or from a context menu over a card) and the tip is left
     floating on top of the dialog, because nothing moved off the card to
     dismiss it. Whatever is opening now owns the screen. */
  tipHide();
  modalOpener = opener instanceof HTMLElement && document.contains(opener)
    && !opener.closest('.modal-scrim') ? opener : null;

  const scrim = document.createElement('div');
  scrim.className = 'modal-scrim';
  scrim.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-label="${esc(title)}">
    <div class="modal-head">${title}</div>
    <div class="modal-body">${bodyHTML}</div>
    <div class="modal-foot">${footHTML || ''}</div>
  </div>`;
  scrim.addEventListener('mousedown', e => { if (e.target === scrim) closeModal(); });
  $('#modalRoot').appendChild(scrim);

  const dialog = scrim.querySelector('.modal');
  /* Tab wraps inside the dialog. Recomputed per keypress rather than
     cached: a modal body can gain and lose controls while it is open (the
     new-memory form swaps a textarea for a title field). */
  const trap = e => {
    if (e.key !== 'Tab') return;
    const items = [...dialog.querySelectorAll(FOCUSABLE)].filter(el => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    const inside = dialog.contains(document.activeElement);
    if (e.shiftKey && (!inside || document.activeElement === first)) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && (!inside || document.activeElement === last)) {
      e.preventDefault(); first.focus();
    }
  };
  addEventListener('keydown', trap, true);
  modalDrop = () => removeEventListener('keydown', trap, true);

  const first = dialog.querySelector('input, textarea, select, button');
  first && first.focus();
  return scrim;
}

export function closeModal() {
  modalDrop?.();
  modalDrop = null;
  $('#modalRoot').innerHTML = '';
  /* the opener may itself have been re-rendered away while the modal was
     open (saving from the drawer repaints it), hence the guard */
  if (modalOpener && document.contains(modalOpener)) modalOpener.focus();
  modalOpener = null;
}

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

export const modalOpen = () => $('#modalRoot').children.length > 0;

/* ─── context menu ───────────────────────────────────────────────────
   A right-click menu, not a modal: it has no scrim and no focus trap,
   because it must be dismissable by clicking the thing you actually
   wanted. Items are `{label, run, danger}` or `{sep: true}`. */

let ctxMenu = null, ctxDrop = null;

export function closeCtxMenu() {
  ctxDrop?.();
  ctxDrop = null;
  ctxMenu?.remove();
  ctxMenu = null;
}

export function openCtxMenu(x, y, items) {
  closeCtxMenu();
  tipHide();            /* same reason as openModal */
  const live = items.filter(Boolean);
  if (!live.some(i => !i.sep)) return;
  const el = document.createElement('div');
  el.className = 'ctx-menu';
  el.innerHTML = live.map((it, i) => it.sep
    ? '<div class="ctx-sep"></div>'
    : `<button class="ctx-item${it.danger ? ' danger' : ''}" data-i="${i}">${esc(it.label)}</button>`
  ).join('');
  el.style.left = `${x}px`;
  el.style.top = `${y}px`;
  document.body.appendChild(el);
  ctxMenu = el;
  /* measured after it is in the document, then clamped both ends -- the
     same reason tipShow does it that way */
  const r = el.getBoundingClientRect();
  el.style.left = `${Math.max(8, Math.min(x, innerWidth - r.width - 8))}px`;
  el.style.top = `${Math.max(8, Math.min(y, innerHeight - r.height - 8))}px`;

  el.querySelectorAll('[data-i]').forEach(b => b.addEventListener('click', () => {
    const it = live[Number(b.dataset.i)];
    closeCtxMenu();
    it.run?.();
  }));

  /* dismissed by whatever you do next -- clicking elsewhere, Escape,
     zooming the canvas. closeCtxMenu takes the listeners off with it. */
  const away = e => { if (!el.contains(e.target)) closeCtxMenu(); };
  const key = e => { if (e.key === 'Escape') closeCtxMenu(); };
  addEventListener('mousedown', away, true);
  addEventListener('keydown', key, true);
  addEventListener('wheel', closeCtxMenu, true);
  ctxDrop = () => {
    removeEventListener('mousedown', away, true);
    removeEventListener('keydown', key, true);
    removeEventListener('wheel', closeCtxMenu, true);
  };
}
