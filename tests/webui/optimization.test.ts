import { afterEach, describe, expect, it } from 'vitest';
import { nextTick } from 'vue';
import OptimizationView from '../../src/memai/webui/views/optimization/OptimizationView.vue';
import { byDay, landingDay, monthCells, stepMonth } from '../../src/memai/webui/views/optimization/calendar.ts';
import { groupWhat, rewriteShare, setPair, verifiedState } from '../../src/memai/webui/views/optimization/suggestion.ts';
import { mountView } from '../../src/memai/webui/core/vue.ts';
import { t } from '../../src/memai/webui/i18n.ts';
import { teardownView } from '../../src/memai/webui/core/lifecycle.ts';
import { calls, catalog, serveApi } from './support.js';

const en = catalog('en');

async function until<T>(check: () => T, ms = 2000): Promise<T> {
  const end = Date.now() + ms;
  for (;;) {
    const got = check();
    if (got) return got;
    if (Date.now() > end) throw new Error('condition never held');
    await new Promise(done => setTimeout(done, 5));
  }
}
const settle = async () => { for (let i = 0; i < 4; i++) { await nextTick(); await new Promise(r => setTimeout(r, 0)); } };
const press = async (el: Element | null) => { (el as HTMLElement).click(); await settle(); };
const confirm = async () => (await until(() => document.querySelector<HTMLElement>('.modal [data-ok]'))).click();

afterEach(teardownView);

const run = (id: number, created_at: string, pending: number, extra = {}) => ({
  id, created_at, note: '', status: 'open', backup_path: null, total: pending + 1, pending, applied: 1, rejected: 0,
  kinds: [{ kind: 'retag', total: pending + 1, pending, rejected: 0 }], ...extra });

const sug = (id: number, kind: string, extra: Record<string, unknown> = {}) => ({
  id, run_id: 1, kind, target_uid: `a1b2c3d4e5f6a00${id}`, rationale: '', verified: '', status: 'pending',
  decided_at: null, created_at: '', payload: { tags: 'lantern, fuel' },
  target: { uid: `a1b2c3d4e5f6a00${id}`, type: 'note', domain: 'acme/lamps', title: `Lamp ${id}`, status: 'active',
            confidence: 'unverified', snippet: 'A lamp.', created_at: '', tags: 'lantern', review_after: '', also: [] },
  ...extra });

const group = (kind: string, pending: number, applied = 0) =>
  ({ kind, total: pending + applied, pending, applied, rejected: 0, verified: 0, facts: {} });
const summary = (groups = [group('retag', 2), group('archive', 0, 1)]) => ({
  run: { id: 1, created_at: '2026-10-03T09:00:00', note: '', status: 'open', backup_path: null },
  total: 3, pending: groups.reduce((n, g) => n + g.pending, 0), verified: 0,
  ledger: { memories: 2, active: 10, domains: 1, relations: 0, confirmed: 0, archived: 0, chars: 0 }, groups });

type Call = { method: string; body?: Record<string, unknown> };
async function show(params: Record<string, string>, answer: (path: string, call: Call) => unknown) {
  serveApi(answer);
  const view = document.getElementById('view') as HTMLElement;
  await mountView(OptimizationView, view, new URLSearchParams(params), { stale: () => false });
  await settle();
  return view;
}

describe('the calendar', () => {
  it('files runs under the local day they were staged on, summed', () => {
    const days = byDay([run(1, '2026-10-03T09:00:00', 2), run(2, '2026-10-03T23:30:00', 0), run(3, '2026-10-05T08:00:00', 1)] as never);
    expect([...days.keys()]).toEqual(['2026-10-03', '2026-10-05']);
    expect(days.get('2026-10-03')).toMatchObject({ total: 4, pending: 2 });
    expect(landingDay(days, '2026-10-20')).toBe('2026-10-05');
  });

  it('lays a month out in whole weeks from Sunday', () => {
    const cells = monthCells('2026-10');
    expect(cells.length % 7).toBe(0);
    expect(cells.slice(0, 5).map(c => c?.day ?? null)).toEqual([null, null, null, null, 1]);
  });

  it('carries the selected day into the next month, clamped to its last day', () => {
    expect(stepMonth('2026-01', '2026-01-31', 1)).toEqual({ month: '2026-02', selected: '2026-02-28' });
    expect(stepMonth('2026-01', '2026-01-15', -1)).toEqual({ month: '2025-12', selected: '2025-12-15' });
  });

  it('keeps the month and the day it shows in the address', async () => {
    const view = await show({ month: '2026-10', day: '2026-10-03' },
      () => ({ runs: [run(1, '2026-10-03T09:00:00', 2), run(2, '2026-10-05T09:00:00', 0)] }));
    expect(view.querySelector('[data-day="2026-10-03"]')?.getAttribute('aria-pressed')).toBe('true');
    expect(view.querySelectorAll('.opt-lot')).toHaveLength(1);
    await press(view.querySelector('[data-day="2026-10-05"]'));
    expect(location.hash).toBe('#/optimization?month=2026-10&day=2026-10-05');
    expect(view.querySelector('.opt-day-review')).toBeNull();
    await press(view.querySelector('#optNext'));
    expect(location.hash).toBe('#/optimization?month=2026-11&day=2026-11-05');
    expect(view.querySelector('.opt-day-empty')).not.toBeNull();
  });
});

