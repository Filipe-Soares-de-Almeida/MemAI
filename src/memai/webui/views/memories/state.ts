/* The list's working state, owned by the view and shared by its panes: the rows loaded, what is
   ticked, where the caret sits, and the edits staged in the inspector. */

import { computed, reactive, ref, shallowReactive } from 'vue';
import type { MemoryRow } from '../../api/types.ts';
import { emptyStaged } from './staged.ts';
import type { Staged } from './staged.ts';

export function useMemoriesState(items: MemoryRow[], total: number, matchQuery: string) {
  /* every row the selection can name: the page, plus what "select all matching" loads */
  const rows = shallowReactive(new Map(items.map(m => [m.uid, m])));
  const pageUids = items.map(m => m.uid);
  const selection = reactive(new Set<string>());
  /* the uids "select all matching" loaded, so the banner can tell that whole set apart */
  const matching = shallowReactive(new Set<string>());
  const caretUid = ref('');
  const staged = reactive<Staged>(emptyStaged());

  const ticked = computed(() =>
    [...selection].map(uid => rows.get(uid)).filter((m): m is MemoryRow => Boolean(m)));
  /* Ticks build a batch; with none, the caret's row is the one inspected. */
  const picked = computed<MemoryRow[]>(() => {
    if (ticked.value.length) return ticked.value;
    const caret = rows.get(caretUid.value);
    return caret ? [caret] : [];
  });

  const clearStaged = () => Object.assign(staged, emptyStaged());

  return { rows, pageUids, total, matchQuery, selection, matching, caretUid, staged, ticked, picked,
           clearStaged };
}

export type MemoriesState = ReturnType<typeof useMemoriesState>;
