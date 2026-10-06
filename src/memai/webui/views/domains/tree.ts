/* The domain tree as the columns read it: which levels each column lists, what a level's status
   is, and which moves are legal. */

import { DOMAIN_SEP, byDomainPath, domainLeaf, domainSegments, inDomainPath } from '../../core/domains.ts';
import type { DomainEntry } from '../../api/types.ts';

export const pathOf = (parent: string, leaf: string): string => (parent ? `${parent}${DOMAIN_SEP}${leaf}` : leaf);

/* A branch nothing names but its own archived memories: an active memory below it or a
   cross-listing into it keeps it live. A domain's status is the status of what names it. */
export const isArchived = (d: DomainEntry): boolean =>
  Boolean(d.subtree_archived) && !d.subtree_active && !d.subtree_also;

/* A purely cross-cutting subject: nothing is filed under it, and memories in other branches point
   at it. */
export const isCrossing = (d: DomainEntry): boolean =>
  Boolean(d.subtree_also) && !d.subtree_active && !d.subtree_archived;

export const childrenOf = (domains: DomainEntry[], parent: string, showArchived: boolean): DomainEntry[] =>
  domains.filter(d => (d.parent || '') === parent)
    .filter(d => showArchived || !isArchived(d))
    .sort(byDomainPath);

export interface Column { parent: string; kids: DomainEntry[]; picked: string; depth: number }

/* One column per level of the path, plus one for the children of the level picked, so the column
   walked into next is already open; a leaf opens no empty column. */
export function columnsFor(domains: DomainEntry[], path: string, showArchived: boolean): Column[] {
  const segs = domainSegments(path);
  const parents = ['', ...segs.map((_, i) => segs.slice(0, i + 1).join(DOMAIN_SEP))];
  return parents.flatMap((parent, depth) => {
    const kids = childrenOf(domains, parent, showArchived);
    if (depth && !kids.length) return [];
    return [{ parent, kids, picked: segs[depth] ? pathOf(parent, segs[depth]) : '', depth }];
  });
}

/* Every prefix of the path, as the bar above the columns links to them. */
export const crumbs = (path: string): Array<{ seg: string; upto: string }> => {
  const segs = domainSegments(path);
  return segs.map((seg, i) => ({ seg, upto: segs.slice(0, i + 1).join(DOMAIN_SEP) }));
};

/* The moves the server refuses: into itself or its own subtree, or to where it already lives. */
export function canMove(from: string, to: string): boolean {
  if (to === from || inDomainPath(to, from)) return false;
  return pathOf(to, domainLeaf(from)) !== from;
}

/* What a move costs: the whole subtree is reindexed, and the cross-listings into it follow it. */
export const moveCost = (d: DomainEntry | undefined): number =>
  (d?.subtree_active || 0) + (d?.subtree_archived || 0) + (d?.subtree_also || 0);