describe('what a suggestion says', () => {
  it('words a group from its facts, and a mixed batch with its own sentence', () => {
    expect(groupWhat({ ...group('redomain', 2), facts: { paths: 2, to: '' } }))
      .toBe(t('op.what.redomainMixed', { n: 2, paths: 2, to: '' }));
    expect(groupWhat({ ...group('redomain', 2), facts: { paths: 1, to: 'acme/lamps' } }))
      .toBe(t('op.what.redomain', { n: 2, paths: 1, to: 'acme/lamps' }));
    expect(groupWhat({ ...group('mystery', 3) })).toContain('3');
  });

  it('reads a set change as what left and what arrived, and a rewrite as its share', () => {
    expect(setPair(sug(1, 'retag') as never)).toMatchObject({ gone: [], born: ['fuel'] });
    expect(rewriteShare({ ...sug(1, 'reword'), chars_before: 200, chars_after: 150 } as never)).toBe('-25%');
    expect(rewriteShare({ ...sug(1, 'reword'), status: 'applied', chars_before: 200, chars_after: 150 } as never)).toBe('');
    expect([verifiedState(2, 2), verifiedState(1, 2), verifiedState(0, 0)]).toEqual(['all', 'some', 'done']);
  });
});

describe('a run', () => {
  it('applies one kind after asking, and reads the run again', async () => {
    let applied = false;
    const view = await show({ run: '1' }, (path, call) => {
      if (path.startsWith('/api/optimization/summary')) return applied ? summary([group('retag', 0, 2)]) : summary();
      if (path === '/api/optimization/apply-all') { applied = true; return { ok: true, applied: 2, failed: [], backup: 'b', backups: ['b'] }; }
      throw new Error(`unexpected ${call.method} ${path}`);
    });
    await press(view.querySelector('[data-applykind="retag"]'));
    expect(document.querySelector('.modal')?.textContent).toContain(en['op.group.applyConfirm.title']);
    await confirm();
    await until(() => view.querySelector('[data-undokind="retag"]'));
    expect(calls.find(c => c.path === '/api/optimization/apply-all')?.body).toEqual({ run: 1, kind: 'retag' });
    expect(document.querySelector('.toast')?.textContent).toContain(en['op.toast.appliedN'].replace('{n}', '2'));
  });

  it('undoes an applied kind by reverting each of its suggestions', async () => {
    const view = await show({ run: '1' }, path => {
      if (path.startsWith('/api/optimization/summary')) return summary();
      if (path.startsWith('/api/optimization/suggestions')) return { run: {}, runs: [], suggestions: [sug(7, 'archive'), sug(8, 'archive')] };
      if (path === '/api/optimization/revert') return { ok: true };
      throw new Error(`unexpected ${path}`);
    });
    await press(view.querySelector('[data-undokind="archive"]'));
    await confirm();
    await until(() => calls.filter(c => c.path === '/api/optimization/revert').length === 2);
    expect(calls.find(c => c.path.startsWith('/api/optimization/suggestions'))?.path).toContain('status=applied');
  });

  it('is discarded only after a danger confirmation', async () => {
    const view = await show({ run: '1' }, path => {
      if (path.startsWith('/api/optimization/summary')) return summary();
      return { ok: true };
    });
    await press(view.querySelector('#optDiscard'));
    expect(document.querySelector('.modal [data-ok]')?.classList.contains('btn-danger')).toBe(true);
    (document.querySelector('.modal [data-x]') as HTMLElement).click();
    await settle();
    expect(calls.filter(c => c.method === 'DELETE')).toHaveLength(0);
    await press(view.querySelector('#optDiscard'));
    await confirm();
    await until(() => calls.find(c => c.method === 'DELETE'));
    expect(calls.find(c => c.method === 'DELETE')?.path).toBe('/api/optimization/runs/1');
  });
});

