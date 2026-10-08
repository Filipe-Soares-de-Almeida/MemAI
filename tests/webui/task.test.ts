import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApp, nextTick } from 'vue';
import type { App } from 'vue';
import TaskChecklist from '../../src/memai/webui/views/record/TaskChecklist.vue';
import { notePeek, progressOf } from '../../src/memai/webui/views/record/checklist.ts';
import { calls, catalog, serveApi } from './support.js';

const en = catalog('en');

const item = (key: string, text: string, state = 'todo') =>
  ({ key, seq: 1, text, state, updated_at: '', updated_session: '', links: [] as unknown[] });

const taskOf = (items: ReturnType<typeof item>[], extra = {}) => ({
  goal: 'Ship the lantern firmware', state: 'open', completed_at: '',
  items, comments: [] as unknown[], notes: [] as unknown[], ...extra,
});

let apps: App[] = [];
afterEach(() => { apps.forEach(app => app.unmount()); apps = []; });

let n = 0;
async function mount(task: unknown, hooks: { onWrite?: (a: unknown) => void; onStatus?: (s: string) => void } = {}) {
  const host = document.getElementById('view') as HTMLElement;
  const uid = `task${String(n++).padStart(12, '0')}`;
  const app = createApp(TaskChecklist, { uid, task, status: 'active', ...hooks });
  app.mount(host);
  apps.push(app);
  await nextTick();
  return { host, uid };
}

async function until<T>(check: () => T, ms = 2000): Promise<T> {
  const end = Date.now() + ms;
  for (;;) {
    const got = check();
    if (got) return got;
    if (Date.now() > end) throw new Error('condition never held');
    await new Promise(done => setTimeout(done, 5));
  }
}

const press = async (el: Element | null) => { (el as HTMLElement).click(); await nextTick(); };
const menuOf = (host: HTMLElement, key: string) => host.querySelector(`[data-menu="${key}"]`);
const entry = (label: string) => [...document.querySelectorAll<HTMLElement>('.ctx-item')]
  .find(b => b.textContent?.includes(label)) as HTMLElement;

describe('the checklist arithmetic', () => {
  it('counts done and dropped against every item, and the percentage leaves dropped out', () => {
    expect(progressOf(taskOf([item('i1', 'a', 'done'), item('i2', 'b', 'dropped'), item('i3', 'c')])))
      .toEqual({ total: 3, done: 1, dropped: 1, pct: 50 });
  });

  it('pct is 100 when every live item is done, and 0 with nothing live', () => {
    expect(progressOf(taskOf([item('i1', 'a', 'done'), item('i2', 'b', 'dropped')])).pct).toBe(100);
    expect(progressOf(taskOf([item('i1', 'a', 'dropped')])).pct).toBe(0);
    expect(progressOf(taskOf([item('i1', 'a', 'done'), item('i2', 'b'), item('i3', 'c')])).pct).toBe(33);
  });

  it('pct floors the exact ratio, with no floating-point slip', () => {
    const items = [
      ...Array.from({ length: 29 }, (_, i) => item(`d${i}`, 'a', 'done')),
      ...Array.from({ length: 21 }, (_, i) => item(`t${i}`, 'b')),
      item('x1', 'c', 'dropped'), item('x2', 'd', 'dropped'),
    ];
    expect(progressOf(taskOf(items)).pct).toBe(58);
  });

  it('peeks at a note as plain words', () => {
    expect(notePeek('| a | b |\n|---|---|\n**Bold** `code`\n== Head ==')).toBe('a b Bold code Head');
  });
});

describe('the checklist layout', () => {
  it('reads goal, items, task notes, then the thread, with no progress bar of its own', async () => {
    const notes = [{ id: 1, title: 'Bench rules', body: 'Flux first.', items: [], updated_at: '', body_links: {}, brief: null }];
    const { host } = await mount(taskOf([item('i1', 'Solder the header')], { notes }));
    const order = [...host.querySelectorAll('.tk > *')].map(el => el.className.split(' ').find(c => c.startsWith('tk-')));
    expect(order).toEqual(['tk-head', 'tk-list', 'tk-tnotes', 'tk-thread']);
    expect(host.querySelector('.tk-prog')).toBeNull();
  });
});

