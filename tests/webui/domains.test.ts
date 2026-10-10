import { beforeEach, describe, expect, it } from 'vitest';
import { nextTick } from 'vue';
import DomainsView from '../../src/memai/webui/views/domains/DomainsView.vue';
import { byDomainPath, domainGuides, domainLeaf, inDomainPath } from '../../src/memai/webui/core/domains.ts';
import { canMove, columnsFor, isArchived, isCrossing } from '../../src/memai/webui/views/domains/tree.ts';
import { enqueue, queue, showArchived } from '../../src/memai/webui/views/domains/store.ts';
import { mountView } from '../../src/memai/webui/core/vue.ts';
import { teardownView } from '../../src/memai/webui/core/lifecycle.ts';
import { MEMORY } from '../../src/memai/webui/contract.ts';
import { calls, serveApi } from './support.js';

const dom = (domain: string, over = {}) => ({
  domain, active: 0, archived: 0, types: {}, latest_at: '', subtree_latest_at: '',
  parent: domain.includes('/') ? domain.slice(0, domain.lastIndexOf('/')) : '', depth: domain.split('/').length,
  children: 0, subtree_active: 0, subtree_archived: 0, also: 0, subtree_also: 0, implicit: false, ...over,
});

const DOMAINS = [
  dom('acme', { active: 1, children: 2, subtree_active: 5, subtree_archived: 2 }),
  dom('acme/x100', { active: 3, children: 1, subtree_active: 4 }),
  dom('acme/x100/p200', { active: 1, subtree_active: 1 }),
  dom('acme/old', { archived: 2, subtree_archived: 2 }),
  dom('Acme', { active: 1, subtree_active: 1 }),
  dom('kiln', { also: 2, subtree_also: 2 }),
];

type Call = { method: string; body: unknown };

async function show(path = '', extra: (path: string, call: Call) => unknown = () => undefined) {
  serveApi((p: string, call: Call) => {
    const answer = extra(p, call);
    if (answer !== undefined) return answer;
    if (p === '/api/domains') return { domains: DOMAINS };
    if (p === '/api/config') return { domain_case: 'preserve', sections: {} };
    if (p.startsWith('/api/domains/detail')) return { domain: path, filed: [], filed_total: 0, crossing: [] };
    throw new Error(`unexpected ${p}`);
  });
  const view = document.getElementById('view') as HTMLElement;
  await mountView(DomainsView, view, new URLSearchParams(path ? { path } : {}), { stale: () => false });
  await settle();
  return view;
}

const settle = async () => { for (let i = 0; i < 3; i++) { await nextTick(); await new Promise(r => setTimeout(r, 0)); } };
const colLevels = (view: HTMLElement) =>
  [...view.querySelectorAll('.dom-col')].map(c => [...c.querySelectorAll<HTMLElement>('.dom-level')].map(l => l.dataset.path));

beforeEach(() => { queue.value = []; showArchived.value = false; });

describe('domain paths', () => {
  it('compare segment-wise and keep the casing they were written with', () => {
    expect(inDomainPath('acme/x100/p200', 'acme/x100')).toBe(true);
    expect(inDomainPath('acme/x1000', 'acme/x100')).toBe(false);
    expect(inDomainPath('Acme/x100', 'acme')).toBe(false);
    expect(inDomainPath('anything', '')).toBe(true);
    expect(domainLeaf('acme/x100/p200')).toBe('p200');
  });

  it('sort parent, subtree, then sibling, and draw the rails of that order', () => {
    const list = ['acme-b', 'acme/x100/p200', 'acme', 'acme/x100'].map(domain => ({ domain })).sort(byDomainPath);
    expect(list.map(d => d.domain)).toEqual(['acme', 'acme/x100', 'acme/x100/p200', 'acme-b']);
    expect(domainGuides(list).map(g => [g.depth, g.last])).toEqual([[1, false], [2, true], [3, true], [1, true]]);
  });
});

describe('the domain tree', () => {
  it('opens one column per level of the path, plus the children of the picked one', () => {
    const cols = columnsFor(DOMAINS, 'acme/x100', false);
    expect(cols.map(c => [c.parent, c.picked, c.kids.map(k => k.domain)])).toEqual([
      ['', 'acme', ['acme', 'Acme', 'kiln']],
      ['acme', 'acme/x100', ['acme/x100']],
      ['acme/x100', '', ['acme/x100/p200']],
    ]);
    expect(columnsFor(DOMAINS, 'acme/x100/p200', false)).toHaveLength(3);
    expect(columnsFor(DOMAINS, 'acme', true)[1].kids.map(k => k.domain)).toEqual(['acme/old', 'acme/x100']);
  });

  it('reads a branch of archived memories as archived, and one only pointed at as crossing', () => {
    expect(isArchived(DOMAINS[3])).toBe(true);
    expect(isArchived(DOMAINS[0])).toBe(false);
    expect(isCrossing(DOMAINS[5])).toBe(true);
  });

  it('refuses a move into the level itself, its subtree, or where it already lives', () => {
    expect(canMove('acme/x100', 'acme/x100/p200')).toBe(false);
    expect(canMove('acme/x100', 'acme')).toBe(false);
    expect(canMove('acme/x100', '')).toBe(true);
    expect(canMove('acme/x100', 'kiln')).toBe(true);
  });
});

