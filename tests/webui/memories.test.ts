import { describe, expect, it } from 'vitest';
import { nextTick } from 'vue';
import MemoriesView from '../../src/memai/webui/views/memories/MemoriesView.vue';
import { readFilters, routeParams } from '../../src/memai/webui/views/memories/filters.ts';
import { bulkBodies, stagedCount } from '../../src/memai/webui/views/memories/staged.ts';
import { mountView } from '../../src/memai/webui/core/vue.ts';
import { teardownView } from '../../src/memai/webui/core/lifecycle.ts';
import { calls, catalog, serveApi } from './support.js';

const en = catalog('en');

const row = (uid: string, over = {}) => ({
  rowid_pk: 1, uid, type: 'note', title: `Lantern ${uid.slice(-1)}`, content: 'An invented body.', content_len: 17,
  domain: 'acme/lantern', tags: '', session: '', status: 'active', confidence: 'unverified', pin: '',
  superseded_by: null, review_after: '', source_ref: '', created_at: '2026-09-01T10:00:00+00:00',
  updated_at: '2026-09-01T10:00:00+00:00', recalls: 0, last_recall: null, ...over,
});

const ITEMS = ['1', '2', '3', '4'].map(n => row(`feedc0de0000000${n}`));
const ALL = [...ITEMS, row('feedc0de00000005'), row('feedc0de00000006')];

type Call = { method: string; body: unknown };

async function show(params: Record<string, string> = {}, extra: (path: string, call: Call) => unknown = () => undefined) {
  serveApi((path: string, call: Call) => {
    const answer = extra(path, call);
    if (answer !== undefined) return answer;
    if (path === '/api/domains') return { domains: [{ domain: 'acme/lantern' }, { domain: 'kiln' }] };
    if (path.startsWith('/api/memories?')) {
      const limit = new URLSearchParams(path.split('?')[1]).get('limit');
      return limit === '50' ? { items: ITEMS, total: ALL.length, searched: false }
                            : { items: ALL, total: ALL.length, searched: false };
    }
    throw new Error(`unexpected ${path}`);
  });
  const view = document.getElementById('view') as HTMLElement;
  await mountView(MemoriesView, view, new URLSearchParams(params), { stale: () => false });
  return view;
}

const flush = async () => { await nextTick(); await new Promise(r => setTimeout(r, 0)); await nextTick(); };
const rows = (view: HTMLElement) => [...view.querySelectorAll<HTMLElement>('.mem-row')];
const ticked = (view: HTMLElement) => rows(view).map(r => (r.classList.contains('selected') ? 1 : 0)).join('');
const hashParams = () => Object.fromEntries(new URLSearchParams(location.hash.split('?')[1]));

async function key(target: HTMLElement, k: string, mods: KeyboardEventInit = {}) {
  target.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...mods }));
  await flush();
}

const labels = (root: Element) => [...root.querySelectorAll('.tb-field .mg-label')].map(l => l.textContent);

describe('the memory list filters', () => {
  it('label every filter, with the rarer ones folded behind one button', async () => {
    const view = await show();
    const main = view.querySelector('.list-toolbar:not(#memMore)') as HTMLElement;
    const more = view.querySelector('#memMore') as HTMLElement;
    expect(labels(main)).toEqual(['mem.f.search', 'mem.f.type', 'mem.f.domain', 'mem.f.status', 'mem.f.conf']
      .map(k => en[k]));
    expect(labels(more)).toEqual(['mem.f.pin', 'mem.f.sort'].map(k => en[k]));
    expect(main.querySelector('#fPin')).toBeNull();

    const button = view.querySelector('#fMore') as HTMLButtonElement;
    expect(button.getAttribute('aria-controls')).toBe('memMore');
    expect(more.hidden).toBe(true);
    button.click();
    await flush();
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(more.hidden).toBe(false);
    button.click();
    teardownView();
  });

  it('filter by pin: the pin in the address goes to the API, and a picked one goes to the address', async () => {
    const view = await show({ pin: 'domain' });
    const list = calls.find(c => c.path.startsWith('/api/memories?'))!;
    expect(new URLSearchParams(list.path.split('?')[1]).get('pin')).toBe('domain');

    (view.querySelector('#fPin') as HTMLElement).click();
    (document.querySelector('.pick-pop [role="option"][data-v="global"]') as HTMLElement).click();
    expect(hashParams().pin).toBe('global');
    teardownView();
  });

  it('take a defect filter off with its own chip, and keep the rest', async () => {
    const view = await show({ untagged: '1', stale: '1' });
    expect(view.querySelector('#memMore')?.hasAttribute('hidden')).toBe(false);
    (view.querySelector('[data-undefect="untagged"]') as HTMLElement).click();
    expect(hashParams()).toEqual({ stale: '1', sort: 'created_at', dir: 'desc' });
    teardownView();
  });

  it('write only what differs from the defaults into the address', () => {
    const f = readFilters(new URLSearchParams('type=note&page=2'));
    expect(routeParams(f, { page: 0 })).toEqual({ type: 'note', sort: 'created_at', dir: 'desc' });
    expect(routeParams(f, {})).toEqual({ type: 'note', sort: 'created_at', dir: 'desc', page: '2' });
    expect(routeParams(f, { status: '' })).toMatchObject({ status: '' });
    expect(routeParams(readFilters(new URLSearchParams('status=')), {}).status).toBe('');
  });
});

