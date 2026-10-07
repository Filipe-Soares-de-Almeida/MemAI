/* The overview's readings: confidence order and colour, the score bands, and the symptom ranking
   and destinations. */

import type { Symptom } from '../../api/types.ts';
import type { Params } from '../../core/router.ts';

export const CONF_ORDER = ['confirmed', 'unverified', 'contradicted'] as const;

/* Their meaning lives in db.health_axes; the row's title is the catalog's wording of it. */
export const AXES = ['curation', 'connectivity', 'freshness', 'organization'] as const;

export const confColor = (c: string): string =>
  c === 'confirmed' ? 'var(--ok)' : c === 'contradicted' ? 'var(--bad)' : 'var(--warn)';

/* One scale for the index and its axes: the index is their mean, and a second scale would make the
   figure disagree with the bars under it. */
export const band = (v: number): string =>
  v >= 75 ? 'var(--ok)' : v >= 50 ? 'var(--warn)' : 'var(--bad)';

const RANK: Record<string, number> = { bad: 0, warn: 1, info: 2 };

/* Worst first, then the larger set; a clean symptom sinks to the end instead of leaving, so the
   list keeps one shape whatever the store's condition. */
export const rankSymptoms = (symptoms: Symptom[]): Symptom[] =>
  symptoms.slice().sort((a, b) =>
    Boolean(a.count) === Boolean(b.count)
      ? (RANK[a.severity] - RANK[b.severity]) || (b.count - a.count)
      : (a.count ? -1 : 1));

/* Broken edges have no list of their own, so their button opens the repair and carries its own
   params. */
const OWN_ROUTE: Record<string, [string, Params?]> = {
  diagrams: ['diagrams'],
  orphans: ['maintenance', { tab: 'storage' }],
};

/* Every other symptom hands over the server's filter, so the list that opens is the set counted. */
export function symptomRoute(s: Symptom): [string, Params] {
  const [name, own] = OWN_ROUTE[s.key] ?? ['memories'];
  return [name, own ?? s.params ?? {}];
}
