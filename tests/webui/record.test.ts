import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { nextTick } from 'vue';
import RecordView from '../../src/memai/webui/views/record/RecordView.vue';
import { diffLines, fieldsOf, saveBody } from '../../src/memai/webui/views/record/record.ts';
import { backTarget, nameStop, resetWalk, stepPos, walk } from '../../src/memai/webui/views/record/walk.ts';
import { mountView } from '../../src/memai/webui/core/vue.ts';
import { teardownView } from '../../src/memai/webui/core/lifecycle.ts';
import MetaDialog from '../../src/memai/webui/views/record/MetaDialog.vue';
import { openDialog } from '../../src/memai/webui/core/ui.js';
import { MEMORY } from '../../src/memai/webui/contract.ts';
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

const memory = (uid: string, extra: Record<string, unknown> = {}) => ({
  rowid_pk: 1, uid, type: 'note', title: 'Lantern pinout', content: 'Pin 4 drives the LED.',
  domain: 'lantern/firmware', also: [], tags: 'pinout', status: 'active', confidence: 'unverified', session: '',
  source_ref: '', review_after: '', pin: '', superseded_by: null, recalls: 0, last_recall: null,
  created_at: '2026-01-02T10:00:00+00:00', updated_at: '2026-01-02T10:00:00+00:00',
  edit_history: [], spec: [], sections: [], section_problem: '', body_links: {}, relations: [],
  referenced_by_diagrams: [], ...extra,
});

const CHECKPOINT = {
  type: 'checkpoint', content: 'INTENT: Wire it.\nESTABLISHED: Pins.',
  spec: [{ key: 'intent', label: 'INTENT', max_len: 600 }, { key: 'established', label: 'ESTABLISHED', max_len: 2000 }],
  sections: [{ key: 'intent', text: 'Wire it.' }, { key: 'established', text: 'Pins.' }],
};

type Call = { method: string; body?: unknown };
async function show(m: ReturnType<typeof memory>, answer: (path: string, call: Call) => unknown = () => ({})) {
  serveApi((path: string, call: Call) => (path === `/api/memories/${m.uid}` && call.method === 'GET'
    ? m : answer(path, call)));
  const view = document.getElementById('view') as HTMLElement;
  await mountView(RecordView, view, new URLSearchParams({ uid: m.uid }), { stale: () => false });
  await nextTick();
  return view;
}

const type = async (box: Element | null, value: string) => {
  (box as HTMLTextAreaElement).value = value;
  box?.dispatchEvent(new Event('input'));
  await nextTick();
};

beforeEach(resetWalk);
afterEach(teardownView);

describe('a record field', () => {
  it('opens on its own, saves the whole set of sections with the note, and closes', async () => {
    const m = memory('a1b2c3d4e5f60010', CHECKPOINT);
    const view = await show(m);
    (view.querySelector('[data-pick="established"]') as HTMLElement).click();
    await nextTick();
    (view.querySelector('[data-edit="established"]') as HTMLElement).click();
    await nextTick();
    const box = view.querySelector('[data-src="established"]') as HTMLTextAreaElement;
    expect(box.value).toBe('Pins.');
    expect(view.querySelectorAll('[data-src]')).toHaveLength(1);

    await type(box, 'Pins and power.');
    await type(view.querySelector('[data-note]'), 'power too');
    (view.querySelector('#dSave') as HTMLElement).click();
    await until(() => !view.querySelector('[data-src]'));
    expect(calls.find(c => c.path.endsWith('/sections'))).toEqual({
      path: `/api/memories/${m.uid}/sections`, method: 'POST',
      body: { sections: { intent: 'Wire it.', established: 'Pins and power.' }, note: 'power too' } });
  });

  it('counts against the field limit as it is typed, and Escape drops the edit', async () => {
    const view = await show(memory('a1b2c3d4e5f60011', CHECKPOINT));
    (view.querySelector('[data-edit="intent"]') as HTMLElement).click();
    await nextTick();
    await type(view.querySelector('[data-src="intent"]'), 'x'.repeat(601));
    const count = view.querySelector('.rf.is-open [data-count]') as HTMLElement;
    expect(count.classList.contains('over')).toBe(true);
    view.querySelector('[data-src="intent"]')?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    await nextTick();
    expect(view.querySelector('[data-src]')).toBeNull();
    expect(calls.filter(c => c.method === 'POST')).toHaveLength(0);
  });

  it('writes a body with no sections as content, from Edit all', async () => {
    const m = memory('a1b2c3d4e5f60012');
    const view = await show(m);
    (view.querySelector('#dEditAll') as HTMLElement).click();
    await nextTick();
    expect(view.querySelector('#dEditAll')?.getAttribute('aria-pressed')).toBe('true');
    await type(view.querySelector('[data-src=""]'), 'Pin 5 drives the LED.');
    view.querySelector('[data-src=""]')?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true }));
    await until(() => calls.find(c => c.path.endsWith('/content')));
    expect(calls.find(c => c.path.endsWith('/content'))?.body).toEqual({ content: 'Pin 5 drives the LED.', note: '' });
  });
});