describe('the memory list keyboard', () => {
  it('moves one caret with the arrows, Home and End, and the inspector follows it', async () => {
    const view = await show();
    const [first] = rows(view);
    first.focus();
    await flush();
    expect(first.classList.contains('is-cursor')).toBe(true);
    await key(first, 'ArrowDown');
    expect(document.activeElement).toBe(rows(view)[1]);
    expect(rows(view).map(r => r.tabIndex)).toEqual([-1, 0, -1, -1]);
    expect(view.querySelector('#memInspect .uid-chip')?.textContent).toBe('feedc0de00000002');
    await key(rows(view)[1], 'End');
    expect(document.activeElement).toBe(rows(view)[3]);
    await key(rows(view)[3], 'Home');
    expect(document.activeElement).toBe(rows(view)[0]);
    teardownView();
  });

  it('ticks with Space, a run with Shift, all with Ctrl+A, and clears with Escape', async () => {
    const view = await show();
    rows(view)[0].focus();
    await key(rows(view)[0], ' ');
    expect(ticked(view)).toBe('1000');
    await key(rows(view)[0], 'ArrowDown', { shiftKey: true });
    await key(rows(view)[1], 'ArrowDown', { shiftKey: true });
    expect(ticked(view)).toBe('1110');
    expect(view.querySelector('[data-selcount]')?.textContent).toBe('3 of 4 selected');
    expect((view.querySelector('#memAll') as HTMLInputElement).indeterminate).toBe(true);
    await key(rows(view)[2], 'a', { ctrlKey: true });
    expect(ticked(view)).toBe('1111');
    expect((view.querySelector('#memAll') as HTMLInputElement).checked).toBe(true);
    await key(rows(view)[2], 'Escape');
    expect(ticked(view)).toBe('0000');
    teardownView();
  });

  it('opens the row under the caret with Enter', async () => {
    const view = await show();
    rows(view)[2].focus();
    await key(rows(view)[2], 'Enter');
    expect(location.hash).toBe('#/memory?uid=feedc0de00000003');
    teardownView();
  });

  it('goes from the search field down into the list', async () => {
    const view = await show();
    await key(view.querySelector('#fQ') as HTMLElement, 'ArrowDown');
    expect(document.activeElement).toBe(rows(view)[0]);
    teardownView();
  });
});

describe('the memory inspector', () => {
  it('offers every matching row once the page is ticked, and loads them in one request', async () => {
    const view = await show();
    (view.querySelector('#memAll') as HTMLInputElement).click();
    await flush();
    const offer = view.querySelector('[data-bn-all]') as HTMLButtonElement;
    expect(offer.textContent).toBe(en['mem.selectMatching'].replace('{n}', '6'));
    offer.click();
    await flush();
    expect(view.querySelector('#memBanner span')?.textContent).toBe(en['mem.allMatching'].replace('{n}', '6'));
    expect(view.querySelector('.mi-count')?.textContent).toContain('6');
    teardownView();
  });

  it('stages confidence and tags, says what they will change, and writes them in order on Apply', async () => {
    const bodies: unknown[] = [];
    const view = await show({}, (path, call) => {
      if (path === '/api/bulk') { bodies.push(call.body); return { ok: true, affected: 2 }; }
      return undefined;
    });
    (rows(view)[0].querySelector('input') as HTMLElement).click();
    (rows(view)[1].querySelector('input') as HTMLElement).click();
    await flush();
    const apply = view.querySelector('[data-apply]') as HTMLButtonElement;
    expect(apply.disabled).toBe(true);
    (view.querySelector('[data-conf="confirmed"]') as HTMLElement).click();
    const tag = view.querySelector('#miTag') as HTMLInputElement;
    tag.value = 'brass,';
    tag.dispatchEvent(new Event('input'));
    await key(tag, 'Enter');
    expect([...view.querySelectorAll('[data-untag]')].map(b => b.textContent)).toEqual(['brass']);
    expect(view.querySelectorAll('.mi-plan span')).toHaveLength(3);
    expect(apply.disabled).toBe(false);
    apply.click();
    await flush();
    await flush();
    expect(bodies).toEqual([
      { action: 'confidence', value: 'confirmed', uids: ['feedc0de00000001', 'feedc0de00000002'] },
      { action: 'tag', value: 'brass', uids: ['feedc0de00000001', 'feedc0de00000002'] },
    ]);
    teardownView();
  });

  it('deletes only after the phrase with the count is typed', async () => {
    const view = await show({}, path => (path === '/api/memories/purge' ? { ok: true, backup: 'b', purged: 2, missing: [] } : undefined));
    (rows(view)[0].querySelector('input') as HTMLElement).click();
    (rows(view)[3].querySelector('input') as HTMLElement).click();
    await flush();
    (view.querySelector('[data-act="purge"]') as HTMLElement).click();
    await flush();
    const ok = document.querySelector('.modal [data-ok]') as HTMLButtonElement;
    expect(ok.disabled).toBe(true);
    const phrase = document.querySelector('.modal [data-phrase]') as HTMLInputElement;
    phrase.value = 'DELETE 2';
    phrase.dispatchEvent(new Event('input'));
    await flush();
    ok.click();
    await flush();
    await flush();
    const sent = calls.find(c => c.path === '/api/memories/purge');
    expect(sent?.body).toEqual({ uids: ['feedc0de00000001', 'feedc0de00000004'], confirm: 'DELETE 2' });
    teardownView();
  });

  it('counts only the writes that change something', () => {
    const picked = [row('a', { confidence: 'confirmed', domain: 'kiln' }), row('b')] as never[];
    expect(stagedCount(picked, { confidence: 'confirmed', tags: [], domain: '' })).toBe(1);
    expect(stagedCount(picked, { confidence: '', tags: ['x'], domain: 'kiln' })).toBe(3);
    expect(bulkBodies({ confidence: '', tags: ['x', 'y'], domain: 'kiln' }))
      .toEqual([{ action: 'tag', value: 'x, y' }, { action: 'rehome', value: 'kiln' }]);
  });
});
