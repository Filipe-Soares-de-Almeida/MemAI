/* The relations graph's per-browser settings, and the address its filters and arrangement write. */

import { ARRANGEMENTS, DEFAULT_MODE } from '../../engines/graph-arrange.ts';
import type { Show } from '../../engines/graph-arrange.ts';

const PREFS = 'memai.graph';

export interface GraphPrefs { mode?: string; links?: boolean; domains?: boolean; names?: boolean; titles?: boolean }

export function readPrefs(): GraphPrefs {
  try { return JSON.parse(localStorage.getItem(PREFS) || '{}') || {}; } catch { return {}; }
}

export function writePrefs(patch: GraphPrefs): void {
  try { localStorage.setItem(PREFS, JSON.stringify({ ...readPrefs(), ...patch })); } catch { /* storage may be blocked */ }
}

export const MODES = ARRANGEMENTS.map(a => a.id);

/* a `mode` in the address wins over the stored one */
export function initialMode(asked: string | null, prefs: GraphPrefs): string {
  if (asked && MODES.includes(asked)) return asked;
  return prefs.mode && MODES.includes(prefs.mode) ? prefs.mode : DEFAULT_MODE;
}

/* Names are on and relations off unless this browser said otherwise. In a record that has `titles`,
   that key covers both kinds of name and `links` is ignored, being set against the opposite default. */
export function initialShow(prefs: GraphPrefs): Show {
  const legacy = prefs.titles !== undefined;
  return {
    links: !legacy && prefs.links === true,
    domains: prefs.domains ?? prefs.titles ?? true,
    names: prefs.names ?? prefs.titles ?? true,
  };
}

export interface GraphFilters { status: string; domain: string; type: string; mode: string }

/* the route params for a set of filters, each default left out */
export function graphParams(p: GraphFilters): Record<string, string> {
  const out: Record<string, string> = {};
  if (p.status !== 'active') out.status = p.status;
  if (p.domain) out.domain = p.domain;
  if (p.type) out.type = p.type;
  if (p.mode !== DEFAULT_MODE) out.mode = p.mode;
  return out;
}

export const clip = (text: unknown, max: number): string => {
  const s = String(text || '');
  return s.length > max ? `${s.slice(0, max - 1).trimEnd()}…` : s;
};
