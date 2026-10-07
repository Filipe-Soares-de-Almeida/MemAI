/* Where the diagram editor's jumps lead, as addresses, and which of them the corner of the canvas
   offers for the step selected. */

import { t } from '../../i18n.ts';
import type { DiagramJump, DiagramRecord } from '../../api/types.ts';

const enc = encodeURIComponent;

/* A real href, so ctrl-click and the middle button open the destination in another tab. `node` is
   what the arriving view lands on; `from`/`fromNode` are the step left, which lets it offer the way back. */
export const jumpHref = (j: DiagramJump, from: string): string =>
  `#/diagram?uid=${enc(j.peer_uid)}`
  + (j.peer_node ? `&node=${enc(j.peer_node)}` : '')
  + `&from=${enc(from)}`
  + (j.node_key ? `&fromNode=${enc(j.node_key)}` : '');

/* A return carries no `from`, or the two flows would each offer the way to the other forever. */
export const backHref = (uid: string, node: string): string =>
  `#/diagram?uid=${enc(uid)}` + (node ? `&node=${enc(node)}` : '');

/* Three chips and the +N button fit one row of the stage once the inspector has its share. */
export const JUMP_CHIPS_SHOWN = 3;
const CHIP_LABEL_CHARS = 20;

/* A flow's name cut to what a chip carries: the part before the first dash-like separator, where a
   title keeps its identifier, clamped; the whole title stays in the tooltip. */
export function chipLabel(title: unknown): string {
  const full = String(title ?? '').trim();
  const head = full.split(/\s+[—–-]\s+/)[0].trim() || full;
  if (head.length <= CHIP_LABEL_CHARS) return head;
  return `${head.slice(0, CHIP_LABEL_CHARS).replace(/[\s,;:.\-/]+$/, '')}…`;
}

export interface NavOffer { href: string; kind: 'back' | 'go'; title: string; step: string; hint: string }

export interface Arrival { from: string; fromNode: string }

/* The way back to the flow that sent the reader here, then the selected step's jumps, then those
   aimed at the whole diagram; the arrow is what the click does, the tooltip which end holds the tie. */
export function navOffers(data: DiagramRecord, uid: string, selected: string | null, came: Arrival): NavOffer[] {
  const node = data.nodes.find(n => n.key === selected);
  /* matched on the step too when the address names one: two flows can hand off to the same step */
  const back = came.from ? data.jumps.find(j => j.peer_uid === came.from
    && (!came.fromNode || j.peer_node === came.fromNode)) : undefined;
  const mine = (node ? data.jumps.filter(j => j.node_key === node.key) : []).filter(j => j !== back);
  const whole = data.jumps.filter(j => !j.node_key && j !== back);
  return [
    ...(back ? [{ href: backHref(back.peer_uid, back.peer_node), kind: 'back' as const, title: back.peer_title,
                  step: back.peer_node, hint: t('dg.jump.backTitle', { title: back.peer_title }) }] : []),
    ...mine.map(j => ({ href: jumpHref(j, uid), kind: 'go' as const, title: j.peer_title, step: j.peer_node,
                        hint: t(j.direction === 'out' ? 'dg.jump.open' : 'dg.jump.openFrom', { title: j.peer_title }) })),
    ...whole.map(j => ({ href: jumpHref(j, uid), kind: 'go' as const, title: j.peer_title, step: '',
                         hint: t('dg.jump.openHere', { title: j.peer_title }) })),
  ];
}
