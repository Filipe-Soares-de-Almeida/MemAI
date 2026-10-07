import { afterEach, describe, expect, it } from 'vitest';
import { nextTick } from 'vue';
import DiagramsView from '../../src/memai/webui/views/diagrams/DiagramsView.vue';
import { filterDiagrams, sortIssues } from '../../src/memai/webui/views/diagrams/diagrams.ts';
import { mountView } from '../../src/memai/webui/core/vue.ts';
import { teardownView } from '../../src/memai/webui/core/lifecycle.ts';
import { t } from '../../src/memai/webui/i18n.ts';
import { calls, catalog, serveApi } from './support.js';

const en = catalog('en');
const settle = async () => { for (let i = 0; i < 4; i++) { await nextTick(); await new Promise(r => setTimeout(r, 0)); } };

afterEach(teardownView);

const row = (uid: string, title: string, extra = {}) => ({
  uid, kind: 'flowchart', title, summary: '', domain: 'acme/lamps', status: 'active', confidence: 'unverified',
  tags: '', created_at: '', updated_at: '2026-10-01T09:00:00Z', also: [], nodes: 3, edges: 2, links: 0, jumps: 0,
  documented: 1, issues: [], issue_count: 0, ...extra });
const ITEMS = [
  row('d1', 'Refill the lanterns', { summary: 'Every lantern burns tonight.' }),
  row('d2', 'Trim the wicks', { issues: [{ kind: 'no_end', keys: [] }, { kind: 'empty', keys: [] }],
       also: ['acme/night-shift'] }),
  row('d3', 'Close the shutters', { tags: 'evening' }),
];

async function show(params: Record<string, string> = {}, items = ITEMS) {
  serveApi(path => {
    if (path === '/api/domains') return { domains: [] };
    if (path.startsWith('/api/diagrams')) return { total: items.length, with_issues: 1, items };
    throw new Error(`unexpected ${path}`);
  });
  const view = document.getElementById('view') as HTMLElement;
  await mountView(DiagramsView, view, new URLSearchParams(params), { stale: () => false });
  await settle();
  return view;
}

const press = async (el: Element | null, key: string) => {
  (el as HTMLElement).focus();
  el?.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
  await settle();
};

describe('the diagram list filter', () => {
  it('matches every field a reader names a flow by, the also-paths and tags included', () => {
    expect(filterDiagrams(ITEMS as never, 'night-shift').map(d => d.uid)).toEqual(['d2']);
    expect(filterDiagrams(ITEMS as never, ' EVENING ').map(d => d.uid)).toEqual(['d3']);
    expect(filterDiagrams(ITEMS as never, '')).toHaveLength(3);
  });

  it('names the worst fault first', () => {
    expect(sortIssues(ITEMS[1] as never).map(i => i.kind)).toEqual(['empty', 'no_end']);
  });
});

describe('the diagram list', () => {
  it('asks for active flows unless told otherwise, and sends an empty status for all of them', async () => {
    await show();
    expect(calls.find(c => c.path.startsWith('/api/diagrams'))?.path).toBe('/api/diagrams?status=active');
    teardownView();
    await show({ status: '', domain: 'acme' });
    expect(calls.find(c => c.path.startsWith('/api/diagrams'))?.path).toBe('/api/diagrams?status=&domain=acme');
  });

  it('picks the first row on arrival and shows it in the inspector', async () => {
    const view = await show();
    const first = view.querySelector('.dgl-row');
    expect(first?.getAttribute('aria-selected')).toBe('true');
    expect(first?.getAttribute('tabindex')).toBe('0');
    expect(view.querySelector('.dgl-ins-title')?.textContent).toBe('Refill the lanterns');
  });

  it('walks the rows with the arrows, Home and End, and the inspector follows', async () => {
    const view = await show();
    await press(view.querySelector('.dgl-row[data-uid="d1"]'), 'ArrowDown');
    expect(document.activeElement?.getAttribute('data-uid')).toBe('d2');
    expect(view.querySelectorAll('.dgl-issue')).toHaveLength(2);
    await press(document.activeElement, 'End');
    expect(document.activeElement?.getAttribute('data-uid')).toBe('d3');
    expect(view.querySelectorAll('.dgl-row[tabindex="0"]')).toHaveLength(1);
    await press(document.activeElement, 'Home');
    expect(view.querySelector('.dgl-ins-title')?.textContent).toBe('Refill the lanterns');
  });

  it('opens the editor on Enter and on a double click', async () => {
    const view = await show();
    await press(view.querySelector('.dgl-row[data-uid="d1"]'), 'Enter');
    expect(location.hash).toBe('#/diagram?uid=d1');
    view.querySelector('.dgl-row[data-uid="d3"]')?.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
    expect(location.hash).toBe('#/diagram?uid=d3');
  });

  it('filters as it is typed, counts what is shown and hands Down to the list', async () => {
    const view = await show();
    const filter = view.querySelector('#dglFilter') as HTMLInputElement;
    filter.value = 'wick';
    filter.dispatchEvent(new Event('input'));
    await settle();
    expect([...view.querySelectorAll('.dgl-row')].map(r => r.getAttribute('data-uid'))).toEqual(['d2']);
    expect(view.querySelectorAll('.dgl-head > span')[1].textContent)
      .toBe(`${t('dgl.count', { n: '1' })} · ${t('dgl.subIssues', { n: '1' })}`);
    expect(view.querySelector('.dgl-ins-title')?.textContent).toBe('Trim the wicks');
    await press(filter, 'ArrowDown');
    expect(document.activeElement?.getAttribute('data-uid')).toBe('d2');

    filter.value = 'nothing like this';
    filter.dispatchEvent(new Event('input'));
    await settle();
    expect(view.querySelector('#dglList .empty')?.textContent).toBe(en['dgl.noMatch']);
    expect(view.querySelector('#dglIns')?.classList.contains('is-empty')).toBe(true);
  });

  it('says an empty store has no flows rather than that nothing matched', async () => {
    const view = await show({}, []);
    expect(view.querySelector('#dglList .empty')?.textContent).toContain(en['dgl.empty']);
  });
});
