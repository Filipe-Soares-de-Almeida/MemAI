/* What a review list is about -- one kind of one run, or every open suggestion of a day -- and the
   only things the two disagree on: the query, the scope a batch acts on, and the way back. */

import { dayKey, fromKey } from '../../core/dom.ts';
import { seg } from '../../core/api.ts';
import { kindLabel } from '../../core/shared.js';
import { I18N, t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { OptimizationSummary, RunRow } from '../../api/types.ts';

export interface Scope {
  day: boolean;
  title: string;
  sub: string;
  listAria: string;
  emptyMsg: string;
  back: { label: string; to: Record<string, string | number> };
  query: string;
  body: Record<string, unknown>;
  /* a decided row stays where it was, marked, instead of the list being fetched again */
  keepDecided: boolean;
  refresh(): Promise<{ pending: number; sub: string }>;
}

export function kindScope(sum: OptimizationSummary, kind: string): Scope {
  const runId = sum.run.id;
  const meta = sum.groups.find(g => g.kind === kind);
  return {
    day: false,
    title: kindLabel(kind),
    sub: meta ? t('op.group.countPending', { p: meta.pending, t: meta.total }) : t('op.group.countAll', { t: 0 }),
    listAria: t('op.sel.listAria', { kind: kindLabel(kind) }),
    emptyMsg: t('op.emptyGroup'),
    back: { label: t('op.runTitle', { id: runId }), to: { run: runId } },
    query: `run=${seg(runId)}&kind=${seg(kind)}`,
    body: { run: runId, kind },
    keepDecided: false,
    async refresh() {
      const g = (await client.optimization.summary({ run: runId })).groups.find(x => x.kind === kind);
      return { pending: g ? g.pending : 0, sub: t('op.group.countPending', { p: g ? g.pending : 0, t: g ? g.total : 0 }) };
    },
  };
}

const totals = (runs: RunRow[]) => runs.reduce(
  (a, r) => ({ pending: a.pending + r.pending, total: a.total + r.total }), { pending: 0, total: 0 });

/* Only what is still open: a day can hold a dozen runs, and their decided rows are not the review. */
export function dayScope(day: string, runsOfDay: RunRow[]): Scope {
  const withWork = runsOfDay.filter(r => r.pending);
  const ids = (withWork.length ? withWork : runsOfDay).map(r => r.id);
  let label = day;
  try {
    label = fromKey(day).toLocaleDateString(I18N.numberLocale, { weekday: 'long', day: 'numeric', month: 'long' });
  } catch { /* the key itself names the day */ }
  const now = totals(runsOfDay);
  return {
    day: true,
    title: label,
    sub: t('op.cal.scopeDay', { n: now.pending, all: now.total }),
    listAria: t('op.cal.dayAria', { day: label }),
    emptyMsg: t('op.cal.allDecided', { n: now.total }),
    back: { label: t('op.title'), to: { day } },
    query: `runs=${seg(ids.join(','))}&status=pending`,
    body: { runs: ids },
    keepDecided: true,
    async refresh() {
      const sum = totals((await client.optimization.runs()).runs.filter(r => dayKey(new Date(r.created_at)) === day));
      return { pending: sum.pending, sub: t('op.cal.scopeDay', { n: sum.pending, all: sum.total }) };
    },
  };
}
