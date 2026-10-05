import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';
import { mountTask } from '../../src/memai/webui/views/task.js';
import { calls, catalog, serveApi } from './support.js';

const en = catalog('en');

const item = (key, text, state = 'todo') => ({ key, text, state, links: [] });

const taskOf = (items, extra = {}) => ({
  goal: 'Ship the lantern firmware', state: 'open', completed_at: null,
  items, comments: [], ...extra,
});

let n = 0;
function mount(task, hooks = {}) {
  const host = document.getElementById('view');
  const uid = `task${String(n++).padStart(12, '0')}`;
  mountTask(host, { uid, task, status: 'active' }, hooks);
  return { host, uid };
}

async function until(check, ms = 2000) {
  const end = Date.now() + ms;
  for (;;) {
    const got = check();
    if (got) return got;
    if (Date.now() > end) throw new Error('condition never held');
    await new Promise(done => setTimeout(done, 5));
  }
}

const menuOf = (host, key) => host.querySelector(`[data-menu="${key}"]`);
const entry = label => [...document.querySelectorAll('.ctx-item')]
  .find(b => b.textContent.includes(label));

describe('an item row', () => {
  it('names its panel in aria-controls only while the panel is drawn', () => {
    const { host } = mount(taskOf([item('i1', 'Solder the header')]));
    const toggle = host.querySelector('[data-toggle="i1"]');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(toggle.hasAttribute('aria-controls')).toBe(false);

    toggle.click();
    const open = host.querySelector('[data-toggle="i1"]');
    expect(open.getAttribute('aria-expanded')).toBe('true');
    expect(host.querySelector(`#${open.getAttribute('aria-controls')}`)).not.toBeNull();

    open.click();
    const closed = host.querySelector('[data-toggle="i1"]');
    expect(closed.hasAttribute('aria-controls')).toBe(false);
    expect(host.querySelector('.tk-panel')).toBeNull();
  });
});

describe('deleting an item', () => {
  async function remove(host, key, { confirm = true } = {}) {
    menuOf(host, key).click();
    entry(en['task.item.delete']).click();
    const ok = await until(() => document.querySelector(confirm ? '[data-ok]' : '[data-x]'));
    expect(document.querySelector('.modal').textContent).toContain(en['task.delete.title']);
    ok.click();
  }

  it('asks first, and does nothing when the question is cancelled', async () => {
    serveApi(() => { throw new Error('unexpected write'); });
    const { host } = mount(taskOf([item('i1', 'Solder the header'), item('i2', 'Flash the board')]));
    await remove(host, 'i1', { confirm: false });
    await new Promise(done => setTimeout(done, 20));
    expect(calls).toHaveLength(0);
    expect(host.querySelectorAll('.tk-item')).toHaveLength(2);
  });

  it('sends a DELETE for the item and moves focus to the next item, else the previous, else the add control', async () => {
    const items = [item('i1', 'Solder the header'), item('i2', 'Flash the board'), item('i3', 'Seal the case')];
    let left = [...items];
    serveApi((path, { method, body }) => {
      left = left.filter(i => i.key !== body.item);
      return { task: taskOf(left), status: 'active' };
    });
    const { host, uid } = mount(taskOf(items));

    await remove(host, 'i2');
    await until(() => host.querySelectorAll('.tk-item').length === 2);
    expect(calls.at(-1)).toEqual({ path: `/api/tasks/${uid}/item`, method: 'DELETE', body: { item: 'i2' } });
    expect(document.activeElement).toBe(host.querySelector('[data-step="i3"]'));

    await remove(host, 'i3');
    await until(() => host.querySelectorAll('.tk-item').length === 1);
    expect(document.activeElement).toBe(host.querySelector('[data-step="i1"]'));
  });

  it('is offered for the only item as a disabled entry that gives its reason', () => {
    const { host } = mount(taskOf([item('i1', 'Solder the header')]));
    menuOf(host, 'i1').click();
    const del = entry(en['task.item.delete']);
    expect(del.getAttribute('aria-disabled')).toBe('true');
    expect(del.textContent).toContain(en['task.item.deleteLast']);
  });

  it('lists the other states above a separator, then the delete entry', () => {
    const { host } = mount(taskOf([item('i1', 'Solder the header', 'doing'), item('i2', 'Flash the board')]));
    menuOf(host, 'i1').click();
    const menu = document.querySelector('.ctx-menu');
    const parts = [...menu.children].map(el => (el.classList.contains('ctx-sep') ? '---' : el.textContent.trim()));
    expect(parts).toEqual([en['task.mark.todo'], en['task.mark.done'], en['task.mark.dropped'],
                           '---', en['task.item.delete']]);
  });
});

describe('a comment', () => {
  it("names its writer with a mark and a word, a person's and an agent's apart", () => {
    const comments = [
      { item: '', author: 'person', body: 'Check the pinout first', created_at: '2026-01-02T10:00:00Z', session: '' },
      { item: '', author: 'agent', body: 'Pinout matches rev B', created_at: '2026-01-02T11:00:00Z', session: 's-001' },
    ];
    const { host } = mount(taskOf([item('i1', 'Solder the header')], { comments }));
    const [person, agent] = host.querySelectorAll('.tk-thread article.tk-c');
    expect(person.classList.contains('is-person')).toBe(true);
    expect(person.querySelector('.tk-c-who').textContent).toBe(en['task.author.person']);
    expect(person.querySelector('.tk-c-av svg')).not.toBeNull();
    expect(agent.classList.contains('is-agent')).toBe(true);
    expect(agent.querySelector('.tk-c-who').textContent).toBe(en['task.author.agent']);
    expect(agent.querySelector('.tk-c-av').innerHTML).not.toBe(person.querySelector('.tk-c-av').innerHTML);
  });
});

describe('a write', () => {
  it('reports the accepted answer through onWrite', async () => {
    const answer = { task: taskOf([item('i1', 'Solder the header', 'doing')]), status: 'active' };
    serveApi(() => answer);
    const onWrite = vi.fn();
    const { host, uid } = mount(taskOf([item('i1', 'Solder the header')]), { onWrite });
    host.querySelector('[data-step="i1"]').click();
    await until(() => onWrite.mock.calls.length);
    expect(onWrite).toHaveBeenCalledWith(answer);
    expect(calls[0]).toEqual({ path: `/api/tasks/${uid}/item`, method: 'POST', body: { item: 'i1', state: 'doing' } });
  });
});

describe('the in-progress arc', () => {
  it('starts each repaint at the phase of the lap admin.css declares', () => {
    const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', '..',
                                  'src', 'memai', 'webui', 'admin.css'), 'utf8');
    const lap = Number(css.match(/--spin: ([\d.]+)s/)[1]);
    vi.spyOn(performance, 'now').mockReturnValue((2 * lap + 0.25) * 1000);
    const { host } = mount(taskOf([item('i1', 'Solder the header', 'doing')]));
    const ring = host.querySelector('[data-step="i1"] .tk-ring');
    expect(ring.getAttribute('style')).toBe('--spin-at:-0.25s');
    vi.restoreAllMocks();
  });
});
