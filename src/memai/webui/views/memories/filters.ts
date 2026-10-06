/* The list's filters as the URL carries them, and the API query and route each one becomes. */

import type { Params } from '../../core/router.ts';

export const PAGE = 50;

/* Handed over by a Health symptom (admin._defect_clauses) and taken off by their chips; never
   offered as a filter of their own. */
export const DEFECTS = ['linked', 'due', 'stale', 'untitled', 'untagged'] as const;

export interface Filters {
  q: string;
  domain: string;
  type: string;
  status: string;
  confidence: string;
  /* '' any memory, 'any' any pin, or one kind of pin */
  pin: string;
  /* open | completed | cancelled, meaningful only for the task type */
  task_state: string;
  session: string;
  linked: string;
  due: string;
  stale: string;
  untitled: string;
  untagged: string;
  sort: string;
  dir: string;
  page: number;
}

export function readFilters(params: URLSearchParams): Filters {
  const get = (key: string) => params.get(key) || '';
  return {
    q: get('q'), domain: get('domain'), type: get('type'),
    status: params.has('status') ? get('status') : 'active',
    confidence: get('confidence'), pin: get('pin'), task_state: get('task_state'), session: get('session'),
    linked: get('linked'), due: get('due'), stale: get('stale'), untitled: get('untitled'),
    untagged: get('untagged'),
    sort: get('sort') || 'created_at', dir: get('dir') || 'desc',
    page: parseInt(get('page') || '0', 10) || 0,
  };
}

/* What the API is asked for, paging aside. */
export function listFilter(f: Filters): Record<string, string> {
  return {
    q: f.q, domain: f.domain, type: f.type, status: f.status, confidence: f.confidence, pin: f.pin,
    session: f.session, sort: f.sort, dir: f.dir,
    task_state: f.type === 'task' ? f.task_state : '',
    linked: f.linked, due: f.due, stale: f.stale, untitled: f.untitled, untagged: f.untagged,
  };
}

/* The route for `f` changed by `patch`. Status active and page 0 are implied; "all" statuses is
   an explicit empty status, since leaving it out means active. */
export function routeParams(f: Filters, patch: Partial<Filters>): Params {
  const p = { ...f, ...patch };
  const out: Params = {};
  for (const [key, value] of Object.entries(p)) if (value !== '' && value != null) out[key] = String(value);
  delete out.page;
  if (p.page) out.page = String(p.page);
  if (p.status === 'active') delete out.status;
  else out.status = p.status || '';
  return out;
}

/* How many of the folded row's filters are in use; any at all opens it. */
export const moreCount = (f: Filters): number =>
  (f.pin ? 1 : 0) + (f.sort !== 'created_at' || f.dir !== 'desc' ? 1 : 0)
  + DEFECTS.filter(key => f[key]).length + (f.session ? 1 : 0);

/* The orders offered, as sort:dir. Least recalled leads with the never-recalled rows, where a
   curation pass starts. */
export const SORTS = ['created_at:desc', 'created_at:asc', 'updated_at:desc', 'recalls:desc', 'recalls:asc'] as const;

/* A sort and dir from the URL can name a pair no option offers; the first one stands in. */
export const activeSort = (f: Filters): string => {
  const pair = `${f.sort}:${f.dir}`;
  return (SORTS as readonly string[]).includes(pair) ? pair : SORTS[0];
};