describe('an item row', () => {
  it('names its panel in aria-controls only while the panel is drawn', async () => {
    const { host } = await mount(taskOf([item('i1', 'Solder the header')]));
    const toggle = host.querySelector('[data-toggle="i1"]') as HTMLElement;
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(toggle.hasAttribute('aria-controls')).toBe(false);

    await press(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(host.querySelector(`#${toggle.getAttribute('aria-controls')}`)).not.toBeNull();

    await press(toggle);
    expect(toggle.hasAttribute('aria-controls')).toBe(false);
    expect(host.querySelector('.tk-panel')).toBeNull();
  });

  it('keeps one panel open at a time', async () => {
    const { host } = await mount(taskOf([item('i1', 'Solder the header'), item('i2', 'Flash the board')]));
    await press(host.querySelector('[data-toggle="i1"]'));
    await press(host.querySelector('[data-toggle="i2"]'));
    expect([...host.querySelectorAll('.tk-panel')].map(p => p.id)).toEqual(['tkp-i2']);
  });
});

describe('an item state', () => {
  it('moves along todo, doing, done from its mark, repainted from the answer', async () => {
    serveApi((path: string, { body }: { body: { item: string; state: string } }) =>
      ({ task: taskOf([item(body.item, 'Solder the header', body.state)]), status: 'active' }));
    const { host, uid } = await mount(taskOf([item('i1', 'Solder the header')]));
    const mark = () => host.querySelector('[data-step="i1"]') as HTMLElement;

    await press(mark());
    await until(() => mark().dataset.s === 'doing');
    expect(calls.at(-1)).toEqual({ path: `/api/tasks/${uid}/item`, method: 'POST', body: { item: 'i1', state: 'doing' } });
    expect(mark().classList.contains('is-pulse')).toBe(true);

    await press(mark());
    await until(() => mark().dataset.s === 'done');
    expect(calls.at(-1)?.body).toEqual({ item: 'i1', state: 'done' });
  });

  it('is set to any other state from the menu', async () => {
    serveApi((path: string, { body }: { body: { item: string; state: string } }) =>
      ({ task: taskOf([item(body.item, 'Solder the header', body.state)]), status: 'active' }));
    const { host } = await mount(taskOf([item('i1', 'Solder the header')]));
    await press(menuOf(host, 'i1'));
    entry(en['task.mark.dropped']).click();
    await until(() => (host.querySelector('[data-step="i1"]') as HTMLElement).dataset.s === 'dropped');
    expect(calls.at(-1)?.body).toEqual({ item: 'i1', state: 'dropped' });
  });

  it('says so when it closes the task, reports the status, and Undo puts the item back', async () => {
    serveApi((path: string, { body }: { body: { item: string; state: string } }) => (body.state === 'done'
      ? { task: taskOf([item('i1', 'Solder the header', 'done')], { state: 'completed', completed_at: '2026-02-01T10:00:00Z' }),
          status: 'archived' }
      : { task: taskOf([item('i1', 'Solder the header', body.state)]), status: 'active' }));
    const onStatus = vi.fn();
    const { host } = await mount(taskOf([item('i1', 'Solder the header', 'doing')]), { onStatus });

    await press(host.querySelector('[data-step="i1"]'));
    await until(() => host.querySelector('.tk-closed.is-completed'));
    expect(onStatus).toHaveBeenCalledWith('archived');
    expect(host.querySelector('.tk-closed')?.classList.contains('is-new')).toBe(true);

    const undo = await until(() => document.querySelector<HTMLElement>('.toast [data-act]'));
    expect(document.querySelector('.toast')?.textContent).toContain(en['task.toast.completed']);
    undo.click();
    await until(() => !host.querySelector('.tk-closed'));
    expect(calls.at(-1)?.body).toEqual({ item: 'i1', state: 'doing' });
    expect(onStatus).toHaveBeenLastCalledWith('active');
  });
});

describe('deleting an item', () => {
  async function remove(host: HTMLElement, key: string, { confirm = true } = {}) {
    await press(menuOf(host, key));
    entry(en['task.item.delete']).click();
    const ok = await until(() => document.querySelector<HTMLElement>(confirm ? '[data-ok]' : '[data-x]'));
    expect(document.querySelector('.modal')?.textContent).toContain(en['task.delete.title']);
    ok.click();
  }

  it('asks first, and does nothing when the question is cancelled', async () => {
    serveApi(() => { throw new Error('unexpected write'); });
    const { host } = await mount(taskOf([item('i1', 'Solder the header'), item('i2', 'Flash the board')]));
    await remove(host, 'i1', { confirm: false });
    await new Promise(done => setTimeout(done, 20));
    expect(calls).toHaveLength(0);
    expect(host.querySelectorAll('.tk-item')).toHaveLength(2);
  });

  it('sends a DELETE for the item and moves focus to the next item, else the previous, else the add control', async () => {
    const items = [item('i1', 'Solder the header'), item('i2', 'Flash the board'), item('i3', 'Seal the case')];
    let left = [...items];
    serveApi((path: string, { body }: { body: { item: string } }) => {
      left = left.filter(i => i.key !== body.item);
      return { task: taskOf(left), status: 'active' };
    });
    const { host, uid } = await mount(taskOf(items));

    await remove(host, 'i2');
    await until(() => host.querySelectorAll('.tk-item').length === 2);
    expect(calls.at(-1)).toEqual({ path: `/api/tasks/${uid}/item`, method: 'DELETE', body: { item: 'i2' } });
    await until(() => document.activeElement === host.querySelector('[data-step="i3"]'));

    await remove(host, 'i3');
    await until(() => host.querySelectorAll('.tk-item').length === 1);
    await until(() => document.activeElement === host.querySelector('[data-step="i1"]'));
  });

  it('is offered for the only item as a disabled entry that gives its reason', async () => {
    const { host } = await mount(taskOf([item('i1', 'Solder the header')]));
    await press(menuOf(host, 'i1'));
    const del = entry(en['task.item.delete']);
    expect(del.getAttribute('aria-disabled')).toBe('true');
    expect(del.textContent).toContain(en['task.item.deleteLast']);
  });

  it('lists the other states above a separator, then the delete entry', async () => {
    const { host } = await mount(taskOf([item('i1', 'Solder the header', 'doing'), item('i2', 'Flash the board')]));
    await press(menuOf(host, 'i1'));
    const menu = document.querySelector('.ctx-menu') as HTMLElement;
    const parts = [...menu.children].map(el => (el.classList.contains('ctx-sep') ? '---' : el.textContent?.trim()));
    expect(parts).toEqual([en['task.mark.todo'], en['task.mark.done'], en['task.mark.dropped'],
                           '---', en['task.item.delete']]);
  });
});

describe('a comment', () => {
  it("names its writer with a mark and a word, a person's and an agent's apart", async () => {
    const comments = [
      { id: 1, item: '', author: 'person', body: 'Check the pinout first', created_at: '2026-01-02T10:00:00Z', session: '' },
      { id: 2, item: '', author: 'agent', body: 'Pinout matches rev B', created_at: '2026-01-02T11:00:00Z', session: 's-001' },
    ];
    const { host } = await mount(taskOf([item('i1', 'Solder the header')], { comments }));
    const [person, agent] = host.querySelectorAll('.tk-thread article.tk-c');
    expect(person.classList.contains('is-person')).toBe(true);
    expect(person.querySelector('.tk-c-who')?.textContent).toBe(en['task.author.person']);
    expect(person.querySelector('.tk-c-av svg')).not.toBeNull();
    expect(agent.classList.contains('is-agent')).toBe(true);
    expect(agent.querySelector('.tk-c-who')?.textContent).toBe(en['task.author.agent']);
    expect(agent.querySelector('.tk-c-av')?.innerHTML).not.toBe(person.querySelector('.tk-c-av')?.innerHTML);
  });

  it('is sent from its draft, which the answer clears', async () => {
    serveApi((path: string, { body }: { body: { body: string } }) => ({
      task: taskOf([item('i1', 'Solder the header')], {
        comments: [{ id: 7, item: '', author: 'person', body: body.body, created_at: '2026-01-02T10:00:00Z', session: '' }] }),
      status: 'active' }));
    const { host, uid } = await mount(taskOf([item('i1', 'Solder the header')]));
    const box = host.querySelector('.tk-thread [data-draft=""]') as HTMLTextAreaElement;
    const send = host.querySelector('.tk-thread [data-send=""]') as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    box.value = '  Flux first  ';
    box.dispatchEvent(new Event('input'));
    await nextTick();
    expect(send.disabled).toBe(false);
    send.click();
    await until(() => host.querySelector('.tk-thread article.tk-c.is-new'));
    expect(calls.at(-1)).toEqual({ path: `/api/tasks/${uid}/comment`, method: 'POST', body: { body: 'Flux first', item: '' } });
    expect(box.value).toBe('');
  });
});

describe('a write', () => {
  it('reports the accepted answer through onWrite', async () => {
    const answer = { task: taskOf([item('i1', 'Solder the header', 'doing')]), status: 'active' };
    serveApi(() => answer);
    const onWrite = vi.fn();
    const { host, uid } = await mount(taskOf([item('i1', 'Solder the header')]), { onWrite });
    await press(host.querySelector('[data-step="i1"]'));
    await until(() => onWrite.mock.calls.length);
    expect(onWrite).toHaveBeenCalledWith(answer);
    expect(calls[0]).toEqual({ path: `/api/tasks/${uid}/item`, method: 'POST', body: { item: 'i1', state: 'doing' } });
  });
});

describe('the in-progress arc', () => {
  it('starts at the phase of the lap admin.css declares', async () => {
    const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', '..',
                                  'src', 'memai', 'webui', 'admin.css'), 'utf8');
    const lap = Number(css.match(/--spin: ([\d.]+)s/)?.[1]);
    vi.spyOn(performance, 'now').mockReturnValue((2 * lap + 0.25) * 1000);
    const { host } = await mount(taskOf([item('i1', 'Solder the header', 'doing')]));
    const ring = host.querySelector('[data-step="i1"] .tk-ring') as HTMLElement;
    expect(ring.style.getPropertyValue('--spin-at')).toBe('-0.25s');
    vi.restoreAllMocks();
  });
});