describe('the metadata dialog', () => {
  it('counts the domain and the tags against their ceilings', async () => {
    const done = openDialog(MetaDialog, { m: { uid: 'feedc0de00000001', type: 'note', title: 'Kiln firing',
                                               domain: 'acme/kiln', also: [], tags: 'kiln', session: '' } });
    await nextTick();
    expect(document.getElementById('mmDomainCount')?.textContent).toBe(`9/${MEMORY.DOMAIN_MAX}`);
    const tags = document.getElementById('mmTags') as HTMLInputElement;
    tags.value = 'k'.repeat(MEMORY.TAGS_MAX + 1);
    tags.dispatchEvent(new Event('input'));
    await nextTick();
    expect(document.getElementById('mmTagsCount')?.classList.contains('over')).toBe(true);
    (document.querySelector('.modal [data-x]') as HTMLElement).click();
    expect(await done).toBe(false);
  });
});

describe('the pin control', () => {
  it('posts the chosen pin for the record', async () => {
    const m = memory('a1b2c3d4e5f60001');
    const view = await show(m);
    const global = view.querySelector('#dPin button[data-pin="global"]') as HTMLElement;
    global.click();
    const sent = await until(() => calls.find(c => c.path === `/api/memories/${m.uid}/pin`));
    expect(sent).toEqual({ path: `/api/memories/${m.uid}/pin`, method: 'POST', body: { pin: 'global' } });
  });

  it('refuses a domain pin to a memory with no domain', async () => {
    const view = await show(memory('a1b2c3d4e5f60002', { domain: '' }));
    expect((view.querySelector('#dPin button[data-pin="domain"]') as HTMLButtonElement).disabled).toBe(true);
  });
});

describe('deleting for good', () => {
  it('stays off until the phrase is typed in full, then purges with it', async () => {
    const m = memory('a1b2c3d4e5f60003');
    const view = await show(m);
    (view.querySelector('#dDelete') as HTMLElement).click();
    const field = await until(() => document.querySelector<HTMLInputElement>('[data-phrase]'));
    const ok = document.querySelector('.modal [data-ok]') as HTMLButtonElement;
    expect(document.querySelector('.modal')?.textContent).toContain(`DELETE ${m.uid}`);
    expect(ok.disabled).toBe(true);
    await type(field, `DELETE ${m.uid.slice(0, -1)}`);
    expect(ok.disabled).toBe(true);
    await type(field, `DELETE ${m.uid}`);
    expect(ok.disabled).toBe(false);
    ok.click();
    const sent = await until(() => calls.find(c => c.path.endsWith('/purge')));
    expect(sent.body).toEqual({ confirm: `DELETE ${m.uid}` });
  });
});

describe('a task shown in its record', () => {
  it('reads the record again after a checklist write and repaints the side panel from it', async () => {
    const uid = 'a1b2c3d4e5f60004';
    const task = { goal: 'Ship the lantern firmware', state: 'open', completed_at: '', comments: [], notes: [], refs: {},
                   items: [{ id: 1, n: 1, seq: 1, text: 'Solder the header', state: 'todo', updated_at: '',
                             updated_session: '', links: [] }] };
    const m = memory(uid, { type: 'task', task, content: 'GOAL: Ship the lantern firmware' });
    const later = { ...m, updated_at: '2026-03-04T12:00:00+00:00', content: `${m.content}\n- [~] Solder the header`,
                    edit_history: [{ id: 1, memory_uid: uid, edited_at: '2026-03-04T12:00:00+00:00', prev_content: '',
                                     new_content: '', note: 'item 1: doing' }] };
    let reads = 0;
    serveApi((path: string, call: Call) => {
      if (path === `/api/memories/${uid}` && call.method === 'GET') return reads++ ? later : m;
      if (path === `/api/tasks/${uid}/item`) {
        return { task: { ...task, items: [{ ...task.items[0], state: 'doing' }] }, status: 'active' };
      }
      return {};
    });
    const view = document.getElementById('view') as HTMLElement;
    await mountView(RecordView, view, new URLSearchParams({ uid }), { stale: () => false });
    await nextTick();
    const updated = () => view.querySelector('[data-rs="updated"]') as HTMLElement;
    expect(updated().title).toBe(m.updated_at);

    (view.querySelector('[data-step="1"]') as HTMLElement).click();
    await until(() => updated().title === later.updated_at);
    expect(reads).toBe(2);
    expect(view.querySelector('[data-rs="history"] .rs-hist-note')?.textContent).toBe('item 1: doing');
  });
});

