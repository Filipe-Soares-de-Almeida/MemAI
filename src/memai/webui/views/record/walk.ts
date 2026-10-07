/* The walk through records, kept across the remount every step causes: the trail of records followed,
   the view the walk started from, and where the stepper stands in the list that opened it. */

import { go, previousRoute } from '../../core/router.ts';
import { openRecord, recordSequence } from '../../core/nav.ts';
import { t } from '../../i18n.ts';
import type { I18nKey } from '../../i18n.ts';

interface Stop { uid: string; label?: string }
interface Origin { name: string; hash: string }
export interface StepPos { at: number; prev: number; next: number; gone: boolean }

let trail: Stop[] = [];
let origin: Origin | null = null;
/* raised by step() for the one navigation it starts: a step across the list keeps the trail's depth */
let stepped = false;
/* where the shown record last sat in the list, so a write that drops its row does not end the walk */
let slot: number | null = null;
let anchored: string | null = null;

export function resetWalk(): void {
  trail = [];
  origin = null;
  stepped = false;
  slot = null;
  anchored = null;
}

/* A record missing from the list resumes at its old slot: the row that took it is the next one. */
export function stepPos(uid: string, seq: readonly string[] = recordSequence()): StepPos | null {
  if (anchored !== uid) { anchored = uid; slot = null; }
  const at = seq.indexOf(uid);
  if (at >= 0) { slot = at; return { at, prev: at - 1, next: at + 1, gone: false }; }
  if (slot === null || !seq.length) return null;
  const anchor = Math.min(slot, seq.length - 1);
  return { at: anchor, prev: anchor - 1, next: anchor, gone: true };
}

/* One step: the same record again, a step across the list, a step back down the trail, or a new one. */
export function walk(uid: string, from: { name: string; hash: string } = previousRoute()): void {
  const across = stepped;
  stepped = false;
  if (from.name && from.name !== 'memory') { trail = []; origin = from; }
  if (trail[trail.length - 1]?.uid === uid) return;
  if (across && trail.length) { trail[trail.length - 1] = { uid }; return; }
  if (trail[trail.length - 2]?.uid === uid) { trail.pop(); return; }
  trail.push({ uid });
}

/* Names the record on top of the trail, for the back button of the one followed from it. */
export function nameStop(uid: string, label: string): void {
  const here = trail[trail.length - 1];
  if (here?.uid === uid) here.label = label.slice(0, 60);
}

/* the one view whose nav entry is not named after it */
const NAV_LABEL: Record<string, I18nKey> = { diagram: 'nav.diagrams' };

export function backTarget(): { label: string; hash: string } {
  const under = trail[trail.length - 2];
  if (under) return { label: under.label || under.uid, hash: '' };
  if (origin?.name) return { label: t(NAV_LABEL[origin.name] || `nav.${origin.name}` as I18nKey), hash: origin.hash };
  return { label: t('nav.memories'), hash: '' };
}

export function goBack(): void {
  const under = trail[trail.length - 2];
  if (under) { trail.pop(); openRecord(under.uid); return; }
  /* the exact URL, so a list comes back on its page and filters */
  if (origin?.hash) { location.hash = origin.hash; return; }
  go('memories');
}

export function step(uid: string, delta: number, seq: readonly string[] = recordSequence()): void {
  const pos = stepPos(uid, seq);
  if (!pos) return;
  const to = delta < 0 ? pos.prev : pos.next;
  if (to < 0 || to >= seq.length) return;
  stepped = true;
  openRecord(seq[to]);
}
