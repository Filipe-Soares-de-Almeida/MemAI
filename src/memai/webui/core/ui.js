/* Floating chrome: toasts, the hover tip, modals, the context menu.

   Everything here is parented outside #view, so it survives a view swap
   and has to be dismissed deliberately -- see the tipHide() calls at the
   top of openModal and openCtxMenu, and lifecycle.js for the general
   case. */

import { $, esc } from './dom.js';
import { t } from '../i18n.js';

/* ─── toasts ────────────────────────────────────────────────────────── */

export function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 350); }, 3600);
}

/* ─── hover tip ─────────────────────────────────────────────────────── */

export function tipShow(html, x, y) {
  const tip = $('#tip');
  tip.innerHTML = html;
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  /* clamp both ends: a wrapped tip can be tall enough that pushing it up
     to fit would otherwise take it off the top of the window */
  tip.style.left = `${Math.max(10, Math.min(x + 14, innerWidth - r.width - 10))}px`;
  tip.style.top = `${Math.max(10, Math.min(y + 14, innerHeight - r.height - 10))}px`;
}

export function tipHide() { $('#tip').hidden = true; }

export function copyUid(uid) {
  navigator.clipboard?.writeText(uid).then(() => toast(t('toast.uidCopied', { uid }), 'ok'));
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
