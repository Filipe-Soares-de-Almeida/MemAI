import { describe, expect, it } from 'vitest';
import OverviewView from '../../src/memai/webui/views/overview/OverviewView.vue';
import { mountView } from '../../src/memai/webui/core/vue.ts';
import { teardownView } from '../../src/memai/webui/core/lifecycle.ts';
import { calls, catalog, serveApi } from './support.js';

const en = catalog('en');

const symptom = (key: string, severity: string, count: number, params = {}, over = {}) =>
  ({ key, severity, count, share: count / 200, params, ...over });

const overview = (over = {}) => ({
  totals: {}, domains: [], recent: [], db: {},
  by_type: { note: 120, checkpoint: 80 },
  by_confidence: { confirmed: 50, unverified: 140, contradicted: 10 },
  by_type_confidence: { note: { confirmed: 40, unverified: 80 }, checkpoint: { unverified: 70, contradicted: 10 } },
  open_tasks: 12345,
  health: { score: 62, axes: { curation: 25, connectivity: 80, freshness: 90, organization: 55 },
            active: 200, delta: -3, delta_days: 30, since: '2026-08-01' },
  symptoms: [
    symptom('untagged', 'info', 40, { tags: 'none' }),
    symptom('contradicted', 'bad', 0, { confidence: 'contradicted' }),
    symptom('stale', 'warn', 30, { stale: '1', status: 'active' }),
    symptom('orphans', 'warn', 4, {}, { of: 900 }),
    symptom('diagrams', 'bad', 2, { defect: '1' }, { of: 7 }),
  ],
  activity: [],
  ...over,
});

