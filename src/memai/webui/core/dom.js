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

const pad2 = n => String(n).padStart(2, '0');

export function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return iso.slice(0, 16);
  /* The year is printed only when it is not this one. Without it at all, an
     edit from last March and one from this March read identically -- which
     in an audit trail is the difference between a record and a guess. With
     it on every row it is four characters of noise on the common case. */
  const year = d.getFullYear() === new Date().getFullYear() ? '' : ` ${d.getFullYear()}`;
  return `${pad2(d.getDate())} ${MONTHS[d.getMonth()]}${year} · ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

/* A calendar day (YYYY-MM-DD), for an axis label. Uses the same month names
   as fmtDate: this was a slice into the string, which numbered the month in
   every language whether or not that language numbers months. */
export const fmtDay = key => {
  const [, m, d] = String(key).split('-');
  return MONTHS[Number(m) - 1] ? `${d} ${MONTHS[Number(m) - 1]}` : key;
};

export function fmtAgo(iso) {
  /* The empty check is not redundant with the NaN one below: new Date(null)
     is the epoch, not an invalid date, so a missing timestamp used to come
     out as "20xxx d" rather than as nothing at all. */
  if (!iso) return '—';
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms)) return '';
  const m = Math.floor(ms / 60000);
  /* the units go through the catalog like every other word on screen --
     'now' was translated and 'min', 'h' and 'd' were not */
  if (m < 1) return I18N.t('ago.now');
  if (m < 60) return I18N.t('ago.min', { n: m });
  const h = Math.floor(m / 60);
  if (h < 48) return I18N.t('ago.hour', { n: h });
  return I18N.t('ago.day', { n: Math.floor(h / 24) });
}

export const debounce = (fn, ms) => {
  let timer;
  return (...a) => { clearTimeout(timer); timer = setTimeout(() => fn(...a), ms); };
};
