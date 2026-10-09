import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApp, nextTick } from 'vue';
import type { App } from 'vue';
import TaskChecklist from '../../src/memai/webui/views/record/TaskChecklist.vue';
import { notePeek, progressOf, writeDepends } from '../../src/memai/webui/views/record/checklist.ts';
import { calls, catalog, serveApi } from './support.js';

const en = catalog('en');

const item = (id: number, text: string, state = 'todo') =>
  ({ id, n: id, seq: id, text, state, updated_at: '', updated_session: '', links: [] as unknown[] });

const taskOf = (items: ReturnType<typeof item>[], extra = {}) => ({
  goal: 'Ship the lantern firmware', state: 'open', completed_at: '',
  items, comments: [] as unknown[], notes: [] as unknown[], refs: {} as Record<string, unknown>, ...extra,
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
  it('counts done against the items not dropped, and the percentage agrees with the count', () => {
    expect(progressOf(taskOf([item(1, 'a', 'done'), item(2, 'b', 'dropped'), item(3, 'c')])))
      .toEqual({ total: 2, done: 1, pct: 50 });
  });

  it('pct is 100 when every live item is done, and 0 with nothing live', () => {
    expect(progressOf(taskOf([item(1, 'a', 'done'), item(2, 'b', 'dropped')])).pct).toBe(100);
    expect(progressOf(taskOf([item(1, 'a', 'dropped')])).pct).toBe(0);
    expect(progressOf(taskOf([item(1, 'a', 'done'), item(2, 'b'), item(3, 'c')])).pct).toBe(33);
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
    const notes = [{ id: 1, title: 'Bench rules', body: 'Flux first.', items: [], updated_at: '', body_links: {}, brief: null, depends: null }];
    const { host } = await mount(taskOf([item(1, 'Solder the header')], { notes }));
    const order = [...host.querySelectorAll('.tk > *')].map(el => el.className.split(' ').find(c => c.startsWith('tk-')));
    expect(order).toEqual(['tk-head', 'tk-list', 'tk-tnotes', 'tk-thread']);
    expect(host.querySelector('.tk-prog')).toBeNull();
  });
});

describe('an item row', () => {
  it('names its panel in aria-controls only while the panel is drawn', async () => {
    const { host } = await mount(taskOf([item(1, 'Solder the header')]));
    const toggle = host.querySelector('[data-toggle="1"]') as HTMLElement;
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
    const { host } = await mount(taskOf([item(1, 'Solder the header'), item(2, 'Flash the board')]));
    await press(host.querySelector('[data-toggle="1"]'));
    await press(host.querySelector('[data-toggle="2"]'));
    expect([...host.querySelectorAll('.tk-panel')].map(p => p.id)).toEqual(['tkp-2']);
  });
});

describe('item numbers', () => {
  it('shows each item its position before the state mark, and names it to a screen reader', async () => {
    const { host } = await mount(taskOf([item(1, 'Solder the header'), item(2, 'Flash the board')]));
    const rows = [...host.querySelectorAll('.tk-row')];
    expect(rows.map(r => r.querySelector('.tk-num')?.textContent)).toEqual(['1', '2']);
    expect(rows[1].firstElementChild?.classList.contains('tk-num')).toBe(true);
    expect(host.querySelector('[data-step="2"]')?.getAttribute('aria-label')).toMatch(/^2\. Flash the board:/);
  });

  it('numbers the items a note can apply to', async () => {
    const { host } = await mount(taskOf([item(1, 'Solder the header'), item(2, 'Flash the board')]));
    await press(host.querySelector('[data-note-add=""]'));
    expect([...host.querySelectorAll('.tk-scope-n')].map(n => n.textContent)).toEqual(['1', '2']);
  });
});