describe('task notes', () => {
  const note = (id: number, title: string, items: string[] = [], body = `${title} body`) =>
    ({ id, title, body, items, updated_at: '', body_links: {} });
  const type = async (host: HTMLElement, field: string, value: string) => {
    const box = host.querySelector(`[data-note-field="${field}"]`) as HTMLInputElement;
    box.value = value;
    box.dispatchEvent(new Event('input', { bubbles: true }));
    await nextTick();
  };

  it("shows task-level notes above the items and an item's notes in its panel", async () => {
    const notes = [note(1, 'Bench rules'), note(2, 'Header pinout', ['i1'])];
    const { host } = await mount(taskOf([item('i1', 'Solder the header')], { notes }));
    const top = host.querySelector('.tk-tnotes') as HTMLElement;
    expect(top.textContent).toContain('Bench rules');
    expect(top.textContent).not.toContain('Header pinout');
    await press(host.querySelector('[data-toggle="i1"]'));
    expect(host.querySelector('.tk-panel')?.textContent).toContain('Header pinout');
  });

  it('draws a note body as rich text, as a record body is drawn', async () => {
    const notes = [note(1, 'Bench rules', [], 'Use `flux` first.\n\n1. Heat\n2. Solder')];
    const { host } = await mount(taskOf([item('i1', 'Solder the header')], { notes }));
    const body = host.querySelector('.tk-note .rf-body') as HTMLElement;
    expect(body.querySelector('code')?.textContent).toBe('flux');
    expect(body.querySelectorAll('ol li')).toHaveLength(2);
  });

  it('shows a lone note open, and several shut with their opening line', async () => {
    const notes = [note(1, 'Bench rules'), note(2, 'Parts list')];
    const { host } = await mount(taskOf([item('i1', 'Solder the header')], { notes }));
    expect(host.querySelectorAll('.tk-note .rf-body')).toHaveLength(0);
    expect(host.querySelector('.tk-note-peek')?.textContent).toBe('Bench rules body');
    await press(host.querySelector('[data-note-open="1"]'));
    expect(host.querySelector('[data-note-open="1"]')?.getAttribute('aria-expanded')).toBe('true');
    expect(host.querySelectorAll('.tk-note .rf-body')).toHaveLength(1);
  });

  it('keeps save off until the note has a title and a body', async () => {
    const { host } = await mount(taskOf([item('i1', 'Solder the header')]));
    await press(host.querySelector('[data-note-add=""]'));
    const save = () => host.querySelector('[data-note-save]') as HTMLButtonElement;
    expect(save().disabled).toBe(true);
    await type(host, 'title', 'Bench rules');
    expect(save().disabled).toBe(true);
    await type(host, 'body', 'Flux first.');
    expect(save().disabled).toBe(false);
  });

  it('writes a new note on the item whose panel it was added from', async () => {
    serveApi((path: string, { body }: { body: { title: string; items: string[] } }) =>
      ({ task: taskOf([item('i1', 'Solder the header')], { notes: [note(7, body.title, body.items)] }), status: 'active' }));
    const { host, uid } = await mount(taskOf([item('i1', 'Solder the header')]));
    await press(host.querySelector('[data-toggle="i1"]'));
    await press(host.querySelector('.tk-panel [data-note-add="i1"]'));
    expect((host.querySelector('[data-note-scope="i1"]') as HTMLInputElement).checked).toBe(true);
    await type(host, 'title', 'Header pinout');
    await type(host, 'body', 'Pin 1 is square.');
    await press(host.querySelector('[data-note-save]'));
    await until(() => host.querySelector('.tk-panel .tk-note-title'));
    expect(calls.at(-1)).toEqual({ path: `/api/tasks/${uid}/note`, method: 'POST',
      body: { title: 'Header pinout', body: 'Pin 1 is square.', items: ['i1'] } });
  });

  it('moves a note to the whole task when its last item is unpicked', async () => {
    serveApi(() => ({ task: taskOf([item('i1', 'Solder the header')], { notes: [note(3, 'Pinout')] }),
                      status: 'active' }));
    const { host, uid } = await mount(taskOf([item('i1', 'Solder the header')], { notes: [note(3, 'Pinout', ['i1'])] }));
    await press(host.querySelector('[data-toggle="i1"]'));
    await press(host.querySelector('[data-note-edit="3"]'));
    await press(host.querySelector('[data-note-scope="i1"]'));
    expect(host.querySelector('[data-note-scope-sum]')?.textContent).toBe(en['task.note.scope.all']);
    await press(host.querySelector('[data-note-save]'));
    await until(() => calls.length && calls.at(-1)?.path === `/api/tasks/${uid}/note`);
    expect(calls.at(-1)?.body).toEqual({ id: 3, title: 'Pinout', body: 'Pinout body', items: [] });
  });

  it('keeps a draft through other changes and drops it on Escape', async () => {
    const { host } = await mount(taskOf([item('i1', 'Solder the header')]));
    await press(host.querySelector('[data-note-add=""]'));
    await type(host, 'title', 'Bench rules');
    await press(host.querySelector('[data-toggle="i1"]'));
    expect((host.querySelector('[data-note-field="title"]') as HTMLInputElement).value).toBe('Bench rules');
    host.querySelector('[data-note-field="title"]')
      ?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await nextTick();
    expect(host.querySelector('[data-note-form]')).toBeNull();
  });
});