describe('the domains view', () => {
  it('walks into a level when it is picked, by click or by Enter', async () => {
    const view = await show();
    expect(colLevels(view)).toEqual([['acme', 'Acme', 'kiln']]);
    (view.querySelector('[data-path="acme"]') as HTMLElement).click();
    expect(location.hash).toBe('#/domains?path=acme');
    teardownView();
    const deep = await show('acme');
    expect(colLevels(deep)).toEqual([['acme', 'Acme', 'kiln'], ['acme/x100']]);
    expect(deep.querySelector('.dom-level.on')?.getAttribute('aria-current')).toBe('true');
    deep.querySelector('[data-path="acme/x100"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(location.hash).toBe('#/domains?path=acme%2Fx100');
    teardownView();
  });

  it('shows the archived branches only when asked, with their count on the toggle', async () => {
    const view = await show('acme');
    const toggle = view.querySelector('#domArchived') as HTMLButtonElement;
    expect(colLevels(view)[1]).toEqual(['acme/x100']);
    toggle.click();
    await settle();
    expect(colLevels(view)[1]).toEqual(['acme/old', 'acme/x100']);
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    teardownView();
  });

  it('queues a dropped move without writing it, and Apply runs the queue in order', async () => {
    const bodies: unknown[] = [];
    const view = await show('acme', (p, call) => {
      if (p === '/api/domains/rename') { bodies.push(call.body); return { ok: true, affected: 4, also_affected: 0, domains: 1, merged: null }; }
      return undefined;
    });
    enqueue('acme/x100', 'kiln', DOMAINS[1]);
    enqueue('Acme', 'kiln', DOMAINS[4]);
    enqueue('acme/x100', '', DOMAINS[1]);
    await settle();
    expect(view.querySelector('[data-path="acme/x100"] .dom-arrow')?.textContent).toBe('→ x100');
    expect(view.querySelector('#domQueue')?.hasAttribute('hidden')).toBe(false);
    expect(calls.some(c => c.path === '/api/domains/rename')).toBe(false);
    (view.querySelector('#domQueue [data-apply]') as HTMLElement).click();
    await settle();
    expect(bodies).toEqual([{ from: 'Acme', to: 'kiln/Acme' }, { from: 'acme/x100', to: 'x100' }]);
    expect(queue.value).toEqual([]);
    teardownView();
  });

  it('deletes a level only once "DELETE <domain>" is typed, and goes back to its parent', async () => {
    const view = await show('acme/x100', p =>
      (p === '/api/domains/delete' ? { ok: true, purged: 4, unlinked: 0, domains: 2 } : undefined));
    (view.querySelector('[data-more]') as HTMLElement).click();
    await settle();
    [...document.querySelectorAll<HTMLElement>('.ctx-item')].find(b => b.textContent?.includes('Delete'))!.click();
    await settle();
    const ok = document.querySelector('.modal [data-ok]') as HTMLButtonElement;
    const phrase = document.getElementById('ddPhrase') as HTMLInputElement;
    phrase.value = 'DELETE acme/X100';
    phrase.dispatchEvent(new Event('input'));
    await settle();
    expect(ok.disabled).toBe(true);
    phrase.value = 'DELETE acme/x100';
    phrase.dispatchEvent(new Event('input'));
    await settle();
    expect(ok.disabled).toBe(false);
    ok.click();
    await settle();
    expect(calls.find(c => c.path === '/api/domains/delete')?.body).toEqual({ domain: 'acme/x100', confirm: 'DELETE acme/x100' });
    expect(location.hash).toBe('#/domains?path=acme');
    teardownView();
  });

  it('counts the path a move makes, and will not move when a path under it would pass the ceiling', async () => {
    const view = await show('acme/x100');
    (view.querySelector('[data-move]') as HTMLElement).click();
    await settle();
    const ok = document.querySelector('.modal [data-ok]') as HTMLButtonElement;
    const name = document.getElementById('rnName') as HTMLInputElement;
    const count = document.getElementById('rnCount') as HTMLElement;
    expect(count.textContent).toBe(`${'acme/x100'.length}/${MEMORY.DOMAIN_MAX}`);
    name.value = 'x'.repeat(MEMORY.DOMAIN_MAX - 'acme/'.length - 2);
    name.dispatchEvent(new Event('input'));
    await settle();
    expect(count.classList.contains('over')).toBe(false);
    expect(document.getElementById('rnTooLong')?.hidden).toBe(false);
    expect(ok.disabled).toBe(true);
    name.value = 'x200';
    name.dispatchEvent(new Event('input'));
    await settle();
    expect(document.getElementById('rnTooLong')?.hidden).toBe(true);
    expect(ok.disabled).toBe(false);
    (document.querySelector('.modal [data-x]') as HTMLElement).click();
    teardownView();
  });

  it('will not move a level inside itself, and says so', async () => {
    const view = await show('acme/x100');
    (view.querySelector('[data-move]') as HTMLElement).click();
    await settle();
    const ok = document.querySelector('.modal [data-ok]') as HTMLButtonElement;
    const name = document.getElementById('rnName') as HTMLInputElement;
    expect(ok.disabled).toBe(true);
    name.value = 'x100/inner';
    name.dispatchEvent(new Event('input'));
    await settle();
    expect(document.getElementById('rnPath')?.textContent).toBe('acme/x100/inner');
    expect(document.getElementById('rnCycle')?.hidden).toBe(false);
    expect(ok.disabled).toBe(true);
    name.value = 'x200';
    name.dispatchEvent(new Event('input'));
    await settle();
    expect(ok.disabled).toBe(false);
    (document.querySelector('.modal [data-x]') as HTMLElement).click();
    teardownView();
  });
});
