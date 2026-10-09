import { afterEach, describe, expect, it } from 'vitest';
import { createApp, h, nextTick, ref } from 'vue';
import type { App } from 'vue';
import MentionMenu from '../../src/memai/webui/views/record/MentionMenu.vue';
import { findTrigger, insertToken, matchItems } from '../../src/memai/webui/views/record/mention.ts';

const item = (id: number, n: number, text: string, state = 'todo') =>
  ({ id, n, seq: n, text, state, updated_at: '', updated_session: '', links: [] as never[] });
const ITEMS = [item(41, 1, 'Solder the header'), item(42, 2, 'Flash the board', 'done')];

describe('the mention trigger', () => {
  it('opens on # or @ at the start of a word', () => {
    expect(findTrigger('after #fla', 10)).toEqual({ start: 6, query: 'fla' });
    expect(findTrigger('@', 1)).toEqual({ start: 0, query: '' });
    expect(findTrigger('mail a@b', 8)).toBeNull();
  });

  it('closes once a space follows the query', () => {
    expect(findTrigger('#fla now', 8)).toBeNull();
  });
});

describe('matching', () => {
  it('matches the shown number or words of the label', () => {
    expect(matchItems(ITEMS, '2').map(i => i.id)).toEqual([42]);
    expect(matchItems(ITEMS, 'head').map(i => i.id)).toEqual([41]);
    expect(matchItems(ITEMS, '').map(i => i.id)).toEqual([41, 42]);
  });

  it('leaves #90 as typed when no item matches', () => {
    expect(matchItems(ITEMS, '90')).toEqual([]);
  });
});

describe('inserting', () => {
  it('replaces the trigger and query with the token and keeps the caret after it', () => {
    const el = document.createElement('textarea');
    el.value = 'after #fla then';
    document.body.append(el);
    insertToken(el, 6, 10, 42);
    expect(el.value).toBe('after [[#42]] then');
    expect(el.selectionStart).toBe(13);
    el.remove();
  });
});

