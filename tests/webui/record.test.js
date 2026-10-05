import { describe, expect, it } from 'vitest';
import { renderRecord } from '../../src/memai/webui/views/record.js';
import { calls, serveApi } from './support.js';

async function until(check, ms = 2000) {
  const end = Date.now() + ms;
  for (;;) {
    const got = check();
    if (got) return got;
    if (Date.now() > end) throw new Error('condition never held');
    await new Promise(done => setTimeout(done, 5));
  }
}

const memory = (uid, extra = {}) => ({
  uid, type: 'note', title: 'Lantern pinout', content: 'Pin 4 drives the LED.',
  domain: 'lantern/firmware', also: [], tags: 'pinout', status: 'active',
  confidence: 'unverified', session: '', source_ref: '', review_after: '', pin: '',
  created_at: '2026-01-02T10:00:00+00:00', updated_at: '2026-01-02T10:00:00+00:00',
  edit_history: [], relations: [], referenced_by_diagrams: [], ...extra,
});

const ctx = { stale: () => false };

async function show(m, answer = () => ({})) {
  serveApi((path, call) => (path === `/api/memories/${m.uid}` && call.method === 'GET'
    ? m : answer(path, call)));
  const view = document.getElementById('view');
  await renderRecord(view, new URLSearchParams({ uid: m.uid }), ctx);
  return view;
}

describe('the pin control', () => {
  it('posts the chosen pin for the record', async () => {
    const m = memory('a1b2c3d4e5f60001');
    const view = await show(m, () => ({}));
    const global = view.querySelector('#dPin button[data-pin="global"]');
    expect(global).not.toBeNull();
    global.click();
    const sent = await until(() => calls.find(c => c.path === `/api/memories/${m.uid}/pin`));
    expect(sent).toEqual({ path: `/api/memories/${m.uid}/pin`, method: 'POST', body: { pin: 'global' } });
  });
});

describe('a task shown in its record', () => {
  it('reads the record again after a checklist write and repaints the side panel from it', async () => {
    const uid = 'a1b2c3d4e5f60002';
    const task = { goal: 'Ship the lantern firmware', state: 'open', completed_at: null, comments: [],
                   items: [{ key: 'i1', text: 'Solder the header', state: 'todo', links: [] }] };
    const m = memory(uid, { type: 'task', task, content: 'GOAL: Ship the lantern firmware' });
    const later = { ...m, updated_at: '2026-03-04T12:00:00+00:00', content: `${m.content}\n- [~] Solder the header`,
                    edit_history: [{ edited_at: '2026-03-04T12:00:00+00:00', note: 'item i1: doing' }] };
    let reads = 0;
    serveApi((path, call) => {
      if (path === `/api/memories/${uid}` && call.method === 'GET') return reads++ ? later : m;
      if (path === `/api/tasks/${uid}/item`) {
        return { task: { ...task, items: [{ ...task.items[0], state: 'doing' }] }, status: 'active' };
      }
      return {};
    });
    const view = document.getElementById('view');
    await renderRecord(view, new URLSearchParams({ uid }), ctx);
    const updated = view.querySelector('[data-rs="updated"]');
    const before = updated.title;

    view.querySelector('[data-step="i1"]').click();
    await until(() => view.querySelector('[data-rs="updated"]').title !== before);
    expect(view.querySelector('[data-rs="updated"]').title).toBe(later.updated_at);
    expect(reads).toBe(2);
  });
});
