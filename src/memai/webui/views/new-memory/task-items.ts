/* A new task's checklist as the server reads it: one item per non-blank line. */

import { TASK } from '../../contract.ts';

export const itemLines = (text: string): string[] =>
  text.split(/\r?\n/).map(line => line.trim()).filter(Boolean);

/* Shown as a warning, never enforced: the server refuses the same list either way. */
export const itemsOver = (lines: string[]): boolean =>
  lines.length > TASK.ITEMS_MAX || lines.some(line => line.length > TASK.ITEM_MAX);
