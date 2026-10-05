import { describe, expect, it, vi } from 'vitest';
import { openDropMenu, openCtxMenu, closeCtxMenu } from '../../src/memai/webui/core/ui.js';

const key = name => document.activeElement.dispatchEvent(
  new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true }));

function dropFrom(items) {
  const btn = document.createElement('button');
  btn.textContent = 'open';
  document.body.appendChild(btn);
  btn.focus();
  openDropMenu(btn, items);
  return btn;
}

const labels = () => [...document.querySelectorAll('.ctx-item')].map(b => b.textContent.trim());

describe('a menu dropped from a button', () => {
  it('takes the keyboard: focus lands on the first entry and the arrows walk and wrap', () => {
    dropFrom([{ label: 'Alpha' }, { label: 'Beta' }, { label: 'Gamma' }]);
    expect(document.activeElement.textContent).toBe('Alpha');
    key('ArrowDown');
    expect(document.activeElement.textContent).toBe('Beta');
    key('End');
    expect(document.activeElement.textContent).toBe('Gamma');
    key('ArrowDown');
    expect(document.activeElement.textContent).toBe('Alpha');
    key('ArrowUp');
    expect(document.activeElement.textContent).toBe('Gamma');
    key('Home');
    expect(document.activeElement.textContent).toBe('Alpha');
  });

  it('hands focus back to its button on Escape and on Tab', () => {
    for (const exit of ['Escape', 'Tab']) {
      const btn = dropFrom([{ label: 'Alpha' }]);
      key(exit);
      expect(document.querySelector('.ctx-menu')).toBeNull();
      expect(document.activeElement).toBe(btn);
      btn.remove();
    }
  });

  it('restores focus before the chosen entry runs', () => {
    let focusedWhenRun = null;
    const btn = dropFrom([{ label: 'Alpha', run: () => { focusedWhenRun = document.activeElement; } }]);
    document.activeElement.click();
    expect(document.querySelector('.ctx-menu')).toBeNull();
    expect(focusedWhenRun).toBe(btn);
  });

  it('shows an entry with a note as disabled, described by its note, and never runs it', () => {
    const run = vi.fn();
    dropFrom([{ label: 'Alpha' }, { sep: true }, { label: 'Remove', note: 'Needs a second row', run }]);
    expect(document.querySelectorAll('.ctx-sep')).toHaveLength(1);
    const blocked = [...document.querySelectorAll('.ctx-item')].at(-1);
    expect(blocked.getAttribute('aria-disabled')).toBe('true');
    const note = document.getElementById(blocked.getAttribute('aria-describedby'));
    expect(note.textContent).toBe('Needs a second row');
    blocked.click();
    expect(run).not.toHaveBeenCalled();
    expect(document.querySelector('.ctx-menu')).not.toBeNull();
    closeCtxMenu();
  });
});

describe('a menu opened at a point', () => {
  it('does not take focus, and leaves out empty entries', () => {
    const before = document.activeElement;
    openCtxMenu(10, 10, [null, { label: 'Alpha' }, false]);
    expect(labels()).toEqual(['Alpha']);
    expect(document.activeElement).toBe(before);
    closeCtxMenu();
  });
});
