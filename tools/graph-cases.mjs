/* Feed the relations graph's geometry a synthetic store and print what came
   back, for tests/test_graph_2d.py to hold it to.

   Only the parts that have no DOM are exercised: the circle packing, the
   force pass, the density field and the three arrangements. The engine around
   them (graph-2d.js) reaches for a canvas and a stylesheet, which is the line
   this stops at.

   Usage: node tools/graph-cases.mjs    ->    one JSON object on stdout */

import { packSiblings, enclose } from '../src/memai/webui/graph-geom.js';
import { Sim, buildQuad, repel } from '../src/memai/webui/graph-force.js';
import { density, isolines, smooth } from '../src/memai/webui/graph-field.js';
import { deriveStore, ARRANGEMENTS, arrangement, buildTree, topOf }
  from '../src/memai/webui/graph-arrange.js';

/* a fixed sequence, so a failure is the same failure twice */
const rng = (seed => () => {
  seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
  let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
})(20260911);

const hex = n => String(n).padStart(16, '0').replace(/0/g, '1').slice(-16);

/* ------------------------------------------------------------- the store */

/* Domains shaped the way a real tree is: a deep branch, a wide one, a root
   holding a single memory, and a leaf domain holding a single memory under a
   parent that holds more -- the one the hub arrangement has to collapse. */
const PLAN = [
  ['acme/x100/p200', 8],
  ['acme/x100/p300', 5],
  ['acme/x100/p900', 1],      /* one memory, no children: collapses */
  ['acme/x200', 4],
  ['zeta/x100/p200', 6],
  ['zeta/x300', 3],
  ['omni', 1],                /* a root with one memory: always a node */
];

const SUBJECTS = ['cache warmup', 'queue drain', 'token refresh', 'index rebuild',
                  'row merge', 'batch retry', 'file upload', 'report export'];
const TYPES = ['note', 'checkpoint', 'anti_pattern', 'reasoning', 'handoff', 'diagram'];

function store() {
  const nodes = [];
  let at = 0;
  for (const [domain, count] of PLAN) {
    for (let i = 0; i < count; i++) {
      at++;
      nodes.push({
        uid: hex(at),
        type: TYPES[at % TYPES.length],
        domain,
        also: [],
        status: 'active',
        confidence: 'unverified',
        title: `${SUBJECTS[at % SUBJECTS.length]} ${at}`,
        label: `${SUBJECTS[at % SUBJECTS.length]} ${at}`,
        name: `${SUBJECTS[at % SUBJECTS.length]} ${at}`,
        tags: '',
        degree: 0,
      });
    }
  }
  const edges = [];
  for (let i = 0; i + 3 < nodes.length; i += 3) {
    edges.push({ id: edges.length + 1, from_uid: nodes[i].uid, to_uid: nodes[i + 3].uid,
                 relation_type: i % 9 === 0 ? 'supersedes' : 'relates_to', note: '' });
  }
  const degree = {};
  for (const e of edges) {
    degree[e.from_uid] = (degree[e.from_uid] || 0) + 1;
    degree[e.to_uid] = (degree[e.to_uid] || 0) + 1;
  }
  for (const n of nodes) n.degree = degree[n.uid] || 0;
  return { nodes, edges };
}

const raw = store();
const D = deriveStore(raw.nodes, raw.edges);
const env = { D, W: 1200, H: 800 };

/* Run an arrangement to a stop, the way a frame loop would. */
function settle(arr) {
  let guard = 0;
  while (arr.step(env) && guard++ < 400) { /* one slice per turn */ }
  return guard;
}

const finite = list => list.every(p => Number.isFinite(p.x) && Number.isFinite(p.y));

/* ----------------------------------------------------------------- pack */

const circles = Array.from({ length: 60 }, () => ({ r: 2 + rng() * 30 }));
const R = packSiblings(circles);
let packOverlaps = 0;
for (let i = 0; i < circles.length; i++)
  for (let j = i + 1; j < circles.length; j++) {
    const a = circles[i], b = circles[j];
    if (Math.hypot(a.x - b.x, a.y - b.y) < a.r + b.r - 1e-6) packOverlaps++;
  }