describe('a kind under review', () => {
  const answer = (items: () => unknown[]) => (path: string) => {
    if (path.startsWith('/api/optimization/summary')) return summary();
    if (path.startsWith('/api/optimization/suggestions')) return { run: {}, runs: [], suggestions: items() };
    if (path === '/api/optimization/apply') return { ok: true, backup: null };
    if (path === '/api/optimization/reject' || path === '/api/optimization/revert') return { ok: true };
    throw new Error(`unexpected ${path}`);
  };

  it('applies the picked suggestion, refetches the list, and offers an Undo that reverts it', async () => {
    let status = 'pending';
    const base = answer(() => [sug(1, 'retag', { status }), sug(2, 'retag')]);
    const view = await show({ run: '1', kind: 'retag' }, path => {
      if (path === '/api/optimization/apply') status = 'applied';
      if (path === '/api/optimization/revert') status = 'pending';
      return base(path);
    });
    await press(view.querySelector('#optDetail [data-apply="1"]'));
    await until(() => view.querySelector('#optDetail [data-revert="1"]'));
    expect(calls.find(c => c.path === '/api/optimization/apply')?.body).toEqual({ id: 1 });
    expect(view.querySelector('.opt-row[data-i="0"] .opt-row-done')?.textContent).toBe(en['op.applied']);

    (document.querySelector('.toast [data-act]') as HTMLElement).click();
    await until(() => calls.find(c => c.path === '/api/optimization/revert'));
    await until(() => view.querySelector('#optDetail [data-apply="1"]'));
  });

  it('walks with the arrows, ticks with Space, and extends a range with Shift', async () => {
    const view = await show({ run: '1', kind: 'retag' }, answer(() => [1, 2, 3, 4].map(i => sug(i, 'retag'))));
    const rows = view.querySelector('.opt-rows') as HTMLElement;
    rows.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    await settle();
    expect(view.querySelector('.opt-row.picked')?.getAttribute('data-i')).toBe('1');
    expect(document.activeElement).toBe(view.querySelector('.opt-row[data-i="1"]'));
    rows.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
    await settle();
    expect(view.querySelector('.opt-foot-n')?.textContent).toBe(en['op.sel.n'].replace('{n}', '1'));
    (view.querySelector('.opt-row[data-i="3"]') as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true }));
    await settle();
    expect([...view.querySelectorAll('.opt-row.marked')].map(r => r.getAttribute('data-i'))).toEqual(['1', '2', '3']);
    const all = view.querySelector('#optSelAll') as HTMLInputElement;
    expect(all.indeterminate).toBe(true);
  });
});

describe('a day under review', () => {
  it('applies what is ticked across its runs, with no decision in the pane', async () => {
    const runs = [run(1, '2026-10-03T09:00:00', 2), run(2, '2026-10-03T15:00:00', 1)];
    let fetched = 0;
    const view = await show({ review: '2026-10-03' }, path => {
      if (path === '/api/optimization/runs') return { runs };
      if (path.startsWith('/api/optimization/suggestions')) {
        fetched++;
        return { run: {}, runs: [], suggestions: [sug(1, 'retag'), sug(2, 'retag'), sug(3, 'retag', { run_id: 2 })] };
      }
      if (path === '/api/optimization/reject') return { ok: true };
      if (path === '/api/optimization/apply-all') return { ok: true, applied: 2, failed: [], backup: 'b', backups: ['b', 'c'] };
      throw new Error(`unexpected ${path}`);
    });
    expect(calls.find(c => c.path.startsWith('/api/optimization/suggestions'))?.path).toContain('runs=1%2C2');
    expect(view.querySelector('#optDetail [data-apply]')).toBeNull();

    await press(view.querySelector('.opt-row[data-i="0"] input'));
    await press(view.querySelector('.opt-row[data-i="2"] input'));
    await press(view.querySelector('[data-selapply]'));
    expect(document.querySelector('.modal')?.textContent).toContain(en['op.sel.applyConfirm.title']);
    await confirm();
    await until(() => calls.find(c => c.path === '/api/optimization/apply-all'));
    expect(calls.find(c => c.path === '/api/optimization/apply-all')?.body).toEqual({ runs: [1, 2], ids: [1, 3] });
    await until(() => fetched === 2);
    expect(view.querySelector('.opt-foot-n')?.textContent).toBe(en['op.sel.n'].replace('{n}', '0'));
  });
});
