/* Formatting and DOM helpers with no API access and no state. Locals are never named `t`, which
   would shadow the i18n translator. */

import { I18N } from '../i18n.ts';

export const $ = (s: string): HTMLElement | null => document.querySelector<HTMLElement>(s);

const ESCAPES: Record<string, string> =
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

export const esc = (s: unknown): string => String(s ?? '').replace(/[&<>"']/g, c => ESCAPES[c]);

export const cssVar = (name: string): string =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();

export const fmtInt = (n: unknown): string => Number(n || 0).toLocaleString(I18N.numberLocale);

export const fmtBytes = (bytes: unknown): string => {
  const b = Number(bytes || 0);
  if (b < 1024) return `${b} B`;
  if (b < 1048576) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1048576).toFixed(1)} MB`;
};

const MONTHS = I18N.months;

const pad2 = (n: number): string => String(n).padStart(2, '0');

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso.slice(0, 16);
  /* The year shows only when it is not this one: an audit trail needs it on last year's rows,
     and on every row it is four characters of noise. */
  const year = d.getFullYear() === new Date().getFullYear() ? '' : ` ${d.getFullYear()}`;
  return `${pad2(d.getDate())} ${MONTHS[d.getMonth()]}${year} · ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

/* A LOCAL calendar day as YYYY-MM-DD: stored timestamps are UTC, and a run staged at 23:30
   belongs to the reader's evening, not to the next morning in Greenwich. */
export const dayKey = (d: Date): string =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

export const monthKey = (d: Date): string =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;

/* dayKey's inverse, as a local date: `new Date(key)` parses a bare date as UTC and lands on the
   previous day west of Greenwich. */
export function fromKey(key: unknown): Date {
  const [y, m, d] = String(key).split('-').map(Number);
  return new Date(y, (m || 1) - 1, d || 1);
}

/* A calendar day (YYYY-MM-DD) for an axis label, with fmtDate's month names. */
export const fmtDay = (key: string): string => {
  const [, m, d] = String(key).split('-');
  return MONTHS[Number(m) - 1] ? `${d} ${MONTHS[Number(m) - 1]}` : key;
};

export function fmtAgo(iso: string | null | undefined): string {
  /* new Date(null) is the epoch, not an invalid date, so the empty check comes first */
  if (!iso) return '—';
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms)) return '';
  const m = Math.floor(ms / 60000);
  if (m < 1) return I18N.t('ago.now');
  if (m < 60) return I18N.t('ago.min', { n: m });
  const h = Math.floor(m / 60);
  if (h < 48) return I18N.t('ago.hour', { n: h });
  return I18N.t('ago.day', { n: Math.floor(h / 24) });
}

export const debounce = <A extends unknown[]>(fn: (...a: A) => void, ms: number) => {
  let timer: ReturnType<typeof setTimeout> | undefined;
  return (...a: A): void => { clearTimeout(timer); timer = setTimeout(() => fn(...a), ms); };
};