const packOutside = circles.filter(c => Math.hypot(c.x, c.y) + c.r > R + 1e-6).length;

const hull = enclose(circles);
const encloseMisses = circles.filter(c =>
  Math.hypot(c.x - hull.x, c.y - hull.y) + c.r > hull.r + 1e-6).length;

/* ---------------------------------------------------------------- force */

/* The repulsion a single body exerts, swept through the distance where a
   floored inverse square would blow up. */
const CHARGE = 900;
let peak = 0;
for (let d = 0; d <= 60; d += 0.05) {
  const other = { x: 0, y: 0, m: 1 };
  const tree = buildQuad([other]);
  const out = { fx: 0, fy: 0 };
  repel(tree, { x: d, y: 0, m: 1 }, CHARGE, out);
  peak = Math.max(peak, Math.hypot(out.fx, out.fy));
}

const capped = new Sim(
  [{ x: 0, y: 0, m: 1 }, { x: 4, y: 1, m: 1 }, { x: -3, y: 5, m: 1 }], [], {});
const cappedPasses = capped.run(1e6, 5);

const halted = new Sim([{ x: 0, y: 0, m: 1 }, { x: 3, y: 3, m: 1 }], [], {});
halted.halt();

/* ---------------------------------------------------------------- field */

const blob = [];
for (let i = 0; i < 120; i++)
  blob.push({ x: 40 + rng() * 30 - 15, y: -20 + rng() * 30 - 15, w: 1 });
const field = density(blob, { x0: -60, y0: -120, x1: 140, y1: 80 }, 6, 22);
const loops = isolines(field, field.max * 0.35).map(l => smooth(l, 2));
const inside = (loop, px, py) => {
  let on = false;
  for (let i = 0, j = loop.length - 1; i < loop.length; j = i++) {
    const a = loop[i], b = loop[j];
    if ((a.y > py) !== (b.y > py)
        && px < ((b.x - a.x) * (py - a.y)) / (b.y - a.y) + a.x) on = !on;
  }
  return on;
};

/* ----------------------------------------------------------------- hubs */

const hubs = arrangement('hubs').make(env);
const hubTurns = settle(hubs);
const hubPaths = hubs.hubs.map(h => h.domain).sort();
const anchors = hubs.mems.map(b => ({
  domain: b.n.domain,
  up: b.up ? b.up.domain : null,
}));
const strandedMems = anchors.filter(a => !a.up).length;
/* a memory has to hang off a domain that is its own or an ancestor of it */
const wrongAnchor = anchors.filter(a =>
  a.up && !(a.domain === a.up || a.domain.startsWith(`${a.up}/`))).length;

/* ------------------------------------------------------------- pack tree */

const packed = arrangement('pack').make(env);
settle(packed);
let nestOverlaps = 0, escapes = 0;
(function check(n) {
  if (n.leaf) return;
  const kids = n.children;
  for (let i = 0; i < kids.length; i++) {
    const c = kids[i];
    if (Math.hypot(c.ax - n.ax, c.ay - n.ay) + c.r > n.r + 1e-6) escapes++;
    for (let j = i + 1; j < kids.length; j++) {
      const o = kids[j];
      if (Math.hypot(c.ax - o.ax, c.ay - o.ay) < c.r + o.r - 1e-6) nestOverlaps++;
    }
    check(c);
  }
})(packed.root);

/* ---------------------------------------------------------------- atlas */

const atlasA = arrangement('atlas').make(env);
const atlasB = arrangement('atlas').make(env);
const seatsA = [...atlasA.seats.entries()].map(([k, v]) => `${k}:${v.x.toFixed(6)},${v.y.toFixed(6)}`);
const seatsB = [...atlasB.seats.entries()].map(([k, v]) => `${k}:${v.x.toFixed(6)},${v.y.toFixed(6)}`);
settle(atlasA);
/* every memory should end nearer its own root's seat than any other */
let athome = 0;
for (const b of atlasA.bodies) {
  let best = null, bd = Infinity;
  for (const [name, seat] of atlasA.seats) {
    const d = (seat.x - b.x) ** 2 + (seat.y - b.y) ** 2;
    if (d < bd) { bd = d; best = name; }
  }
  if (best === topOf(b.n.domain)) athome++;
}

