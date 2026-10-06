/* What the domains view keeps across its own remounts -- walking to another level and every write
   redraw it -- so the archived toggle and the queued moves outlive both. */

import { ref } from 'vue';
import { domainLeaf } from '../../core/domains.ts';
import type { DomainEntry } from '../../api/types.ts';
import { moveCost, pathOf } from './tree.ts';

export interface Move { from: string; to: string; memories: number }

export const showArchived = ref(false);

/* Dropped but not written, in the order they were made, which is the order Apply runs them. */
export const queue = ref<Move[]>([]);

/* A drop re-queues a level that was already queued, at the back. */
export function enqueue(from: string, toParent: string, node: DomainEntry | undefined): void {
  queue.value = [...queue.value.filter(m => m.from !== from),
                 { from, to: pathOf(toParent, domainLeaf(from)), memories: moveCost(node) }];
}

/* A queue is about the tree as it stands: a move whose level is gone is dropped. */
export function pruneQueue(exists: (path: string) => boolean): void {
  if (queue.value.some(m => !exists(m.from))) queue.value = queue.value.filter(m => exists(m.from));
}
