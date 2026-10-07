/* What the optimization view reads off a staged suggestion and a run's groups: names, counts, the
   shape its evidence is drawn in, and how a batch result is reported. */

import { previousRoute } from '../../core/router.ts';
import { CONF, kindLabel, relLabel } from '../../core/shared.js';
import { toast } from '../../core/ui.js';
import { CONTENT_KINDS, FLAG_KINDS, LINE_KINDS, SET_KINDS, TEXT_KINDS, WHAT_MIXED } from '../../core/suggestion-kinds.js';
import { t } from '../../i18n.ts';
import type { I18nKey } from '../../i18n.ts';
import type { AppliedAll, KindGroup, Suggestion } from '../../api/types.ts';

export type Shape = 'distill' | 'rel' | 'text' | 'prose' | 'flag' | 'set' | 'line' | 'raw';

/* one pane per shape of change, not per kind */
export function shapeOf(kind: string): Shape {
  if (kind === 'distill') return 'distill';
  if (kind === 'link' || kind === 'merge') return 'rel';
  if (TEXT_KINDS.has(kind)) return 'text';
  if (CONTENT_KINDS.has(kind)) return 'prose';
  if (FLAG_KINDS.has(kind)) return 'flag';
  if (SET_KINDS.has(kind)) return 'set';
  if (LINE_KINDS.has(kind)) return 'line';
  return 'raw';
}

export const leakField = (s: Suggestion): string => String(s.payload.field || 'content');

/* the memory's own snippet is shown under review unless the pane already draws that memory */
export const showsPreview = (s: Suggestion): boolean => {
  const shape = shapeOf(s.kind);
  return Boolean(s.target) && shape !== 'rel' && shape !== 'distill' && shape !== 'prose'
    && !(shape === 'text' && leakField(s) === 'content');
};

const SET_SPLIT = /\s*,\s*/;
export const setOf = (v: unknown): string[] => (Array.isArray(v) ? v : String(v ?? '').split(SET_SPLIT))
  .map(x => String(x).trim()).filter(Boolean);

/* the set before and after, in full, so what stayed shows as well as what moved */
export function setPair(s: Suggestion) {
  const tg = s.target, p = s.payload;
  const [was, now] = s.kind === 'retag' ? [setOf(tg?.tags), setOf(p.tags)] : [setOf(tg?.also), setOf(p.also)];
  return { was, now, gone: was.filter(x => !now.includes(x)), born: now.filter(x => !was.includes(x)) };
}

export function linePair(s: Suggestion): [string, string] {
  const tg = s.target, p = s.payload;
  if (s.kind === 'retitle') return [tg?.title || '', String(p.title ?? '')];
  if (s.kind === 'redomain') return [tg?.domain || '', String(p.domain ?? '')];
  return [tg?.review_after || '', String(p.review_after ?? '')];
}

/* The name a suggestion goes by in the list: the memory's title or opening, else the uids it ties. */
export function rowName(s: Suggestion): string {
  const tg = s.target;
  if (tg?.title) return tg.title;
  if (tg?.snippet) return tg.snippet;
  const p = s.payload;
  if (s.kind === 'link') return `${p.from_uid || '?'} → ${p.to_uid || '?'}`;
  if (s.kind === 'merge') return `${p.keep_uid || '?'} ← ${p.drop_uid || '?'}`;
  if (s.kind === 'distill') return String(p.title || t('op.distill.new'));
  return s.kind;
}

/* How much text a still-pending rewrite drops; once applied, chars_before is the new body. */
export const rewriteShare = (s: Suggestion): string => {
  if (s.status !== 'pending' || !s.chars_before) return '';
  const pct = Math.round(((s.chars_after ?? 0) - s.chars_before) * 100 / s.chars_before);
  return `${pct > 0 ? '+' : ''}${pct}%`;
};

/* amber while something unchecked is pending, green when every pending one is checked, quiet after */
export function verifiedState(verified: number, pending: number): 'all' | 'some' | 'done' {
  if (!pending) return 'done';
  return verified === pending ? 'all' : 'some';
}

export const VERIFIED_FILL = { all: 'var(--ok)', some: 'var(--warn)', done: 'var(--ink-3)' };

/* a value set mid-sentence loses the capital its vocabulary writes it with */
const midSentence = (v: string): string => (v ? v.charAt(0).toLocaleLowerCase() + v.slice(1) : v);

const WHAT_MASK: Record<string, (v: string) => string> = {
  conf: c => midSentence((CONF[c] || {}).label || c),
  rel: v => midSentence(relLabel(v)),
};

/* A group's sentence, worded from the facts the server counted; an empty fact picks `<kind>Mixed`. */
export function groupWhat(g: KindGroup): string {
  const facts = g.facts || {};
  const field = (WHAT_MIXED as Record<string, string>)[g.kind];
  const key = `op.what.${g.kind}${field && !facts[field] ? 'Mixed' : ''}` as I18nKey;
  const shown = Object.fromEntries(Object.entries(facts).map(
    ([k, v]) => [k, WHAT_MASK[k] && v ? WHAT_MASK[k](String(v)) : v]));
  const line = t(key, { n: g.pending || g.total, ...shown });
  return line === key ? t('op.what.other', { n: g.pending || g.total, kind: kindLabel(g.kind) }) : line;
}

/* A partly failed batch is a warning that names each failure, not a red total. */
export function reportApplied(res: AppliedAll): void {
  const bad = res.failed || [];
  const saved = res.backups && res.backups.length ? ` · ${t('op.toast.backupsN', { n: res.backups.length })}` : '';
  if (!bad.length) { toast(t('op.toast.appliedN', { n: res.applied }) + saved, 'ok'); return; }
  toast(t('op.toast.appliedN', { n: res.applied }) + saved + t('op.toast.failedN', { m: bad.length }),
        'warn', { detail: bad.map(f => `#${f.id}: ${f.error}`).join(' · ') });
}

/* Up one level, back to the same page when that is where the reader came from; every level is
   'optimization', so the view name alone cannot tell. */
export function upTo(params: Record<string, string | number> = {}): void {
  const qs = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString();
  const target = `#/optimization${qs ? '?' + qs : ''}`;
  if (previousRoute().hash === target) history.back();
  else location.hash = target;
}
