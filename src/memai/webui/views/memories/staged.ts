/* The inspector's staged edits: chosen in the pane, written together by Apply. */

import type { MemoryRow } from '../../api/types.ts';

export interface Staged { confidence: string; tags: string[]; domain: string }

export const emptyStaged = (): Staged => ({ confidence: '', tags: [], domain: '' });

/* The rows each staged edit actually changes. */
export const confChanges = (picked: MemoryRow[], confidence: string): number =>
  picked.filter(m => m.confidence !== confidence).length;

export const domainChanges = (picked: MemoryRow[], domain: string): number =>
  picked.filter(m => m.domain !== domain).length;

/* How many writes the staged edits amount to; zero keeps Apply disabled rather than run no-ops. */
export function stagedCount(picked: MemoryRow[], staged: Staged): number {
  let n = 0;
  if (staged.confidence) n += confChanges(picked, staged.confidence);
  if (staged.tags.length) n += picked.length;
  if (staged.domain) n += domainChanges(picked, staged.domain);
  return n;
}

/* One /api/bulk body per staged edit, in the order they must run: a re-home re-applies the
   cross-listing policy, so it has to see the row after the other edits. */
export function bulkBodies(staged: Staged): Array<{ action: string; value: string }> {
  const out = [];
  if (staged.confidence) out.push({ action: 'confidence', value: staged.confidence });
  if (staged.tags.length) out.push({ action: 'tag', value: staged.tags.join(', ') });
  if (staged.domain) out.push({ action: 'rehome', value: staged.domain });
  return out;
}

/* A tag typed into the box: trailing commas off, and nothing when it is empty or already staged. */
export function stagedTag(text: string, staged: Staged): string {
  const tag = text.trim().replace(/,+$/, '');
  return tag && !staged.tags.includes(tag) ? tag : '';
}
