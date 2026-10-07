import { describe, expect, it } from 'vitest';
import { calendar, intoWeeks, level, weekLabel, weekdaySums, WEEKS } from '../../src/memai/webui/views/overview/calendar.ts';

/* a Wednesday, so the last week is padded with Thursday to Sunday */
const NOW = new Date(2026, 8, 16, 15, 30);

describe('the activity calendar', () => {
  it('fills every day from a Monday to today, the sparse rows counted in place', () => {
    const days = calendar([{ day: '2026-09-14', count: 3 }, { day: '2026-01-01', count: 9 }], NOW);
    expect(days[0].key).toBe('2026-08-17');
    expect(days.at(-1)).toEqual({ key: '2026-09-16', count: 0, today: true });
    expect(days.length).toBe((WEEKS - 1) * 7 + 3);
    expect(days.find(d => d.key === '2026-09-14')?.count).toBe(3);
    expect(days.filter(d => d.today)).toHaveLength(1);
    expect(days.reduce((sum, d) => sum + d.count, 0)).toBe(3);
  });

  it('cuts whole weeks and pads the last one with days still to come', () => {
    const weeks = intoWeeks(calendar([], NOW));
    expect(weeks).toHaveLength(WEEKS);
    expect(weeks.every(row => row.length === 7)).toBe(true);
    expect(weeks.at(-1)?.slice(3)).toEqual([null, null, null, null]);
  });

  it('labels a week inside one month and across two without leading zeros', () => {
    const weeks = intoWeeks(calendar([], NOW));
    expect(weekLabel(weeks[0])).toBe('17–23 Aug');
    expect(weekLabel(weeks[2])).toBe('31 Aug–6 Sep');
    expect(weekLabel(weeks[3])).toBe('7–13 Sep');
    expect(weekLabel(weeks.at(-1)!)).toBe('14–16 Sep');
  });

  it('shades by quartiles of the peak, with nothing as its own step', () => {
    expect([0, 1, 5, 6, 10, 20].map(n => level(n, 20))).toEqual([0, 1, 1, 2, 2, 4]);
  });

  it('totals each weekday Monday first', () => {
    const sums = weekdaySums(calendar([{ day: '2026-09-14', count: 2 }, { day: '2026-09-07', count: 1 },
                                       { day: '2026-09-13', count: 4 }], NOW));
    expect(sums).toEqual([3, 0, 0, 0, 0, 0, 4]);
  });
});
