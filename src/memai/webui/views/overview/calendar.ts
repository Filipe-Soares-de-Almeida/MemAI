/* The activity rows reshaped into a Monday-first calendar of whole weeks that ends today. */

import { dayKey, fmtDay, fromKey } from '../../core/dom.ts';
import type { DayCount } from '../../api/types.ts';

export interface CalendarDay { key: string; count: number; today: boolean }
/* null is a day that has not happened yet, which is not the same fact as a day with nothing */
export type Week = (CalendarDay | null)[];

export const WEEKS = 5;

/* The API sends only the days that have activity; the calendar needs every day from the Monday
   WEEKS-1 weeks back, so today sits in the last row whatever the weekday. */
export function calendar(activity: DayCount[], now: Date = new Date()): CalendarDay[] {
  const byDay = Object.fromEntries(activity.map(a => [a.day, a.count]));
  const today = new Date(now);
  today.setHours(0, 0, 0, 0);
  const todayKey = dayKey(today);
  const monday = new Date(today);
  monday.setDate(monday.getDate() - ((today.getDay() + 6) % 7) - (WEEKS - 1) * 7);
  const days: CalendarDay[] = [];
  for (const d = new Date(monday); d <= today; d.setDate(d.getDate() + 1)) {
    const key = dayKey(d);
    days.push({ key, count: byDay[key] || 0, today: key === todayKey });
  }
  return days;
}

export function intoWeeks(days: CalendarDay[]): Week[] {
  const weeks: Week[] = [];
  for (let i = 0; i < days.length; i += 7) {
    const row: Week = days.slice(i, i + 7);
    while (row.length < 7) row.push(null);
    weeks.push(row);
  }
  return weeks;
}

/* Quartiles of the peak rather than of the mean, so one busy day does not flatten the rest. */
export const level = (n: number, max: number): number =>
  n ? Math.min(4, Math.ceil((n / max) * 4)) : 0;

/* '3–9 Aug' inside a month, '31 Aug–6 Sep' across two; no leading zero, or the label overruns
   its column into the first cell. */
export function weekLabel(row: Week): string {
  const days = row.filter((d): d is CalendarDay => d !== null);
  const [d1, m1] = fmtDay(days[0].key).replace(/^0/, '').split(' ');
  const [d2, m2] = fmtDay(days[days.length - 1].key).replace(/^0/, '').split(' ');
  return m1 === m2 ? `${d1}–${d2} ${m2}` : `${d1} ${m1}–${d2} ${m2}`;
}

/* Totals per weekday, Monday first like the grid. */
export function weekdaySums(days: CalendarDay[]): number[] {
  const sums = new Array<number>(7).fill(0);
  for (const d of days) sums[(fromKey(d.key).getDay() + 6) % 7] += d.count;
  return sums;
}