/* --------------------------------------------------------- domain hits */

/* Every arrangement that draws a domain has to be able to say which one the
   pointer is on: the engine tells a domain from a memory by the `domain` a
   hit carries, and focuses everything filed under it. */
const hubBody = hubs.hubs.find(h => h.domain === 'acme/x100');
const hubHit = hubs.hit(hubBody.x, hubBody.y, { k: 1 });

/* a point inside a packed domain but away from every memory */
const packDom = packed.doms.find(d => d.domain === 'acme/x100');
const packHit = packed.hit(packDom.ax, packDom.ay - packDom.r * 0.93, { k: 1 });

/* the atlas stopped at its ceiling still has to have traced its coasts */
const ceiling = arrangement('atlas').make(env);
ceiling.step(env);
ceiling.halt();
/* a point inside the coast with no settlement under it: the ground of a
   territory is the territory, and that is what the focus reads */
let atlasHit = null;
for (const c of ceiling.coasts) {
  for (const loop of c.loops) {
    for (const v of loop) {
      const x = v.x + (c.cx - v.x) * 0.12, y = v.y + (c.cy - v.y) * 0.12;
      if (ceiling.bodies.some(b => (b.x - x) ** 2 + (b.y - y) ** 2 < 18 * 18)) continue;
      const at = ceiling.hit(x, y, { k: 1 });
      if (at && !at.uid) { atlasHit = at; break; }
    }
    if (atlasHit) break;
  }
  if (atlasHit) break;
}

/* ----------------------------------------------------------------- tree */

const tree = buildTree(raw.nodes);
const rootCounts = Object.fromEntries(tree.kids.map(k => [k.name, k.count]));
const deepest = (function depth(d) {
  return d.kids.reduce((m, k) => Math.max(m, depth(k)), d.depth);
})(tree);

process.stdout.write(JSON.stringify({
  modes: ARRANGEMENTS.map(a => a.id),
  notes: ARRANGEMENTS.map(a => a.note),
  pack: { overlaps: packOverlaps, outside: packOutside, radius: R, count: circles.length },
  enclose: { misses: encloseMisses, radius: hull.r },
  force: { peak, cap: CHARGE / 16, passes: cappedPasses, halted: halted.settled },
  field: {
    loops: loops.length,
    holdsCentre: loops.some(l => inside(l, 40, -20)),
    closed: loops.every(l => l.length > 4),
  },
  hubs: {
    turns: hubTurns,
    paths: hubPaths,
    stranded: strandedMems,
    wrongAnchor,
    finite: finite(hubs.bodies),
    box: hubs.box(),
    located: !!hubs.locate(raw.nodes[0].uid),
  },
  nest: {
    overlaps: nestOverlaps,
    escapes,
    leaves: packed.leaves.length,
    located: !!packed.locate(raw.nodes[0].uid),
  },
  atlas: {
    deterministic: seatsA.join('|') === seatsB.join('|'),
    seats: seatsA.length,
    athome: athome / atlasA.bodies.length,
    finite: finite(atlasA.bodies),
    coasts: (atlasA.coasts || []).length,
  },
  tree: { roots: rootCounts, depth: deepest, nodes: raw.nodes.length },
  domainHit: {
    hub: hubHit && (hubHit.domain || null),
    hubIsMemory: !!(hubHit && hubHit.uid),
    pack: packHit && (packHit.domain || null),
    packIsMemory: !!(packHit && packHit.uid),
    atlas: atlasHit && (atlasHit.domain || null),
    haltedCoasts: ceiling.coasts.length,
    haltedQueue: ceiling.queue.length,
  },
}, null, 2));
