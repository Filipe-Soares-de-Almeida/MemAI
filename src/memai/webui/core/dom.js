/* Formatting and DOM helpers shared by every view.

   Nothing here talks to the API or owns state -- if a helper needs
   either, it belongs in api.js, ui.js or shared.js instead.

   Note on naming: `t` is the i18n translator, imported all over this
   codebase. Locals are never called `t` -- a timer is `timer`, a memory
   type is `tp` -- because shadowing the translator inside a helper
   breaks the next person who reaches for t() in that scope. */

import { I18N } from '../i18n.js';

export const $ = s => document.querySelector(s);

export const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export const cssVar = name =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();

export const fmtInt = n => Number(n || 0).toLocaleString(I18N.numberLocale);

export const fmtBytes = b => {
  b = Number(b || 0);
  if (b < 1024) return `${b} B`;
  if (b < 1048576) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1048576).toFixed(1)} MB`;
};

const MONTHS = I18N.months;

export function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return iso.slice(0, 16);
  return `${String(d.getDate()).padStart(2, '0')} ${MONTHS[d.getMonth()]} · ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export function fmtAgo(iso) {
  /* The empty check is not redundant with the NaN one below: new Date(null)
     is the epoch, not an invalid date, so a missing timestamp used to come
     out as "20xxx d" rather than as nothing at all. */
  if (!iso) return '—';
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms)) return '';
  const m = Math.floor(ms / 60000);
  if (m < 1) return I18N.t('ago.now');
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h`;
  return `${Math.floor(h / 24)} d`;
}

export const debounce = (fn, ms) => {
  let timer;
  return (...a) => { clearTimeout(timer); timer = setTimeout(() => fn(...a), ms); };
};
