import { describe, expect, it } from 'vitest';
import { renderMemories } from '../../src/memai/webui/views/memories.js';
import { calls, catalog, serveApi } from './support.js';

const en = catalog('en');
const ctx = { stale: () => false };

async function show(params = {}) {
  serveApi(path => {
    if (path === '/api/domains') return { domains: [] };
    if (path.startsWith('/api/memories?')) return { items: [], total: 0, searched: false };
    return {};
  });
  const view = document.getElementById('view');
  await renderMemories(view, new URLSearchParams(params), ctx);
  return view;
}

const labels = root => [...root.querySelectorAll('.tb-field .mg-label')].map(l => l.textContent);

describe('the memory list filters', () => {
  it('label every filter, with the rarer ones folded behind one button', async () => {
    const view = await show();
    const main = view.querySelector('.list-toolbar:not(#memMore)');
    const more = view.querySelector('#memMore');
    expect(labels(main)).toEqual(['mem.f.search', 'mem.f.type', 'mem.f.domain', 'mem.f.status', 'mem.f.conf']
      .map(k => en[k]));
    expect(labels(more)).toEqual(['mem.f.pin', 'mem.f.sort'].map(k => en[k]));
    expect(main.querySelector('#fPin')).toBeNull();

    const button = view.querySelector('#fMore');
    expect(button.getAttribute('aria-controls')).toBe('memMore');
    expect(more.hidden).toBe(true);
    button.click();
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(more.hidden).toBe(false);
    button.click();
  });

  it('filter by pin: the pin in the address goes to the API, and a picked one goes to the address', async () => {
    const view = await show({ pin: 'domain' });
    const list = calls.find(c => c.path.startsWith('/api/memories?'));
    expect(new URLSearchParams(list.path.split('?')[1]).get('pin')).toBe('domain');

    view.querySelector('#fPin').click();
    document.querySelector('.pick-pop [role="option"][data-v="global"]').click();
    expect(new URLSearchParams(location.hash.split('?')[1]).get('pin')).toBe('global');
  });
});