describe('an item state', () => {
  it('moves along todo, doing, done from its mark, repainted from the answer', async () => {
    serveApi((path: string, { body }: { body: { item: number; state: string } }) =>
      ({ task: taskOf([item(body.item, 'Solder the header', body.state)]), status: 'active' }));
    const { host, uid } = await mount(taskOf([item(1, 'Solder the header')]));
    const mark = () => host.querySelector('[data-step="1"]') as HTMLElement;

    await press(mark());
    await until(() => mark().dataset.s === 'doing');
    expect(calls.at(-1)).toEqual({ path: `/api/tasks/${uid}/item`, method: 'POST', body: { item: 1, state: 'doing' } });
    expect(mark().classList.contains('is-pulse')).toBe(true);

    await press(mark());
    await until(() => mark().dataset.s === 'done');
    expect(calls.at(-1)?.body).toEqual({ item: 1, state: 'done' });
  });

  it('is set to any other state from the menu', async () => {
    serveApi((path: string, { body }: { body: { item: number; state: string } }) =>
      ({ task: taskOf([item(body.item, 'Solder the header', body.state)]), status: 'active' }));
    const { host } = await mount(taskOf([item(1, 'Solder the header')]));
    await press(menuOf(host, '1'));
    entry(en['task.mark.dropped']).click();
    await until(() => (host.querySelector('[data-step="1"]') as HTMLElement).dataset.s === 'dropped');
    expect(calls.at(-1)?.body).toEqual({ item: 1, state: 'dropped' });
  });

  it('says so when it closes the task, reports the status, and Undo puts the item back', async () => {
    serveApi((path: string, { body }: { body: { item: number; state: string } }) => (body.state === 'done'
      ? { task: taskOf([item(1, 'Solder the header', 'done')], { state: 'completed', completed_at: '2026-02-01T10:00:00Z' }),
          status: 'archived' }
      : { task: taskOf([item(1, 'Solder the header', body.state)]), status: 'active' }));
    const onStatus = vi.fn();
    const { host } = await mount(taskOf([item(1, 'Solder the header', 'doing')]), { onStatus });

    await press(host.querySelector('[data-step="1"]'));
    await until(() => host.querySelector('.tk-closed.is-completed'));
    expect(onStatus).toHaveBeenCalledWith('archived');
    expect(host.querySelector('.tk-closed')?.classList.contains('is-new')).toBe(true);

    const undo = await until(() => document.querySelector<HTMLElement>('.toast [data-act]'));
    expect(document.querySelector('.toast')?.textContent).toContain(en['task.toast.completed']);
    undo.click();
    await until(() => !host.querySelector('.tk-closed'));
    expect(calls.at(-1)?.body).toEqual({ item: 1, state: 'doing' });
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

  async function ask(host: HTMLElement, key: string) {
    await press(menuOf(host, key));
    entry(en['task.item.delete']).click();
    return until(() => document.querySelector<HTMLElement>('.modal'));
  }
  const cancel = () => (document.querySelector('[data-x]') as HTMLElement).click();

  it('says nothing about other items moving', async () => {
    serveApi(() => { throw new Error('unexpected write'); });
    const { host } = await mount(taskOf([item(1, 'a'), item(2, 'b'), item(3, 'c')]));
    const text = (await ask(host, '2')).textContent;
    expect(text).not.toContain('become');
    expect(text).not.toContain('number');
    cancel();
  });

  const dependent = (id: number, applies: number[], on: number[]) => ({
    id, title: `Plan ${id}`, body: 'x', items: applies, updated_at: '', body_links: {}, brief: {},
    depends: on.map(at => ({ item: at, text: '', reason: '' })),
  });

  it('names the one item that depends on the item being deleted', async () => {
    serveApi(() => { throw new Error('unexpected write'); });
    const items = [item(1, 'a'), item(2, 'b'), item(3, 'c')];
    const { host } = await mount(taskOf(items, { notes: [dependent(1, [3], [2])] }));
    expect((await ask(host, '2')).textContent)
      .toContain('Item 3 depends on this one; its reference will be marked as deleted.');
    cancel();
  });

  it('names every item that depends on it, by the numbers they have now', async () => {
    serveApi(() => { throw new Error('unexpected write'); });
    const items = [item(1, 'a'), item(2, 'b'), item(3, 'c'), item(4, 'd')];
    const notes = [dependent(1, [3], [2]), dependent(2, [4, 3], [2, 1]), dependent(3, [1], [4])];
    const { host } = await mount(taskOf(items, { notes }));
    expect((await ask(host, '2')).textContent)
      .toContain('Items 3 and 4 depend on this one; their references will be marked as deleted.');
    cancel();
  });

  it('says nothing about dependents when no note depends on the item', async () => {
    serveApi(() => { throw new Error('unexpected write'); });
    const items = [item(1, 'a'), item(2, 'b')];
    const { host } = await mount(taskOf(items, { notes: [dependent(1, [2], [1])] }));
    expect((await ask(host, '2')).textContent).not.toContain('depend');
    cancel();
  });

  it('says the item was deleted', async () => {
    const items = [item(1, 'a'), item(2, 'b'), item(3, 'c')];
    let left = [...items];
    serveApi((path: string, { body }: { body: { item: number } }) => {
      left = left.filter(i => i.id !== body.item);
      return { task: taskOf(left), status: 'active' };
    });
    const { host } = await mount(taskOf(items));
    await remove(host, '2');
    await until(() => host.querySelectorAll('.tk-item').length === 2);
    await until(() => [...document.querySelectorAll('.toast')].some(t => t.textContent?.startsWith(en['task.toast.deleted'] + '×')));
  });

  it('asks first, and does nothing when the question is cancelled', async () => {
    serveApi(() => { throw new Error('unexpected write'); });
    const { host } = await mount(taskOf([item(1, 'Solder the header'), item(2, 'Flash the board')]));
    await remove(host, '1', { confirm: false });
    await new Promise(done => setTimeout(done, 20));
    expect(calls).toHaveLength(0);
    expect(host.querySelectorAll('.tk-item')).toHaveLength(2);
  });

  const serveDeleting = (items: ReturnType<typeof item>[]) => {
    let left = [...items];
    serveApi((path: string, { body }: { body: { item: number } }) => {
      left = left.filter(i => i.id !== body.item);
      return { task: taskOf(left), status: 'active' };
    });
  };
  const textOf = (host: HTMLElement, key: string) => host.querySelector(`[data-key="${key}"] .tk-text`)?.textContent;

  it('sends a DELETE naming the item by id and moves focus to the next item, else the previous, else the add control', async () => {
    const items = [item(1, 'Solder the header'), item(2, 'Flash the board'), item(3, 'Seal the case')];
    serveDeleting(items);
    const { host, uid } = await mount(taskOf(items));

    await remove(host, '2');
    await until(() => host.querySelectorAll('.tk-item').length === 2);
    expect(calls.at(-1)).toEqual({ path: `/api/tasks/${uid}/item`, method: 'DELETE', body: { item: 2 } });
    expect(textOf(host, '3')).toBe('Seal the case');
    expect([...host.querySelectorAll('.tk-num')].map(n => n.textContent)).toEqual(['1', '2']);
    await until(() => document.activeElement === host.querySelector('[data-step="3"]'));

    await remove(host, '3');
    await until(() => host.querySelectorAll('.tk-item').length === 1);
    await until(() => document.activeElement === host.querySelector('[data-step="1"]'));
  });

  it('keeps an open panel and a comment draft with their item when another item goes', async () => {
    const items = [item(1, 'a'), item(2, 'b'), item(3, 'c'), item(4, 'd')];
    serveDeleting(items);
    const { host } = await mount(taskOf(items));
    await press(host.querySelector('[data-toggle="4"]'));
    const box = host.querySelector('.tk-panel [data-draft="4"]') as HTMLTextAreaElement;
    box.value = 'Needs the second flux pen';
    box.dispatchEvent(new Event('input'));
    await nextTick();

    await remove(host, '2');
    await until(() => host.querySelectorAll('.tk-item').length === 3);
    expect(host.querySelector('.tk-panel')?.id).toBe('tkp-4');
    expect((host.querySelector('.tk-panel [data-draft="4"]') as HTMLTextAreaElement).value).toBe('Needs the second flux pen');
  });

  it('is offered for the only item as a disabled entry that gives its reason', async () => {
    const { host } = await mount(taskOf([item(1, 'Solder the header')]));
    await press(menuOf(host, '1'));
    const del = entry(en['task.item.delete']);
    expect(del.getAttribute('aria-disabled')).toBe('true');
    expect(del.textContent).toContain(en['task.item.deleteLast']);
  });

  it('lists the other states above a separator, then renaming and deleting', async () => {
    const { host } = await mount(taskOf([item(1, 'Solder the header', 'doing'), item(2, 'Flash the board')]));
    await press(menuOf(host, '1'));
    const menu = document.querySelector('.ctx-menu') as HTMLElement;
    const parts = [...menu.children].map(el => (el.classList.contains('ctx-sep') ? '---' : el.textContent?.trim()));
    expect(parts).toEqual([en['task.mark.todo'], en['task.mark.done'], en['task.mark.dropped'],
                           '---', en['task.item.rename'], en['task.item.delete']]);
  });
});

describe('renaming an item', () => {
  const items = () => [item(1, 'Solder the header'), item(2, 'Flash the board')];
  const textOf = (host: HTMLElement, key: string) => host.querySelector(`[data-key="${key}"] .tk-text`)?.textContent;
  const type = async (box: HTMLInputElement, text: string) => {
    box.value = text;
    box.dispatchEvent(new Event('input'));
    await nextTick();
  };
  const key = (box: Element, name: string) => box.dispatchEvent(new KeyboardEvent('keydown', { key: name }));

  async function open(host: HTMLElement, at: string) {
    await press(menuOf(host, at));
    entry(en['task.item.rename']).click();
    return until(() => host.querySelector<HTMLInputElement>(`[data-rename="${at}"]`));
  }

  it('edits the text in place and sends it by the item id', async () => {
    serveApi((path: string, { body }: { body: { text: string } }) =>
      ({ task: taskOf([item(1, 'Solder the header'), item(2, body.text)]), status: 'active' }));
    const { host, uid } = await mount(taskOf(items()));
    const box = await open(host, '2');
    expect(box.value).toBe('Flash the board');
    await until(() => document.activeElement === box);

    await type(box, 'Flash the bootloader');
    key(box, 'Enter');
    await until(() => textOf(host, '2') === 'Flash the bootloader');
    expect(calls.at(-1)).toEqual({ path: `/api/tasks/${uid}/item/text`, method: 'POST',
      body: { item: 2, text: 'Flash the bootloader' } });
    await until(() => document.activeElement === host.querySelector('[data-toggle="2"]'));
  });

  it('leaves on Escape without a write, focus back on the item', async () => {
    serveApi(() => { throw new Error('unexpected write'); });
    const { host } = await mount(taskOf(items()));
    const box = await open(host, '1');
    await type(box, 'Something else');
    key(box, 'Escape');
    await until(() => !host.querySelector('[data-rename]'));
    expect(calls).toHaveLength(0);
    expect(textOf(host, '1')).toBe('Solder the header');
    await until(() => document.activeElement === host.querySelector('[data-toggle="1"]'));
  });

  it('keeps the box and the typed text when the server refuses, and says why', async () => {
    serveApi(() => { throw new Error('item 2 cites i1: name the item by what it does'); });
    const { host } = await mount(taskOf(items()));
    const box = await open(host, '2');
    await type(box, 'Flash after i1');
    key(box, 'Enter');
    await until(() => document.querySelector('.toast')?.textContent?.includes(en['task.err.rename']));
    expect(document.querySelector('.toast')?.textContent).toContain('cites i1');
    expect(host.querySelector<HTMLInputElement>('[data-rename="2"]')?.value).toBe('Flash after i1');
  });

  it('counts the text against the item limit and will not save past it', async () => {
    serveApi(() => { throw new Error('unexpected write'); });
    const { host } = await mount(taskOf(items()));
    const box = await open(host, '1');
    await type(box, 'x'.repeat(81));
    const count = host.querySelector('[data-rename-count]') as HTMLElement;
    expect(count.textContent).toBe('81/80');
    expect(count.classList.contains('over')).toBe(true);
    expect((host.querySelector('[data-rename-save]') as HTMLButtonElement).disabled).toBe(true);
    key(box, 'Enter');
    await new Promise(done => setTimeout(done, 20));
    expect(calls).toHaveLength(0);
  });
});

describe('a comment', () => {
  it("names its writer with a mark and a word, a person's and an agent's apart", async () => {
    const comments = [
      { id: 1, item: null, author: 'person', body: 'Check the pinout first', created_at: '2026-01-02T10:00:00Z', session: '' },
      { id: 2, item: null, author: 'agent', body: 'Pinout matches rev B', created_at: '2026-01-02T11:00:00Z', session: 's-001' },
    ];
    const { host } = await mount(taskOf([item(1, 'Solder the header')], { comments }));
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
      task: taskOf([item(1, 'Solder the header')], {
        comments: [{ id: 7, item: null, author: 'person', body: body.body, created_at: '2026-01-02T10:00:00Z', session: '' }] }),
      status: 'active' }));
    const { host, uid } = await mount(taskOf([item(1, 'Solder the header')]));
    const box = host.querySelector('.tk-thread [data-draft=""]') as HTMLTextAreaElement;
    const send = host.querySelector('.tk-thread [data-send=""]') as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    box.value = '  Flux first  ';
    box.dispatchEvent(new Event('input'));
    await nextTick();
    expect(send.disabled).toBe(false);
    send.click();
    await until(() => host.querySelector('.tk-thread article.tk-c.is-new'));
    expect(calls.at(-1)).toEqual({ path: `/api/tasks/${uid}/comment`, method: 'POST', body: { body: 'Flux first', item: 0 } });
    expect(box.value).toBe('');
  });
});