describe('the menu', () => {
  let apps: App[] = [];
  afterEach(() => { apps.forEach(app => app.unmount()); apps = []; });

  async function mount({ bare = false, onChose = (_: number) => {}, onKey = (_: KeyboardEvent) => {} } = {}) {
    const host = document.getElementById('view') as HTMLElement;
    const scope = ref<HTMLElement | null>(null);
    const text = ref('');
    const app = createApp({
      render: () => h('div', { ref: scope }, [
        h('textarea', {
          value: text.value, 'data-mention': bare ? 'bare' : '',
          onInput: (e: Event) => { text.value = (e.target as HTMLTextAreaElement).value; },
          onKeydown: (e: KeyboardEvent) => onKey(e),
        }),
        h(MentionMenu, { host: scope.value, items: ITEMS, onChose }),
      ]),
    });
    app.mount(host);
    apps.push(app);
    await nextTick();
    const box = host.querySelector('textarea') as HTMLTextAreaElement;
    return { box, text };
  }

  const type = async (box: HTMLTextAreaElement, value: string) => {
    box.focus();
    box.value = value;
    box.setSelectionRange(value.length, value.length);
    box.dispatchEvent(new Event('input', { bubbles: true }));
    await nextTick();
  };
  const key = async (box: HTMLTextAreaElement, name: string, mods: KeyboardEventInit = {}) => {
    box.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true, ...mods }));
    await nextTick();
  };
  const options = () => [...document.querySelectorAll<HTMLElement>('[data-mention-menu] [role="option"]')];

  it('opens a listbox of the items on #, each with its number and label', async () => {
    const { box } = await mount();
    await type(box, 'after #');
    expect(options().map(o => o.textContent?.trim())).toEqual(['1 · Solder the header', '2 · Flash the board']);
    expect(box.getAttribute('aria-expanded')).toBe('true');
    expect(box.getAttribute('aria-activedescendant')).toBe(options()[0].id);
  });

  it('chooses the highlighted item with Tab, after the arrow keys move it', async () => {
    const { box, text } = await mount();
    await type(box, 'after #');
    await key(box, 'ArrowDown');
    expect(options()[1].getAttribute('aria-selected')).toBe('true');
    await key(box, 'Tab');
    expect(text.value).toBe('after [[#42]]');
    expect(options()).toHaveLength(0);
  });

  it('chooses with Enter once an arrow key has picked the item', async () => {
    const { box, text } = await mount();
    await type(box, '@fla');
    await key(box, 'ArrowDown');
    await key(box, 'ArrowUp');
    await key(box, 'Enter');
    expect(text.value).toBe('[[#42]]');
  });

  it('lets Enter through, the text as typed, when nothing was picked by arrow or pointer', async () => {
    const seen: string[] = [];
    const { box, text } = await mount({ onKey: e => seen.push(e.key) });
    await type(box, 'Issue #2');
    await key(box, 'Enter');
    expect(text.value).toBe('Issue #2');
    expect(seen).toEqual(['Enter']);
    expect(options()).toHaveLength(0);
  });

  it('closes on Ctrl+Enter and lets the editor save the text as typed', async () => {
    const seen: string[] = [];
    const { box, text } = await mount({ onKey: e => seen.push(`${e.ctrlKey ? 'Ctrl+' : ''}${e.key}`) });
    await type(box, 'Merged PR #2');
    await key(box, 'ArrowDown');
    await key(box, 'Enter', { ctrlKey: true });
    expect(text.value).toBe('Merged PR #2');
    expect(seen).toEqual(['Ctrl+Enter']);
    expect(options()).toHaveLength(0);
  });

  it('lets Shift+Tab move focus back without choosing', async () => {
    const seen: string[] = [];
    const { box, text } = await mount({ onKey: e => seen.push(e.key) });
    await type(box, 'after #');
    await key(box, 'Tab', { shiftKey: true });
    expect(text.value).toBe('after #');
    expect(seen).toEqual(['Tab']);
  });

  it('chooses with a click', async () => {
    const { box, text } = await mount();
    await type(box, 'see #');
    options()[0].dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
    options()[0].click();
    await nextTick();
    expect(text.value).toBe('see [[#41]]');
  });

  it('closes on Escape, leaving the text as typed and the editor open', async () => {
    let escaped = 0;
    const { box, text } = await mount({ onKey: e => { if (e.key === 'Escape') escaped += 1; } });
    await type(box, 'after #fla');
    await key(box, 'Escape');
    expect(options()).toHaveLength(0);
    expect(text.value).toBe('after #fla');
    expect(escaped).toBe(0);
    await key(box, 'Escape');
    expect(escaped).toBe(1);
  });

  it('leaves #90 as typed when no item matches', async () => {
    const { box, text } = await mount();
    await type(box, 'PR #9');
    expect(options()).toHaveLength(0);
    await type(box, 'PR #90');
    await key(box, 'Enter');
    expect(text.value).toBe('PR #90');
  });

  it('in a bare field, offers the items for any text and hands the choice back', async () => {
    const chose: number[] = [];
    const { box, text } = await mount({ bare: true, onChose: (id: number) => { chose.push(id); } });
    await type(box, 'board');
    expect(options().map(o => o.textContent?.trim())).toEqual(['2 · Flash the board']);
    await key(box, 'Enter');
    expect(chose).toEqual([42]);
    expect(text.value).toBe('');
  });

  it('lets Tab leave a bare field nothing was typed in, choosing nothing', async () => {
    const chose: number[] = [];
    const seen: string[] = [];
    const { box } = await mount({ bare: true, onChose: (id: number) => { chose.push(id); }, onKey: e => seen.push(e.key) });
    box.focus();
    box.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
    await nextTick();
    expect(options()).toHaveLength(2);
    await key(box, 'Tab');
    expect(chose).toEqual([]);
    expect(seen).toEqual(['Tab']);
  });
});