const flush = () => new Promise(resolve => setTimeout(resolve, 0));
const hashParams = () => Object.fromEntries(new URLSearchParams(location.hash.split('?')[1]));
const hashView = () => location.hash.replace(/^#\/?/, '').split('?')[0];

async function open(answers: Record<string, unknown> = {}) {
  const data = overview(answers['/api/overview'] as object);
  serveApi((path: string) => {
    const base = path.split('?')[0];
    if (base === '/api/overview') return data;
    if (base in answers) return answers[base];
    throw new Error(`unexpected ${path}`);
  });
  const view = document.getElementById('view') as HTMLElement;
  await mountView(OverviewView, view, new URLSearchParams(), { stale: () => false });
  return view;
}

const button = (view: HTMLElement, selector: string) => view.querySelector(selector) as HTMLButtonElement;

describe('the health view', () => {
  it('lists the worst symptoms first and sinks a clean one to the end', async () => {
    const view = await open();
    const order = [...view.querySelectorAll('.hx-sym')].map(el => (el as HTMLElement).dataset.sym);
    expect(order).toEqual(['diagrams', 'stale', 'orphans', 'untagged', 'contradicted', 'dupes', 'file']);
    expect(button(view, '[data-sym="contradicted"] .hx-sym-go').disabled).toBe(true);
    expect(view.querySelector('[data-sym="untagged"] .hx-sym-share')?.textContent).toBe('20.0%');
    expect(view.querySelector('[data-sym="orphans"] .hx-sym-share')?.textContent).toBe('4 of 900');
    teardownView();
  });

  it('opens exactly the set a symptom counted, with the server\'s own filter', async () => {
    const view = await open();
    button(view, '[data-sym="stale"] .hx-sym-go').click();
    expect(hashView()).toBe('memories');
    expect(hashParams()).toEqual({ stale: '1', status: 'active' });
    button(view, '[data-sym="diagrams"] .hx-sym-go').click();
    expect(hashView()).toBe('diagrams');
    expect(hashParams()).toEqual({ defect: '1' });
    teardownView();
  });

  it('sends broken relations to the repair, since they have no list of their own', async () => {
    const view = await open();
    button(view, '[data-sym="orphans"] .hx-sym-go').click();
    expect(hashView()).toBe('maintenance');
    expect(hashParams()).toEqual({ tab: 'storage' });
    teardownView();
  });

  it('draws the ring\'s share and the index with its change', async () => {
    const view = await open();
    expect(view.querySelector('.hx-ring-pct')?.textContent).toBe('25%');
    expect(view.querySelectorAll('.hx-ring circle')).toHaveLength(4);
    expect(view.querySelector('.hx-score')?.textContent).toBe('62 / 100');
    expect(view.querySelector('.hx-delta.down')?.textContent).toBe(`-3 ${en['ov.hx.inDays'].replace('{n}', '30')}`);
    expect(view.querySelectorAll('.hx-axis')).toHaveLength(4);
    teardownView();
  });

  it('hides the change until there is a snapshot old enough to compare', async () => {
    const view = await open({ '/api/overview': {
      health: { score: 62, axes: { curation: 25, connectivity: 80, freshness: 90, organization: 55 },
                active: 200, delta: null, delta_days: 30, since: null } } });
    expect(view.querySelector('.hx-delta')).toBeNull();
    teardownView();
  });

  it('opens a confidence or a type from its legend row', async () => {
    const view = await open();
    button(view, '.hx-legend-row[data-conf="contradicted"]').click();
    expect(hashParams()).toEqual({ confidence: 'contradicted', status: 'active' });
    button(view, '.hx-type[data-type="checkpoint"]').click();
    expect(hashParams()).toEqual({ type: 'checkpoint', status: 'active' });
    teardownView();
  });

  it('groups the open-task count the way the locale does, and leaves it out at zero', async () => {
    let view = await open();
    expect(view.querySelector('.hx-tasks-title')?.textContent).toContain('12,345');
    button(view, '.hx-tasks .btn').click();
    expect(hashParams()).toEqual({ type: 'task', task_state: 'open', status: 'active' });
    teardownView();
    view = await open({ '/api/overview': { open_tasks: 0 } });
    expect(view.querySelector('.hx-tasks')).toBeNull();
    teardownView();
  });

  it('scans for duplicates only when asked, then sends Review to maintenance without scanning again', async () => {
    const view = await open({ '/api/maintenance/dedup': { pairs: [{}, {}, {}], threshold: 0.85 } });
    expect(calls.map(c => c.path)).toEqual(['/api/overview']);
    const scan = button(view, '[data-sym="dupes"] .btn');
    scan.click();
    await flush();
    expect(view.querySelector('[data-sym="dupes"] .hx-sym-n')?.textContent).toBe('3');
    expect(view.querySelector('[data-sym="dupes"] .hx-sym-share')?.textContent)
      .toBe(en['ov.sym.atOverlap'].replace('{p}', '85'));
    expect(scan.textContent).toBe(en['ov.sym.dupes.act']);
    scan.click();
    expect(hashView()).toBe('maintenance');
    expect(calls.filter(c => c.path.startsWith('/api/maintenance/dedup'))).toHaveLength(1);
    teardownView();
  });

  it('turns a failed file check into a repair in the storage tab', async () => {
    const view = await open({ '/api/maintenance/health': {
      integrity: { ok: true, detail: '' }, fts: { ok: true, detail: '', rows: 190, expected: 200 } } });
    const row = view.querySelector('[data-sym="file"]') as HTMLElement;
    expect(row.querySelector('.hx-sev')?.className).toBe('hx-sev');
    button(view, '[data-sym="file"] .btn').click();
    await flush();
    expect(row.querySelector('.hx-sym-n')?.textContent).toBe('1/2');
    expect(row.querySelector('.hx-sev')?.className).toBe('hx-sev sev-warn');
    expect(row.title).toBe(en['ov.sym.file.rows'].replace('{a}', '190').replace('{b}', '200'));
    const repair = button(view, '[data-sym="file"] .btn');
    expect(repair.textContent).toBe(en['ov.sym.file.repair']);
    repair.click();
    expect(hashView()).toBe('maintenance');
    expect(hashParams()).toEqual({ tab: 'storage' });
    expect(calls.filter(c => c.path === '/api/maintenance/health')).toHaveLength(1);
    teardownView();
  });
});