describe('a write', () => {
  it('reports the accepted answer through onWrite', async () => {
    const answer = { task: taskOf([item(1, 'Solder the header', 'doing')]), status: 'active' };
    serveApi(() => answer);
    const onWrite = vi.fn();
    const { host, uid } = await mount(taskOf([item(1, 'Solder the header')]), { onWrite });
    await press(host.querySelector('[data-step="1"]'));
    await until(() => onWrite.mock.calls.length);
    expect(onWrite).toHaveBeenCalledWith(answer);
    expect(calls[0]).toEqual({ path: `/api/tasks/${uid}/item`, method: 'POST', body: { item: 1, state: 'doing' } });
  });
});

describe('the in-progress arc', () => {
  it('starts at the phase of the lap admin.css declares', async () => {
    const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', '..',
                                  'src', 'memai', 'webui', 'admin.css'), 'utf8');
    const lap = Number(css.match(/--spin: ([\d.]+)s/)?.[1]);
    vi.spyOn(performance, 'now').mockReturnValue((2 * lap + 0.25) * 1000);
    const { host } = await mount(taskOf([item(1, 'Solder the header', 'doing')]));
    const ring = host.querySelector('[data-step="1"] .tk-ring') as HTMLElement;
    expect(ring.style.getPropertyValue('--spin-at')).toBe('-0.25s');
    vi.restoreAllMocks();
  });
});

