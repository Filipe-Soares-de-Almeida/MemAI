/* Where a mention starts under the caret, which items it can name, and how its token goes in. */
import type { TaskItem } from '../../api/types.ts';

const TRIGGER = /(^|\s)#([^\s#]*)$/;

export function findTrigger(text: string, caret: number): { start: number; query: string } | null {
  const found = TRIGGER.exec(text.slice(0, caret));
  if (!found) return null;
  return { start: caret - found[2].length - 1, query: found[2] };
}

/* a number matches the shown numbers it starts, anything else the labels it appears in */
export function matchItems<T extends Pick<TaskItem, 'n' | 'text'>>(items: T[], query: string): T[] {
  const q = query.trim().toLowerCase();
  if (!q) return items;
  if (/^[0-9]+$/.test(q)) return items.filter(i => String(i.n).startsWith(q));
  return items.filter(i => i.text.toLowerCase().includes(q));
}

/* execCommand keeps the field's own undo; setRangeText is the fallback where it is missing */
export function insertToken(el: HTMLTextAreaElement | HTMLInputElement, start: number, end: number, id: number): void {
  const token = `[[#${id}]]`;
  el.focus();
  el.setSelectionRange(start, end);
  const done = typeof document.execCommand === 'function' && document.execCommand('insertText', false, token);
  if (!done || el.value.slice(start, start + token.length) !== token) el.setRangeText(token, start, end);
  el.setSelectionRange(start + token.length, start + token.length);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}
