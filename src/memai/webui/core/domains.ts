/* Domain paths: 'acme/x100/p200' is a routine in a module in a product. The server stores and
   matches them; these read their shape, and never fold case -- 'Acme' and 'acme' are two domains. */

import { MEMORY } from '../contract.ts';

export const DOMAIN_SEP: string = MEMORY.DOMAIN_SEP;

export const domainSegments = (d: string | null | undefined): string[] =>
  (d || '').split(DOMAIN_SEP).filter(Boolean);
export const domainLeaf = (d: string | null | undefined): string => domainSegments(d).slice(-1)[0] || '';
export const domainDepth = (d: string | null | undefined): number => domainSegments(d).length;

/* Whether a path is a scope or under it, segment-wise like the server: 'acme/x1000' is not
   inside 'acme/x100'. An empty scope holds everything. */
export function inDomainPath(domain: string | null | undefined, scope: string | null | undefined): boolean {
  const want = domainSegments(scope), segs = domainSegments(domain);
  return want.every((s, i) => segs[i] === s);
}

/* Tree order: parent, its subtree, then the next sibling. By segments then depth, since '-'
   sorts before '/' and would split a subtree. */
export function byDomainPath(a: { domain: string }, b: { domain: string }): number {
  const x = domainSegments(a.domain), y = domainSegments(b.domain);
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const c = x[i].localeCompare(y[i]);
    if (c) return c;
  }
  return x.length - y.length;
}

export interface DomainGuide { depth: number; through: boolean[]; last: boolean }

/* The tree's shape per row: which ancestor rails continue and whether the row closes its branch.
   `list` must be in tree order (byDomainPath); shared so every renderer draws the same tree. */
export function domainGuides(list: Array<{ domain: string }>): DomainGuide[] {
  const out: DomainGuide[] = [];
  /* cont[k - 1]: the row last seen at depth k has a sibling still to come, so the column that
     branch owns keeps its line through the rows between */
  const cont: boolean[] = [];
  list.forEach((d, i) => {
    const depth = domainDepth(d.domain);
    let last = true;
    for (let j = i + 1; j < list.length; j++) {
      const next = domainDepth(list[j].domain);
      if (next > depth) continue;
      last = next < depth;
      break;
    }
    /* one flag per pass-through column; the connector column is this row's own `last`, and a
       root has neither */
    out.push({ depth, through: cont.slice(1, depth - 1), last });
    cont[depth - 1] = !last;
  });
  return out;
}