describe('task notes', () => {
  const note = (id: number, title: string, items: number[] = [], body = `${title} body`,
                brief: Record<string, string> | null = null, depends: unknown[] | null = null) =>
    ({ id, title, body, items, updated_at: '', body_links: {}, brief, depends });
  const TYPED = { goal: 'Seat the header', context: 'Pin 1 is square', steps: 'Tack two corners',
                  pitfalls: 'Cold joints', done_when: 'Every pin is wet' };
  const FIELDS = { ...TYPED, depends_on: 'none' };
  const BRIEF_BODY = 'GOAL: Seat the header\nCONTEXT: Pin 1 is square\nSTEPS: Tack two corners\n'
    + 'PITFALLS: Cold joints\nDONE WHEN: Every pin is wet\nDEPENDS ON: none';
  const type = async (host: HTMLElement, field: string, value: string) => {
    const box = host.querySelector(`[data-note-field="${field}"]`) as HTMLInputElement;
    box.value = value;
    box.dispatchEvent(new Event('input', { bubbles: true }));
    await nextTick();
  };

  it("shows task-level notes above the items and an item's notes in its panel", async () => {
    const notes = [note(1, 'Bench rules'), note(2, 'Header pinout', [1])];
    const { host } = await mount(taskOf([item(1, 'Solder the header')], { notes }));
    const top = host.querySelector('.tk-tnotes') as HTMLElement;
    expect(top.textContent).toContain('Bench rules');
    expect(top.textContent).not.toContain('Header pinout');
    await press(host.querySelector('[data-toggle="1"]'));
    expect(host.querySelector('.tk-panel')?.textContent).toContain('Header pinout');
  });

  it('draws a note body as rich text, as a record body is drawn', async () => {
    const notes = [note(1, 'Bench rules', [], 'Use `flux` first.\n\n1. Heat\n2. Solder')];
    const { host } = await mount(taskOf([item(1, 'Solder the header')], { notes }));
    const body = host.querySelector('.tk-note .rf-body') as HTMLElement;
    expect(body.querySelector('code')?.textContent).toBe('flux');
    expect(body.querySelectorAll('ol li')).toHaveLength(2);
  });

  it('shows a lone note open, and several shut with their opening line', async () => {
    const notes = [note(1, 'Bench rules'), note(2, 'Parts list')];
    const { host } = await mount(taskOf([item(1, 'Solder the header')], { notes }));
    expect(host.querySelectorAll('.tk-note .rf-body')).toHaveLength(0);
    expect(host.querySelector('.tk-note-peek')?.textContent).toBe('Bench rules body');
    await press(host.querySelector('[data-note-open="1"]'));
    expect(host.querySelector('[data-note-open="1"]')?.getAttribute('aria-expanded')).toBe('true');
    expect(host.querySelectorAll('.tk-note .rf-body')).toHaveLength(1);
  });

  it('keeps save off until the note has a title and a body', async () => {
    const { host } = await mount(taskOf([item(1, 'Solder the header')]));
    await press(host.querySelector('[data-note-add=""]'));
    const save = () => host.querySelector('[data-note-save]') as HTMLButtonElement;
    expect(save().disabled).toBe(true);
    await type(host, 'title', 'Bench rules');
    expect(save().disabled).toBe(true);
    await type(host, 'body', 'Flux first.');
    expect(save().disabled).toBe(false);
  });

  it('writes a new note on the item whose panel it was added from', async () => {
    serveApi((path: string, { body }: { body: { title: string; items: number[] } }) =>
      ({ task: taskOf([item(1, 'Solder the header')], { notes: [note(7, body.title, body.items)] }), status: 'active' }));
    const { host, uid } = await mount(taskOf([item(1, 'Solder the header')]));
    await press(host.querySelector('[data-toggle="1"]'));
    await press(host.querySelector('.tk-panel [data-note-add="1"]'));
    expect((host.querySelector('[data-note-scope="1"]') as HTMLInputElement).checked).toBe(true);
    await type(host, 'title', 'Header pinout');
    for (const [key, value] of Object.entries(TYPED)) await type(host, key, value);
    await press(host.querySelector('[data-note-save]'));
    await until(() => host.querySelector('.tk-panel .tk-note-title'));
    expect(calls.at(-1)).toEqual({ path: `/api/tasks/${uid}/note`, method: 'POST',
      body: { title: 'Header pinout', body: BRIEF_BODY, items: [1] } });
  });

  it('draws a brief field by field, extra info last and only when present', async () => {
    const notes = [note(1, 'Header', [1], BRIEF_BODY, FIELDS)];
    const { host } = await mount(taskOf([item(1, 'Solder the header')], { notes }));
    await press(host.querySelector('[data-toggle="1"]'));
    const keys = [...host.querySelectorAll('.tk-panel [data-brief]')].map(el => el.getAttribute('data-brief'));
    expect(keys).toEqual(['goal', 'context', 'steps', 'pitfalls', 'done_when', 'depends_on']);
    expect(host.querySelector('[data-brief="goal"] .tk-brief-h')?.textContent).toBe(en['sec.task_note.goal']);
  });

  it('peeks at a shut brief by its goal', async () => {
    const notes = [note(1, 'Header', [], BRIEF_BODY, FIELDS), note(2, 'Parts')];
    const { host } = await mount(taskOf([item(1, 'Solder the header')], { notes }));
    expect(host.querySelector('.tk-note-peek')?.textContent).toBe('Seat the header');
  });

  it('asks for every required field once an item is picked, and sends the brief as its body', async () => {
    serveApi((path: string, { body }: { body: { title: string; items: number[] } }) =>
      ({ task: taskOf([item(1, 'Solder the header')], { notes: [note(7, body.title, body.items, BRIEF_BODY, FIELDS)] }), status: 'active' }));
    const { host, uid } = await mount(taskOf([item(1, 'Solder the header')]));
    await press(host.querySelector('[data-toggle="1"]'));
    await press(host.querySelector('.tk-panel [data-note-add="1"]'));
    expect(host.querySelector('[data-note-field="body"]')).toBeNull();
    const save = () => host.querySelector('[data-note-save]') as HTMLButtonElement;
    await type(host, 'title', 'Header');
    for (const [key, value] of Object.entries(TYPED).slice(0, 4)) await type(host, key, value);
    expect(save().disabled).toBe(true);
    await type(host, 'done_when', TYPED.done_when);
    expect(save().disabled).toBe(false);
    await press(save());
    await until(() => calls.length);
    expect(calls.at(-1)).toEqual({ path: `/api/tasks/${uid}/note`, method: 'POST',
      body: { title: 'Header', body: BRIEF_BODY, items: [1] } });
  });

  it('opens a stored free item note with its text under extra info', async () => {
    const { host } = await mount(taskOf([item(1, 'Solder the header')], { notes: [note(3, 'Pinout', [1])] }));
    await press(host.querySelector('[data-toggle="1"]'));
    await press(host.querySelector('[data-note-edit="3"]'));
    expect((host.querySelector('[data-note-field="extra_info"]') as HTMLTextAreaElement).value).toBe('Pinout body');
    expect((host.querySelector('[data-note-field="goal"]') as HTMLTextAreaElement).value).toBe('');
  });

  it('moves a note to the whole task when its last item is unpicked', async () => {
    serveApi(() => ({ task: taskOf([item(1, 'Solder the header')], { notes: [note(3, 'Pinout')] }),
                      status: 'active' }));
    const { host, uid } = await mount(taskOf([item(1, 'Solder the header')], { notes: [note(3, 'Pinout', [1])] }));
    await press(host.querySelector('[data-toggle="1"]'));
    await press(host.querySelector('[data-note-edit="3"]'));
    await press(host.querySelector('[data-note-scope="1"]'));
    expect(host.querySelector('[data-note-scope-sum]')?.textContent).toBe(en['task.note.scope.all']);
    await press(host.querySelector('[data-note-save]'));
    await until(() => calls.length && calls.at(-1)?.path === `/api/tasks/${uid}/note`);
    expect(calls.at(-1)?.body).toEqual({ id: 3, title: 'Pinout', body: 'Pinout body', items: [] });
  });

  const fieldOf = (host: HTMLElement, field: string) =>
    host.querySelector(`[data-note-field="${field}"]`) as HTMLTextAreaElement;

  it('carries an edit to the body back under extra info when an item is picked again', async () => {
    const { host } = await mount(taskOf([item(1, 'Solder the header')], { notes: [note(3, 'Pinout', [1])] }));
    await press(host.querySelector('[data-toggle="1"]'));
    await press(host.querySelector('[data-note-edit="3"]'));
    await press(host.querySelector('[data-note-scope="1"]'));
    expect(fieldOf(host, 'body').value).toBe('Pinout body');
    await type(host, 'body', 'Pinout body, revised');
    await press(host.querySelector('[data-note-scope="1"]'));
    expect(fieldOf(host, 'extra_info').value).toBe('Pinout body, revised');
    expect(fieldOf(host, 'goal').value).toBe('');
  });

  it('turns a brief into labelled text when its last item is unpicked, and an emptied brief into nothing', async () => {
    const notes = [note(3, 'Header', [1], BRIEF_BODY, FIELDS)];
    const { host } = await mount(taskOf([item(1, 'Solder the header')], { notes }));
    await press(host.querySelector('[data-toggle="1"]'));
    await press(host.querySelector('[data-note-edit="3"]'));
    await press(host.querySelector('[data-note-scope="1"]'));
    expect(fieldOf(host, 'body').value).toBe(BRIEF_BODY);
    await press(host.querySelector('[data-note-scope="1"]'));
    for (const key of [...Object.keys(TYPED), 'extra_info']) await type(host, key, '');
    await press(host.querySelector('[data-note-scope="1"]'));
    expect(fieldOf(host, 'body').value).toBe('');
    expect((host.querySelector('[data-note-save]') as HTMLButtonElement).disabled).toBe(true);
  });

  it('restores the fields of a stored brief when its last item is unpicked and picked again', async () => {
    const notes = [note(3, 'Header', [1], BRIEF_BODY, FIELDS)];
    const { host } = await mount(taskOf([item(1, 'Solder the header')], { notes }));
    await press(host.querySelector('[data-toggle="1"]'));
    await press(host.querySelector('[data-note-edit="3"]'));
    await press(host.querySelector('[data-note-scope="1"]'));
    await press(host.querySelector('[data-note-scope="1"]'));
    expect(fieldOf(host, 'goal').value).toBe('Seat the header');
    expect(fieldOf(host, 'extra_info').value).toBe('');
  });

  it('puts an edit made while no item was picked under extra info when one is picked again', async () => {
    const notes = [note(3, 'Header', [1], BRIEF_BODY, FIELDS)];
    const { host } = await mount(taskOf([item(1, 'Solder the header')], { notes }));
    await press(host.querySelector('[data-toggle="1"]'));
    await press(host.querySelector('[data-note-edit="3"]'));
    await press(host.querySelector('[data-note-scope="1"]'));
    await type(host, 'body', `${BRIEF_BODY}
A late thought.`);
    await press(host.querySelector('[data-note-scope="1"]'));
    expect(fieldOf(host, 'extra_info').value).toBe(`${BRIEF_BODY}
A late thought.`);
    expect(fieldOf(host, 'goal').value).toBe('');
  });

  it('marks the required brief fields and leaves extra info unmarked', async () => {
    const notes = [note(3, 'Header', [1], BRIEF_BODY, FIELDS)];
    const { host } = await mount(taskOf([item(1, 'Solder the header')], { notes }));
    await press(host.querySelector('[data-toggle="1"]'));
    await press(host.querySelector('[data-note-edit="3"]'));
    for (const key of Object.keys(TYPED)) expect(fieldOf(host, key).getAttribute('aria-required')).toBe('true');
    expect(fieldOf(host, 'extra_info').hasAttribute('aria-required')).toBe(false);
    expect(fieldOf(host, 'extra_info').hasAttribute('aria-label')).toBe(false);
  });

  it('names each scope checkbox by its number and text', async () => {
    const { host } = await mount(taskOf([item(1, 'Solder the header'), item(2, 'Flash the board')]));
    await press(host.querySelector('[data-note-add=""]'));
    expect(host.querySelector('[data-note-scope="2"]')?.getAttribute('aria-label')).toBe('2. Flash the board');
  });

  it('keeps the brief a whole-task note holds when an item is picked and its body is unchanged', async () => {
    const notes = [note(5, 'Header', [], BRIEF_BODY, FIELDS)];
    const { host } = await mount(taskOf([item(1, 'Solder the header')], { notes }));
    await press(host.querySelector('[data-note-edit="5"]'));
    await press(host.querySelector('[data-note-scope="1"]'));
    expect(fieldOf(host, 'goal').value).toBe('Seat the header');
    expect(fieldOf(host, 'extra_info').value).toBe('');
  });

  it("keeps a brief's text, as the body, when the item it applies to is deleted", async () => {
    const items = [item(1, 'Solder the header'), item(2, 'Flash the board')];
    serveApi(() => ({ task: taskOf([items[0]], { notes: [note(3, 'Header', [], BRIEF_BODY, FIELDS)] }), status: 'active' }));
    const { host } = await mount(taskOf(items, { notes: [note(3, 'Header', [2], BRIEF_BODY, FIELDS)] }));
    await press(host.querySelector('[data-toggle="2"]'));
    await press(host.querySelector('[data-note-edit="3"]'));
    await type(host, 'goal', 'Seat the header flush');
    await press(menuOf(host, '2'));
    entry(en['task.item.delete']).click();
    (await until(() => document.querySelector<HTMLElement>('[data-ok]'))).click();
    await until(() => host.querySelectorAll('.tk-item').length === 1);
    await until(() => fieldOf(host, 'body'));
    expect(fieldOf(host, 'body').value).toBe(BRIEF_BODY.replace('Seat the header\n', 'Seat the header flush\n'));
  });

  it('refuses a brief longer than a note may be, and marks the counter', async () => {
    const { host } = await mount(taskOf([item(1, 'Solder the header')], { notes: [note(3, 'Header', [1], BRIEF_BODY, FIELDS)] }));
    await press(host.querySelector('[data-toggle="1"]'));
    await press(host.querySelector('[data-note-edit="3"]'));
    const save = host.querySelector('[data-note-save]') as HTMLButtonElement;
    const count = host.querySelector('[data-note-count]') as HTMLElement;
    expect(save.disabled).toBe(false);
    expect(count.classList.contains('over')).toBe(false);
    await type(host, 'steps', 'x'.repeat(4000));
    expect(save.disabled).toBe(true);
    expect(count.classList.contains('over')).toBe(true);
  });

  describe('DEPENDS ON', () => {
    const items = [item(1, 'Solder the header'), item(2, 'Flash the board'), item(3, 'Run the week-long battery test')];
    const withDepends = (depends: unknown[] | null, text = '[[#1]] (needs the header), deleted "Old step"') =>
      taskOf(items, { notes: [note(1, 'Flash plan', [2], BRIEF_BODY, { ...FIELDS, depends_on: text }, depends)] });
    const OPENS = [{ item: 1, text: '', reason: 'needs the header' }, { item: null, text: 'Old step', reason: '' }];
    const field = (host: HTMLElement) => host.querySelector('.tk-panel [data-brief="depends_on"]') as HTMLElement;
    const openPanel = async (host: HTMLElement, key = 2) => press(host.querySelector(`[data-toggle="${key}"]`));

    it('draws an item as a chip with its number and text, then the reason', async () => {
      const { host } = await mount(withDepends(OPENS));
      await openPanel(host);
      const chip = field(host).querySelector('button') as HTMLElement;
      expect(chip.textContent).toBe('1 · Solder the header');
      expect(chip.getAttribute('aria-label')).toBe(`Go to item 1: Solder the header, ${en['task.state.todo']}`);
      expect(chip.closest('li')?.querySelector('.tk-dep-why')?.textContent).toBe('needs the header');
    });

    it('draws a deleted entry struck through, named for assistive tech, with no button', async () => {
      const { host } = await mount(withDepends(OPENS));
      await openPanel(host);
      expect(field(host).querySelectorAll('button')).toHaveLength(1);
      const gone = field(host).querySelector('s') as HTMLElement;
      expect(gone.textContent).toBe('Old step');
      expect(gone.closest('li')?.querySelector('.sr-only')?.textContent).toContain(en['task.depends.deleted']);
    });

    it('draws an id that is not on the list plainly', async () => {
      const { host } = await mount(withDepends([{ item: 9, text: '', reason: '' }]));
      await openPanel(host);
      expect(field(host).querySelector('button')).toBeNull();
      expect(field(host).textContent).toContain(9);
    });

    describe('the state mark on a chip', () => {
      const STATES = ['todo', 'doing', 'done', 'dropped'];
      const states = STATES.map((s, at) => item(at + 1, `Step ${s}`, s));
      const target = item(5, 'Close the lid');
      const asks = STATES.map((_, at) => ({ item: at + 1, text: '', reason: '' }));
      const withStates = () => taskOf([...states, target],
        { notes: [note(1, 'Lid plan', [5], BRIEF_BODY, { ...FIELDS, depends_on: '[[#1]], [[#2]], [[#3]], [[#4]]' }, asks)] });
      const chips = (host: HTMLElement) => [...field(host).querySelectorAll<HTMLElement>('.tk-dep-chip')];
      const markOf = (chip: HTMLElement) => chip.querySelector<HTMLElement>('.tk-dep-mark');

      it('opens each chip with the mark its row uses, and ends its name with the state', async () => {
        const { host } = await mount(withStates());
        await openPanel(host, '5');
        expect(chips(host)).toHaveLength(4);
        chips(host).forEach((chip, at) => {
          const state = STATES[at];
          expect(markOf(chip)?.dataset.s).toBe(state);
          expect(chip.firstElementChild).toBe(markOf(chip));
          const row = host.querySelector(`.tk-state[data-step="${at + 1}"] .tk-ring`) as HTMLElement;
          expect(markOf(chip)?.querySelector('.tk-ring')?.innerHTML).toBe(row.innerHTML);
          expect(chip.getAttribute('aria-label')).toBe(`Go to item ${at + 1}: Step ${state}, ${en[`task.state.${state}`]}`);
          expect(chip.textContent?.trim()).toBe(`${at + 1} · Step ${state}`);
        });
        expect(markOf(chips(host)[0])?.querySelector('svg')).toBeNull();
        expect(markOf(chips(host)[1])?.querySelector('svg')).not.toBeNull();
      });

      it('follows the item when its state changes, without redrawing the chip', async () => {
        serveApi((path: string, { body }: { body: { item: number; state: string } }) =>
          ({ task: { ...withStates(), items: states.map(s => (s.id === body.item ? { ...s, state: body.state } : s)).concat(target) },
             status: 'active' }));
        const { host } = await mount(withStates());
        await openPanel(host, '5');
        const chip = chips(host)[0];
        const mark = markOf(chip) as HTMLElement;
        expect(mark.dataset.s).toBe('todo');
        await press(host.querySelector('[data-step="1"]'));
        await until(() => mark.dataset.s === 'doing');
        expect(chips(host)[0]).toBe(chip);
        expect(markOf(chip)).toBe(mark);
        expect(chip.getAttribute('aria-label')).toContain(en['task.state.doing']);
      });

      it('holds the doing mark still', async () => {
        const { host } = await mount(withStates());
        await openPanel(host, '5');
        const doing = markOf(chips(host)[1]) as HTMLElement;
        expect(doing.dataset.s).toBe('doing');
        expect(doing.outerHTML).not.toContain('spin');
        expect(doing.style.getPropertyValue('--spin-at')).toBe('');
        expect(doing.querySelector('.tk-ring')?.getAttribute('style')).toBeNull();
        expect(doing.querySelector('svg')?.getAttribute('style')).toBeNull();
      });

      it('leaves a deleted entry and an unknown key without a mark', async () => {
        const { host } = await mount(withDepends([{ item: 9, text: '', reason: '' }, { item: null, text: 'Old step', reason: '' }]));
        await openPanel(host);
        expect(field(host).querySelector('.tk-dep-mark')).toBeNull();
      });
    });

    it('reads none as the translated word', async () => {
      const { host } = await mount(withDepends([], 'none'));
      await openPanel(host);
      expect(field(host).querySelector('button')).toBeNull();
      expect(field(host).textContent).toContain(en['task.depends.none']);
    });

    it('draws the text of a field that predates the grammar', async () => {
      const { host } = await mount(withDepends(null, 'Item 9 and the bench'));
      await openPanel(host);
      expect(field(host).querySelector('button')).toBeNull();
      expect(field(host).textContent).toContain('Item 9 and the bench');
    });

    describe('a chip', () => {
      afterEach(() => { vi.useRealTimers(); });

      it('focuses the item, flashes its row for a moment, and opens nothing', async () => {
        const { host } = await mount(withDepends(OPENS));
        await openPanel(host);
        vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
        await press(field(host).querySelector('button'));
        const row = host.querySelector('.tk-item[data-key="1"]') as HTMLElement;
        expect(document.activeElement).toBe(host.querySelector('[data-toggle="1"]'));
        expect(row.classList.contains('is-flash')).toBe(true);
        expect(host.querySelector('[data-toggle="1"]')?.getAttribute('aria-expanded')).toBe('false');
        expect(host.querySelector('#tkp-1')).toBeNull();
        vi.advanceTimersByTime(1300);
        await nextTick();
        expect(row.classList.contains('is-flash')).toBe(false);
      });

      it('restarts the flash on a second click', async () => {
        const { host } = await mount(withDepends(OPENS));
        await openPanel(host);
        vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
        const chip = field(host).querySelector('button');
        const row = host.querySelector('.tk-item[data-key="1"]') as HTMLElement;
        await press(chip);
        vi.advanceTimersByTime(800);
        await press(chip);
        await nextTick();
        await nextTick();
        vi.advanceTimersByTime(800);
        await nextTick();
        expect(row.classList.contains('is-flash')).toBe(true);
        vi.advanceTimersByTime(500);
        await nextTick();
        expect(row.classList.contains('is-flash')).toBe(false);
      });
    });

    describe('in the editor', () => {
      const edit = async (host: HTMLElement) => {
        await openPanel(host, '2');
        await press(host.querySelector('[data-note-edit="1"]'));
      };
      const save = (host: HTMLElement) => host.querySelector('[data-note-save]') as HTMLButtonElement;
      const rows = (host: HTMLElement) => [...host.querySelectorAll<HTMLElement>('[data-dep-row]')];
      const sent = () => (calls.at(-1)?.body as { body: string }).body.split('\n').find(l => l.startsWith('DEPENDS ON:'));
      const answer = () => serveApi(() => ({ task: withDepends(OPENS), status: 'active' }));

      it('lists each dependency as a chip with its reason, a deleted one by its text', async () => {
        const { host } = await mount(withDepends(OPENS));
        await edit(host);
        expect(rows(host)).toHaveLength(2);
        expect(rows(host)[0].querySelector('.tk-dep-chip')?.textContent?.trim()).toBe('1 · Solder the header');
        expect((rows(host)[0].querySelector('[data-dep-reason]') as HTMLInputElement).value).toBe('needs the header');
        expect(rows(host)[1].querySelector('s')?.textContent).toBe('Old step');
        expect(host.querySelector('[data-note-field="depends_on"]')).toBeNull();
      });

      it('sends the chips back as tokens, with a reason edited in place', async () => {
        answer();
        const { host } = await mount(withDepends(OPENS));
        await edit(host);
        const why = rows(host)[0].querySelector('[data-dep-reason]') as HTMLInputElement;
        why.value = 'the header seats it';
        why.dispatchEvent(new Event('input'));
        await nextTick();
        await press(save(host));
        await until(() => calls.length);
        expect(sent()).toBe('DEPENDS ON: [[#1]] (the header seats it), deleted "Old step"');
      });

      it('removes a dependency', async () => {
        answer();
        const { host } = await mount(withDepends(OPENS));
        await edit(host);
        await press(rows(host)[0].querySelector('[data-dep-remove]'));
        expect(rows(host)).toHaveLength(1);
        await press(save(host));
        await until(() => calls.length);
        expect(sent()).toBe('DEPENDS ON: deleted "Old step"');
      });

      it('offers only the items the note neither applies to nor already waits for', async () => {
        answer();
        const { host } = await mount(withDepends(OPENS));
        await edit(host);
        const add = host.querySelector('[data-dep-add]') as HTMLSelectElement;
        expect([...add.options].map(o => o.value).filter(Boolean)).toEqual(['3']);
        add.value = '3';
        add.dispatchEvent(new Event('change'));
        await nextTick();
        expect(rows(host)).toHaveLength(3);
        await press(save(host));
        await until(() => calls.length);
        expect(sent()).toBe('DEPENDS ON: [[#1]] (needs the header), deleted "Old step", [[#3]]');
      });

      it('sends none when no chip is left', async () => {
        answer();
        const { host } = await mount(withDepends([{ item: null, text: 'Old step', reason: '' }]));
        await edit(host);
        await press(rows(host)[0].querySelector('[data-dep-remove]'));
        await press(save(host));
        await until(() => calls.length);
        expect(sent()).toBe('DEPENDS ON: none');
      });

      it('holds Save while the note comes to apply to an item it waits for, and says which', async () => {
        const { host } = await mount(withDepends([{ item: 3, text: '', reason: '' }], '[[#3]]'));
        await edit(host);
        expect(save(host).disabled).toBe(false);
        await press(host.querySelector('[data-note-scope="3"]'));
        expect(host.querySelector('[data-depends-error]')?.textContent).toBe(
          en['task.depends.error.self'].replace('{n}', '3'));
        expect(save(host).disabled).toBe(true);
      });

      it('shows a field written before the grammar as text, with no chip', async () => {
        const { host } = await mount(withDepends(null, 'Item 9 and the bench'));
        await edit(host);
        expect(rows(host)).toHaveLength(0);
        expect(host.querySelector('[data-dep-legacy]')?.textContent).toContain('Item 9 and the bench');
      });
    });
  });

  it('keeps a draft through other changes and drops it on Escape', async () => {
    const { host } = await mount(taskOf([item(1, 'Solder the header')]));
    await press(host.querySelector('[data-note-add=""]'));
    await type(host, 'title', 'Bench rules');
    await press(host.querySelector('[data-toggle="1"]'));
    expect((host.querySelector('[data-note-field="title"]') as HTMLInputElement).value).toBe('Bench rules');
    host.querySelector('[data-note-field="title"]')
      ?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await nextTick();
    expect(host.querySelector('[data-note-form]')).toBeNull();
  });
});