describe('the goal and new items', () => {
  it('saves an edited goal and leaves an unchanged one without a write', async () => {
    serveApi((path: string, { body }: { body: { goal: string } }) =>
      ({ task: taskOf([item('i1', 'Solder the header')], { goal: body.goal }), status: 'active' }));
    const { host, uid } = await mount(taskOf([item('i1', 'Solder the header')]));
    await press(host.querySelector('#tkGoalEdit'));
    await press(host.querySelector('#tkGoalSave'));
    expect(calls).toHaveLength(0);
    expect(host.querySelector('#tkGoalBox')).toBeNull();

    await press(host.querySelector('#tkGoalEdit'));
    const box = host.querySelector('#tkGoalBox') as HTMLTextAreaElement;
    box.value = 'Ship it tonight';
    box.dispatchEvent(new Event('input'));
    await press(host.querySelector('#tkGoalSave'));
    await until(() => host.querySelector('.tk-goal-text')?.textContent === 'Ship it tonight');
    expect(calls.at(-1)).toEqual({ path: `/api/tasks/${uid}/goal`, method: 'POST', body: { goal: 'Ship it tonight' } });
  });

  it('adds the lines typed as items, and the new ones rise in', async () => {
    serveApi(() => ({ task: taskOf([item('i1', 'Solder the header'), item('i2', 'Flash the board')]), status: 'active' }));
    const { host, uid } = await mount(taskOf([item('i1', 'Solder the header')]));
    await press(host.querySelector('#tkAddOpen'));
    const box = host.querySelector('#tkAddBox') as HTMLTextAreaElement;
    box.value = 'Flash the board';
    box.dispatchEvent(new Event('input'));
    await press(host.querySelector('#tkAddSend'));
    await until(() => host.querySelectorAll('.tk-item').length === 2);
    expect(calls.at(-1)).toEqual({ path: `/api/tasks/${uid}/items`, method: 'POST', body: { items: 'Flash the board' } });
    expect(host.querySelector('[data-key="i2"]')?.classList.contains('is-new')).toBe(true);
    expect(host.querySelector('[data-key="i1"]')?.classList.contains('is-new')).toBe(false);
    expect(host.querySelector('#tkAddBox')).toBeNull();
  });
});
