/* The month the optimization runs are laid out on: runs grouped by the reader's local day, the
   grid of whole weeks, and what a month came to. */

import { dayKey, fromKey, monthKey } from '../../core/dom.ts';
import { I18N } from '../../i18n.ts';
import type { RunRow } from '../../api/types.ts';

/* which weekday a row starts on, 0 = Sunday */
export const WEEK_START = 0;

/* I18N.weekdays is authored Monday-first, so the names are rotated to WEEK_START */
export function weekdayNames(): string[] {
  const names = I18N.weekdays || [];
  return names.map((_, i) => names[(i + WEEK_START + 6) % names.length]);
}

export function longDate(date: Date, opts: Intl.DateTimeFormatOptions): string {
  try { return date.toLocaleDateString(I18N.numberLocale, opts); }
  catch { return dayKey(date); }
}

export const fmtTime = (iso: string): string => {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? '' : `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

export interface DaySlot { key: string; runs: RunRow[]; total: number; pending: number; applied: number;
                           rejected: number }

/* A day is the reader's LOCAL day: created_at is UTC, so a run staged at 23:30 is filed here and
   not by a server-side date. */
export function byDay(runs: RunRow[]): Map<string, DaySlot> {
  const days = new Map<string, DaySlot>();
  for (const r of runs) {
    const when = new Date(r.created_at);
    if (isNaN(when.getTime())) continue;
    const key = dayKey(when);
    let slot = days.get(key);
    if (!slot) days.set(key, slot = { key, runs: [], total: 0, pending: 0, applied: 0, rejected: 0 });
    slot.runs.push(r);
    slot.total += r.total;
    slot.pending += r.pending;
    slot.applied += r.applied;
    slot.rejected += r.rejected;
  }
  for (const slot of days.values()) slot.runs.sort((a, b) => a.id - b.id);
  return days;
}

export const daysIn = (month: string): number => {
  const at = fromKey(`${month}-01`);
  return new Date(at.getFullYear(), at.getMonth() + 1, 0).getDate();
};

/* One month as whole weeks from WEEK_START; the padding cells are null. */
export function monthCells(month: string): Array<{ day: number; key: string } | null> {
  const lead = (fromKey(`${month}-01`).getDay() - WEEK_START + 7) % 7;
  const cells: Array<{ day: number; key: string } | null> = Array.from({ length: lead }, () => null);
  for (let d = 1; d <= daysIn(month); d++) cells.push({ day: d, key: `${month}-${String(d).padStart(2, '0')}` });
  while (cells.length % 7) cells.push(null);
  return cells;
}

export function monthTotals(month: string, days: Map<string, DaySlot>) {
  let runs = 0, sug = 0, open = 0, worked = 0;
  for (const [key, slot] of days) {
    if (!key.startsWith(month)) continue;
    worked += 1;
    runs += slot.runs.length;
    sug += slot.total;
    open += slot.pending;
  }
  return { runs, sug, open, idle: daysIn(month) - worked };
}

/* Where to open with no address: the newest day with something to decide, else the newest day
   worked at all, else today. */
export function landingDay(days: Map<string, DaySlot>, today: string): string {
  const keys = [...days.keys()].sort();
  return keys.filter(k => days.get(k)?.pending).pop() || keys[keys.length - 1] || today;
}

/* A step of months takes the selected day along, clamped to the new month's last day. */
export function stepMonth(month: string, selected: string, by: number): { month: string; selected: string } {
  const at = fromKey(`${month}-01`);
  const next = monthKey(new Date(at.getFullYear(), at.getMonth() + by, 1));
  const day = Math.min(Number(selected.slice(8)) || 1, daysIn(next));
  return { month: next, selected: `${next}-${String(day).padStart(2, '0')}` };
}