describe('the DEPENDS ON writer', () => {
  it('writes ids as tokens with their reasons, a deleted item by its text, and none for nothing', () => {
    expect(writeDepends([{ item: 41, text: '', reason: 'power' }, { item: null, text: 'Old "step"', reason: '' }]))
      .toBe('[[#41]] (power), deleted "Old \'step\'"');
    expect(writeDepends([{ item: null, text: 'Fix the mount\nagain', reason: '' }])).toBe('deleted "Fix the mount again"');
    expect(writeDepends([])).toBe('none');
  });
});

describe('mentions', () => {
  const withGoal = (goal: string, refs: Record<string, unknown>) =>
    taskOf([item(41, 'Solder the header'), item(42, 'Flash the board', 'done')], { goal, refs });

  it('draws a mention in the goal as the item number, its mark and its label', async () => {
    const { host } = await mount(withGoal('After [[#42]], ship it', { 42: { n: 2, text: 'Flash the board', state: 'done' } }));
    const chip = host.querySelector('.tk-goal .rt-item[data-item="42"]') as HTMLElement;
    expect(chip.textContent?.trim()).toBe('2 · Flash the board');
    expect(chip.querySelector('.tk-dep-mark')?.getAttribute('data-s')).toBe('done');
    expect(chip.getAttribute('aria-label')).toBe(en['task.mention.open'].replace('{n}', '2').replace('{text}', 'Flash the board'));
    expect(host.querySelector('.tk-goal-text')?.textContent).toContain('ship it');
  });

  it('draws a deleted item and [[#0]] as a muted mark with no button', async () => {
    const { host } = await mount(withGoal('After [[#7]] and [[#0]]', { 7: { deleted: true }, 0: { deleted: true } }));
    const gone = [...host.querySelectorAll('.tk-goal .rt-item.is-gone')];
    expect(gone).toHaveLength(2);
    expect(gone[0].textContent).toBe(en['task.mention.deleted']);
    expect(host.querySelector('.tk-goal button.rt-item')).toBeNull();
  });

  it('flashes the item a mention points at', async () => {
    const { host } = await mount(withGoal('After [[#42]]', { 42: { n: 2, text: 'Flash the board', state: 'done' } }));
    await press(host.querySelector('.tk-goal .rt-item[data-item="42"]'));
    expect(host.querySelector('.tk-item[data-key="42"]')?.classList.contains('is-flash')).toBe(true);
  });

  it('draws mentions in a comment', async () => {
    const comments = [{ id: 1, item: null, author: 'agent', body: 'blocked on [[#41]]', created_at: '2026-01-02T10:00:00Z', session: '' }];
    const { host } = await mount(taskOf([item(41, 'Solder the header')],
      { comments, refs: { 41: { n: 1, text: 'Solder the header', state: 'todo' } } }));
    expect(host.querySelector('.tk-thread .rt-item[data-item="41"]')?.textContent?.trim()).toBe('1 · Solder the header');
  });
});

