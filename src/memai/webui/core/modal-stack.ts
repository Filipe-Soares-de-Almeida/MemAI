/* The stack every dialog joins: only the innermost is live, the app bar and view are inert while
   any is open, Tab wraps inside the top one, and closing hands the caret back. */

import { tipHide } from './tip.ts';

interface Entry {
  scrim: HTMLElement;
  opener: HTMLElement | null;
  close: () => void;
}

const FOCUSABLE = ['a[href]', 'button:not([disabled])', 'input:not([disabled])',
                   'textarea:not([disabled])', 'select:not([disabled])',
                   'details > summary', '[tabindex]:not([tabindex="-1"])'].join(',');

const stack: Entry[] = [];
let trapWired = false;

const top = (): Entry | null => stack[stack.length - 1] || null;

export const modalOpen = (): boolean => stack.length > 0;

export const modalDepth = (): number => stack.length;

/* `inert`, not aria-hidden, because it also stops clicks. */
function inertBackground(on: boolean): void {
  for (const sel of ['.appbar', '.frame']) document.querySelector(sel)?.toggleAttribute('inert', on);
}

export function focusInto(dialog: HTMLElement): void {
  const first = dialog.querySelector<HTMLElement>('input, textarea, select, button');
  (first || dialog).focus();
}

function wireTrap(): void {
  if (trapWired) return;
  trapWired = true;
  /* recomputed per keypress, since a dialog can gain and lose controls while open */
  addEventListener('keydown', e => {
    const entry = top();
    if (e.key !== 'Tab' || !entry) return;
    const dialog = entry.scrim.querySelector<HTMLElement>('.modal');
    if (!dialog) return;
    const items = [...dialog.querySelectorAll<HTMLElement>(FOCUSABLE)]
      .filter(el => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    const inside = dialog.contains(document.activeElement);
    if (e.shiftKey && (!inside || document.activeElement === first)) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && (!inside || document.activeElement === last)) {
      e.preventDefault(); first.focus();
    }
  }, true);
}

/* Puts a dialog on top; `opener` is whatever held the caret before it opened. */
export function pushModal(scrim: HTMLElement, close: () => void, opener: Element | null): void {
  tipHide();
  if (!stack.length) inertBackground(true);
  stack.push({ scrim, close, opener: opener instanceof HTMLElement ? opener : null });
  wireTrap();
  const dialog = scrim.querySelector<HTMLElement>('.modal');
  if (dialog) focusInto(dialog);
}

/* Takes a dialog off the stack wherever it sits; the caret returns to its opener, or else into the
   dialog now on top. */
export function removeModal(scrim: HTMLElement): void {
  const at = stack.findIndex(e => e.scrim === scrim);
  if (at < 0) return;
  const wasTop = at === stack.length - 1;
  const [entry] = stack.splice(at, 1);
  const back = top();
  /* released before the focus call: an inert subtree cannot take focus */
  if (!back) inertBackground(false);
  if (!wasTop) return;
  if (entry.opener && document.contains(entry.opener)) entry.opener.focus();
  else if (back) {
    const dialog = back.scrim.querySelector<HTMLElement>('.modal');
    if (dialog) focusInto(dialog);
  }
}

/* Closes the top dialog only. */
export function closeModal(): void {
  top()?.close();
}