describe('a task shown in its record, progress', () => {
  it('shows the progress in the side panel and moves it with the checklist', async () => {
    const uid = 'a1b2c3d4e5f60005';
    const items = [{ id: 1, n: 1, seq: 1, text: 'Solder the header', state: 'todo', updated_at: '', updated_session: '', links: [] },
                   { id: 2, n: 2, seq: 2, text: 'Flash the board', state: 'dropped', updated_at: '', updated_session: '', links: [] }];
    const task = { goal: 'Ship the lantern firmware', state: 'open', completed_at: '', comments: [], notes: [], refs: {}, items };
    const m = memory(uid, { type: 'task', task, content: 'GOAL: Ship the lantern firmware' });
    serveApi((path: string, call: Call) => {
      if (path === `/api/memories/${uid}` && call.method === 'GET') return m;
      if (path === `/api/tasks/${uid}/item`) {
        return { task: { ...task, state: 'completed', items: [{ ...items[0], state: 'done' }, items[1]] }, status: 'archived' };
      }
      return {};
    });
    const view = document.getElementById('view') as HTMLElement;
    await mountView(RecordView, view, new URLSearchParams({ uid }), { stale: () => false });
    await nextTick();
    const pct = () => view.querySelector('.rec-side [data-progress-pct]')?.textContent;
    expect(pct()).toBe('0%');
    const bar = () => view.querySelector('.rec-side [role="progressbar"]') as HTMLElement;
    expect(bar().getAttribute('aria-label')).toBe(en['task.progress.label']);
    expect(bar().getAttribute('aria-valuetext')).toBe('0%, 0 of 1 done');
    expect(view.querySelector('.rec-side .tk-prog .chip')).toBeNull();
    expect(view.querySelector('.rec-side .tk-bar-drop')).toBeNull();
    expect(view.querySelector('.rec-side .tk-prog-n')?.textContent).toBe('0 of 1 done');
    expect(view.querySelector('.rec-main .tk-prog')).toBeNull();
    const line = view.querySelector('.rec-side .tk-prog-line') as HTMLElement;
    expect([...line.children].map(c => c.className))
      .toEqual(['tk-prog-pct', 'tk-prog-n']);
    expect(line.parentElement?.className).toBe('tk-prog');
    expect(line.nextElementSibling).toBe(bar());
    expect(view.querySelector('.rec-side [data-progress-pct]')?.closest('.tk-prog-line')).toBe(line);
    (view.querySelector('[data-step="1"]') as HTMLElement).click();
    await until(() => pct() === '100%');
    expect(bar().getAttribute('aria-valuetext')).toBe('100%, 1 of 1 done');
    expect(view.querySelector('.tk-item.is-new')).toBeNull();
  });
});

describe('the edit history', () => {
  it('opens a version\'s line diff on demand', async () => {
    const view = await show(memory('a1b2c3d4e5f60005', { edit_history: [
      { id: 1, memory_uid: '', edited_at: '2026-01-02T11:00:00+00:00', prev_content: 'a\nb', new_content: 'a\nc', note: '' }] }));
    const button = view.querySelector('[data-diff="0"]') as HTMLElement;
    expect(button.textContent).toBe(en['dr.hist.show']);
    button.click();
    await nextTick();
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect([...view.querySelectorAll('#histDiff0 span')].map(s => [s.className, s.textContent]))
      .toEqual([['diff-ctx', '  a'], ['diff-del', '− b'], ['diff-add', '+ c']]);
  });
});

describe('what the record derives', () => {
  it('reads a sectioned type as its fields, and anything else as one body', () => {
    const m = memory('a1b2c3d4e5f60006', { ...CHECKPOINT, sections: [{ key: 'intent', text: 'Wire it.' }] });
    expect(fieldsOf(m as never).map(f => [f.key, f.present])).toEqual([['intent', true], ['established', false]]);
    expect(fieldsOf({ ...m, section_problem: 'no INTENT line' } as never).map(f => f.key)).toEqual(['']);
  });

  it('sends the fields not being edited as they were read', () => {
    const fields = fieldsOf(memory('a1b2c3d4e5f60007', CHECKPOINT) as never);
    expect(saveBody(fields, { intent: 'New.' }, 'why')).toEqual(
      { sections: { intent: 'New.', established: 'Pins.' }, note: 'why' });
  });

  it('diffs a body too large to compare line by line as its two ends', () => {
    const big = Array.from({ length: 600 }, (_, i) => `line ${i}`).join('\n');
    expect(diffLines(big, `${big}!`).map(l => l.cls)).toEqual(['diff-del', 'diff-add']);
  });
});

describe('the walk through records', () => {
  it('steps across the list, keeps its slot when the row drops, and names the way back', () => {
    walk('aaaa', { name: 'memories', hash: '#/memories?page=2' });
    expect(stepPos('aaaa', ['zzzz', 'aaaa', 'bbbb'])).toEqual({ at: 1, prev: 0, next: 2, gone: false });
    expect(stepPos('aaaa', ['zzzz', 'bbbb'])).toEqual({ at: 1, prev: 0, next: 1, gone: true });
    expect(backTarget()).toEqual({ label: en['nav.memories'], hash: '#/memories?page=2' });

    nameStop('aaaa', 'Lantern pinout');
    walk('bbbb', { name: 'memory', hash: '#/memory?uid=aaaa' });
    expect(backTarget()).toEqual({ label: 'Lantern pinout', hash: '' });
    walk('aaaa', { name: 'memory', hash: '#/memory?uid=bbbb' });
    expect(backTarget().hash).toBe('#/memories?page=2');
  });
});
