/* The diagram list's filtering and ordering, over the whole set the server sends. */

import type { DiagramIssue, DiagramRow } from '../../api/types.ts';

const ISSUE_ORDER = ['empty', 'no_start', 'many_starts', 'unreachable', 'dead_end', 'no_end'];

/* Worst first, so a row and the inspector name the faults in the same order. */
export const sortIssues = (d: DiagramRow): DiagramIssue[] => d.issues.slice().sort(
  (a, b) => ISSUE_ORDER.indexOf(a.kind) - ISSUE_ORDER.indexOf(b.kind));

export function filterDiagrams(items: DiagramRow[], query: string): DiagramRow[] {
  const q = query.trim().toLowerCase();
  if (!q) return items;
  return items.filter(d => `${d.title} ${d.summary} ${d.domain} ${(d.also || []).join(' ')} ${d.tags}`
    .toLowerCase().includes(q));
}