describe('the goal and new items', () => {
  it('saves an edited goal and leaves an unchanged one without a write', async () => {
    serveApi((path: string, { body }: { body: { goal: string } }) =>
      ({ task: taskOf([item(1, 'Solder the header')], { goal: body.goal }), status: 'active' }));
    const { host, uid } = await mount(taskOf([item(1, 'Solder the header')]));
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
    serveApi(() => ({ task: taskOf([item(1, 'Solder the header'), item(2, 'Flash the board')]), status: 'active' }));
    const { host, uid } = await mount(taskOf([item(1, 'Solder the header')]));
    await press(host.querySelector('#tkAddOpen'));
    const box = host.querySelector('#tkAddBox') as HTMLTextAreaElement;
    box.value = 'Flash the board';
    box.dispatchEvent(new Event('input'));
    await press(host.querySelector('#tkAddSend'));
    await until(() => host.querySelectorAll('.tk-item').length === 2);
    expect(calls.at(-1)).toEqual({ path: `/api/tasks/${uid}/items`, method: 'POST', body: { items: 'Flash the board' } });
    expect(host.querySelector('[data-key="2"]')?.classList.contains('is-new')).toBe(true);
    expect(host.querySelector('[data-key="1"]')?.classList.contains('is-new')).toBe(false);
    expect(host.querySelector('#tkAddBox')).toBeNull();
  });
});
