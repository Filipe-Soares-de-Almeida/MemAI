/* The relations graph's three arrangements (hubs, pack, atlas) behind one interface: constructor,
   step, progress, halt, box, locate, draw, hit, click. A hit carries `uid` or `domain`. */

import { clamp, packSiblings } from './graph-geom.ts';
import { Sim, spiral } from './graph-force.ts';
import type { Body, Link } from './graph-force.ts';
import { density, isolines, smooth } from './graph-field.ts';
import { dots, lines, ring, gradLine, hexA, robustBounds, Picker, LabelBoard }
  from './graph-draw.ts';
import type { Box, Pt, Rect } from './graph-draw.ts';

/* A memory as /api/graph sends it, plus what the canvas derives from it. */
export interface GraphNodeData {
  uid: string;
  type: string;
  title?: string;
  label?: string;
  domain?: string;
  also?: string[];
  tags?: string;
  degree?: number;
}

export interface GraphNode extends GraphNodeData {
  i: number;
  name: string;
  miss: boolean;
}

export interface GraphEdge { from_uid: string; to_uid: string; relation_type: string }

export interface DomainNode {
  name: string;
  path: string;
  depth: number;
  kids: DomainNode[];
  kidMap: Map<string, DomainNode>;
  mems: GraphNode[];
  count: number;
  parent?: DomainNode;
}

export interface Store {
  nodes: GraphNode[];
  edges: GraphEdge[];
  byUid: Map<string, GraphNode>;
  adj: Map<string, GraphEdge[]>;
  degree: Map<string, number>;
  tree: DomainNode;
  domainOf: Map<string, DomainNode>;
  domColor: Map<string, string>;
  hueOf: (path: string) => string;
}

/* What a click or the pointer lands on: a memory, or a domain with its count. */
export interface DomainHit { uid?: undefined; domain: string; count: number }
export type Hit = GraphNode | DomainHit;

export interface Palette {
  ink: string;
  ink2: string;
  ink3: string;
  accent: string;
  accentHi: string;
  hot: string;
  tree: string;
  treeHi: string;
  treeHot: string;
  halo: string;
  font: string;
  mono: string;
  rel: Record<string, string> & { relates_to: string };
}

export interface Show { links: boolean; domains: boolean; names: boolean }
export interface Mark { uid?: string; domain?: string; color: string; width: number }

export interface Camera {
  k: number;
  toScreen(wx: number, wy: number): Pt;
  goTo(wx: number, wy: number, k: number, ms?: number): void;
}

/* What GraphCanvas hands an arrangement, rebuilt per frame. */
export interface Env {
  D: Store;
  W: number;
  H: number;
  cam: Camera;
  palette: Palette;
  show: Show;
  hover: Hit | null;
  selected: GraphNode | null;
  linkFrom: GraphNode | null;
  lit: Set<string> | null;
  marks: Mark[];
  taken: Rect[];
  colorOf: (type: string) => string;
  fade: (uid: string) => number;
  inScope: (path: string) => boolean;
  font: (weight: number, size: number) => string;
}

export interface Arrangement {
  readonly progress: number;
  step(env?: Env): boolean;
  halt(): void;
  box(): Box;
  locate(uid: string): { x: number; y: number; r: number } | null;
  draw(ctx: CanvasRenderingContext2D, cam: Camera, env: Env): void;
  hit(x: number, y: number, cam: Camera): Hit | null;
  click?(hit: Hit | null, env: Env): boolean;
}

export interface ArrangementSpec {
  id: string;
  make: (env: Env) => Arrangement;
  note: string;
  settles: boolean;
}

/* The list under `key`, created empty on first use. */
const bucket = <K, V>(map: Map<K, V[]>, key: K): V[] => {
  let list = map.get(key);
  if (!list) { list = []; map.set(key, list); }
  return list;
};

interface Seat { x: number; y: number; room: number; name?: string }

interface HubBody extends Body {
  domain: string;
  name: string;
  count: number;
  depth: number;
  hue: string;
  r: number;
  up?: HubBody;
}

interface MemBody extends Body { n: GraphNode; uid: string; r: number; up?: HubBody }

interface RelLink { a: MemBody; b: MemBody; e: GraphEdge }

/* One frame's share of a settle, in ms: what a 16ms frame leaves after drawing. */
const SLICE_MS = 11;

/* Thirteen hues that hold apart on the ground, given to the biggest domains; the rest share the
   neutral blue-grey. */
const DOMAIN_HUES = [
  '#64b5f6', '#ffb74d', '#81c784', '#e57373', '#ba68c8', '#4dd0e1',
  '#fff176', '#f06292', '#aed581', '#9575cd', '#4db6ac', '#ff8a65', '#a1887f',
];
const DOMAIN_TAIL = '#78909c';

export const seg = (d: string | null | undefined): string[] => (d ? String(d).split('/') : []);
export const topOf = (d: string | null | undefined): string => (d ? String(d).split('/')[0] : '');

/* The domain tree, memories hung off their FILED path only (`also` would double-count); `count`
   is the subtree total that area-based arrangements divide by. */
export function buildTree(nodes: GraphNode[]): DomainNode {
  const root: DomainNode = { name: '', path: '', depth: 0, kids: [], kidMap: new Map(), mems: [], count: 0 };
  for (const n of nodes) {
    let cur = root;
    for (const s of seg(n.domain)) {
      let nx = cur.kidMap.get(s);
      if (!nx) {
        nx = { name: s, path: cur.path ? `${cur.path}/${s}` : s,
               depth: cur.depth + 1, kids: [], kidMap: new Map(), mems: [], count: 0,
               parent: cur };
        cur.kidMap.set(s, nx); cur.kids.push(nx);
      }
      cur = nx;
    }
    cur.mems.push(n);
  }
  (function tally(d: DomainNode): number {
    d.count = d.mems.length;
    d.kids.forEach(k => { tally(k); d.count += k.count; });
    d.kids.sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
    return d.count;
  })(root);
  return root;
}

const flatten = (root: DomainNode): DomainNode[] => {
  const out: DomainNode[] = [];
  (function walk(d: DomainNode) { if (d.depth) out.push(d); d.kids.forEach(walk); })(root);
  return out;
};

/* Everything the three arrangements need derived from the payload once: the
   adjacency, the degree, the domain tree, and the hue per root domain. */
export function deriveStore(nodes: GraphNode[], edges: GraphEdge[]): Store {
  const byUid = new Map(nodes.map(n => [n.uid, n]));
  const live = edges.filter(e => byUid.has(e.from_uid) && byUid.has(e.to_uid));
  const adj = new Map<string, GraphEdge[]>(nodes.map(n => [n.uid, []]));
  for (const e of live) { adj.get(e.from_uid)?.push(e); adj.get(e.to_uid)?.push(e); }
  const degree = new Map(nodes.map(n => [n.uid, adj.get(n.uid)?.length ?? 0]));
  const tree = buildTree(nodes);
  const domainOf = new Map(flatten(tree).map(d => [d.path, d]));
  const domColor = new Map<string, string>();
  tree.kids.forEach((k, i) =>
    domColor.set(k.name, i < DOMAIN_HUES.length ? DOMAIN_HUES[i] : DOMAIN_TAIL));
  const hueOf = (path: string) => domColor.get(topOf(path)) || DOMAIN_TAIL;
  return { nodes, edges: live, byUid, adj, degree, tree, domainOf, domColor, hueOf };
}

/* Relation colours: `relates_to`, nearly every relation, takes the neutral line colour. */
const relColor = (palette: Palette, type: string): string => palette.rel[type] || palette.rel.relates_to;

/* Whether a point is inside a set of closed loops, even-odd -- a territory
   with an island and a hole is several loops and one place. */
function insideLoops(loops: Pt[][], x: number, y: number): boolean {
  let on = false;
  for (const loop of loops)
    for (let i = 0, j = loop.length - 1; i < loop.length; j = i++) {
      const a = loop[i], b = loop[j];
      if ((a.y > y) !== (b.y > y)
          && x < ((b.x - a.x) * (y - a.y)) / ((b.y - a.y) || 1e-9) + a.x) on = !on;
    }
  return on;
}

/* ------------------------------------------------------------------ hubs */

/* A domain holding more than one thing is a node; one holding a single memory collapses into its
   ancestor. Roots seed from a circle packing, so physics never has to haul subtrees across. */
const HUBS = {
  charge: 900, linkK: 0.1, linkLen: 40, center: 14,
};

class Hubs implements Arrangement {
  declare bodies: Body[];
  declare hubs: HubBody[];
  declare mems: MemBody[];
  declare rel: RelLink[];
  declare byUid: Map<string, MemBody>;
  declare picker: Picker<MemBody | HubBody> | null;
  declare sim: Sim;

  constructor(env: Env) {
    const D = env.D;
    const bodies: Body[] = [], links: Link[] = [], hubs: HubBody[] = [], mems: MemBody[] = [];

    const keep = new Map<string, DomainNode>();
    for (const d of D.domainOf.values()) {
      if (d.depth === 1 || d.mems.length + d.kids.length > 1) keep.set(d.path, d);
    }
    const anchorFor = (d: DomainNode | undefined): DomainNode | null => {
      for (let x = d; x; x = x.parent) if (keep.has(x.path)) return keep.get(x.path) ?? null;
      return null;
    };

    const rootSeat = new Map<string, Seat>();
    const circles = D.tree.kids.map(k => ({ k, r: 18 + Math.sqrt(k.count) * 13, x: 0, y: 0 }));
    packSiblings(circles);
    for (const c of circles) rootSeat.set(c.k.name, { x: c.x, y: c.y, room: c.r });

    const seedR = Math.sqrt(Math.max(1, D.nodes.length)) * 26;
    const seedFor = (path: string | undefined, i: number, n: number): Pt => {
      const seat = rootSeat.get(topOf(path)) || { x: 0, y: 0, room: seedR * 0.3 };
      const s = spiral(i, Math.max(1, n), seat.room * 0.7);
      return { x: seat.x + s.x, y: seat.y + s.y };
    };

    let at = 0;
    const bodyOf = new Map<DomainNode, HubBody>();
    for (const [, d] of keep) {
      const b: HubBody = {
        domain: d.path, name: d.name, count: d.count, depth: d.depth,
        hue: D.hueOf(d.path),
        r: 4 + Math.sqrt(d.count) * 2.1,
        m: 2 + Math.sqrt(d.count) * 1.5,
        vx: 0, vy: 0,
        ...seedFor(d.path, at++, keep.size),
      };
      bodyOf.set(d, b); hubs.push(b); bodies.push(b);
    }
    for (const [, d] of keep) {
      const up = d.parent && anchorFor(d.parent);
      const mine = bodyOf.get(d), theirs = up ? bodyOf.get(up) : undefined;
      if (up && up !== d && mine && theirs) {
        mine.up = theirs;
        links.push({ a: mine, b: theirs, len: HUBS.linkLen * 2.1, k: HUBS.linkK * 1.4 });
      }
    }

    const N = D.nodes.length;
    const byUid = new Map<string, MemBody>();
    D.nodes.forEach((n, i) => {
      const b: MemBody = {
        n, uid: n.uid,
        r: 2.2 + Math.sqrt(D.degree.get(n.uid) || 0) * 1.5, m: 1,
        vx: 0, vy: 0,
        ...seedFor(n.domain, i, N),
      };
      byUid.set(n.uid, b);
      mems.push(b); bodies.push(b);
      const host = D.domainOf.get(n.domain ?? '');
      const anchor = host && anchorFor(host);
      const hub = anchor ? bodyOf.get(anchor) : undefined;
      if (hub) {
        b.up = hub;
        links.push({ a: b, b: hub, len: HUBS.linkLen, k: HUBS.linkK * 1.6 });
      }
    });

    const rel: RelLink[] = [];
    for (const e of D.edges) {
      const a = byUid.get(e.from_uid), b = byUid.get(e.to_uid);
      if (!a || !b) continue;
      rel.push({ a, b, e });
      links.push({ a, b, len: HUBS.linkLen * 1.7, k: HUBS.linkK * 0.35 });
    }

    const seat = { x: 0, y: 0 };
    for (const b of bodies) b.seat = seat;

    this.bodies = bodies; this.hubs = hubs; this.mems = mems;
    this.rel = rel; this.byUid = byUid;
    this.picker = null;
    this.sim = new Sim(bodies, links, {
      charge: HUBS.charge, linkK: HUBS.linkK, linkLen: HUBS.linkLen,
      groupK: HUBS.center / 4000, center: true,
      decay: 0.012, alphaMin: 0.03,
    });
  }

  get progress(): number { return this.sim.progress; }

  step(): boolean {
    if (this.sim.settled) return false;
    this.sim.run(SLICE_MS);
    this.picker = null;
    return !this.sim.settled;
  }

  halt(): void { this.sim.halt(); }

  box(): Box { return robustBounds(this.bodies, 26); }

  locate(uid: string) {
    const b = this.byUid.get(uid);
    return b ? { x: b.x, y: b.y, r: b.r } : null;
  }

  draw(ctx: CanvasRenderingContext2D, cam: Camera, env: Env): void {
    const { palette, show } = env;
    const K = cam.k;
    const board = new LabelBoard(ctx);
    board.reset(env.taken);
    const lit = env.lit;
    const sc = (b: Pt) => cam.toScreen(b.x, b.y);

    /* The tree is the quietest layer; what the pointer lights draws its own, brighter. */
    const leafLines: Pt[][] = [], hubLines: Pt[][] = [], hotTree: Pt[][] = [];
    for (const b of this.mems) {
      if (!b.up) continue;
      (lit && lit.has(b.uid) ? hotTree : leafLines).push([sc(b), sc(b.up)]);
    }
    for (const h of this.hubs) if (h.up) hubLines.push([sc(h), sc(h.up)]);
    lines(ctx, leafLines, palette.tree, 1, lit ? 0.45 : 1);
    lines(ctx, hubLines, palette.treeHi, 1.4, lit ? 0.5 : 1);
    lines(ctx, hotTree, palette.treeHot, 1.2);

    if (show.links) {
      const byType = new Map<string, Pt[][]>();
      for (const r of this.rel) bucket(byType, r.e.relation_type).push([sc(r.a), sc(r.b)]);
      for (const [type, segs] of byType)
        lines(ctx, segs, relColor(palette, type), type === 'relates_to' ? 1 : 1.6,
              lit ? 0.25 : 1);
    }
    /* A hovered memory's relations, faint at the end they leave; a hovered domain lights its
       memories, not relations. */
    if (lit && env.hover && env.hover.uid) {
      for (const r of this.rel) {
        if (!lit.has(r.a.uid) || !lit.has(r.b.uid)) continue;
        const from = r.e.from_uid === r.a.uid ? r.a : r.b;
        const to = from === r.a ? r.b : r.a;
        gradLine(ctx, sc(from), sc(to), palette.hot, 1.8);
      }
    }

    dots(ctx, this.mems.map(b => {
      const s = sc(b);
      return { sx: s.x, sy: s.y, r: clamp(b.r * K, 1.4, 9),
               fill: env.colorOf(b.n.type),
               alpha: env.fade(b.uid) * (lit && !lit.has(b.uid) ? 0.4 : 1) };
    }), 0.94);

    /* a hub is a place, not an unusually large memory: a wash and an edge */
    for (const h of this.hubs) {
      const s = sc(h);
      const r = clamp(h.r * K, 3, 46);
      const near = env.inScope(h.domain);
      ctx.beginPath();
      ctx.arc(s.x, s.y, r, 0, 6.2832);
      ctx.fillStyle = hexA(h.hue, near ? 0.14 : 0.05);
      ctx.fill();
      ctx.strokeStyle = hexA(h.hue, near ? (h.depth === 1 ? 0.85 : 0.45) : 0.16);
      ctx.lineWidth = h.depth === 1 ? 1.6 : 1;
      ctx.stroke();
    }

    for (const mark of env.marks) {
      const b = mark.uid ? this.byUid.get(mark.uid) : this.hubs.find(h => h.domain === mark.domain);
      if (!b) continue;
      const s = sc(b);
      ring(ctx, s.x, s.y, clamp((b.r || 3) * K, 4, 48) + 4, mark.color, mark.width);
    }

    if (!show.domains && !show.names) return;
    const named = show.domains ? [...this.hubs].sort((a, b) => b.count - a.count) : [];
    for (const h of named.slice(0, 70)) {
      const s = sc(h);
      if (s.x < -60 || s.y < -20 || s.x > env.W + 60 || s.y > env.H + 20) continue;
      const near = env.inScope(h.domain);
      board.draw(h.name, s.x, s.y, {
        font: env.font(500, h.depth === 1 ? 12.5 : 11),
        color: near ? (h.depth === 1 ? palette.ink : hexA(h.hue, 0.85)) : palette.ink3,
        halo: palette.halo,
        gap: clamp(h.r * K, 3, 46) + 5,
      });
    }
    if (!show.names || K <= 1.1) return;
    const near = this.mems
      .map(b => ({ b, s: sc(b) }))
      .filter(o => o.s.x > 0 && o.s.y > 0 && o.s.x < env.W && o.s.y < env.H)
      .filter(o => env.fade(o.b.uid) === 1)
      .sort((a, b) => (env.D.degree.get(b.b.uid) || 0) - (env.D.degree.get(a.b.uid) || 0));
    for (const o of near.slice(0, 90))
      board.draw(o.b.n.name, o.s.x, o.s.y, {
        font: env.font(400, 11.5), color: palette.ink2, halo: palette.halo,
        maxW: 190, gap: clamp(o.b.r * K, 3, 9) + 8,
      });
  }

  hit(x: number, y: number, cam: Camera): Hit | null {
    if (!this.picker) this.picker = new Picker<MemBody | HubBody>([...this.mems, ...this.hubs], 34);
    const found = this.picker.at(x, y, 16 / cam.k);
    if (!found) return null;
    return 'n' in found ? found.n : { domain: found.domain, count: found.count };
  }
}

/* ------------------------------------------------------------------ pack */

/* The domain tree as tangent circles sized by count. A domain below a screen radius draws once as
   a disc with its count and type mix, so the first frame costs the same at any store size. */
const PACK = { gap: 2, pad: 5, memR: 3, degR: 1.5, minPx: 18 };

interface PackBase {
  x: number;
  y: number;
  r: number;
  ax: number;
  ay: number;
  mix: Record<string, number>;
  parent?: PackDom;
}

interface PackLeaf extends PackBase { leaf: true; mem: GraphNode }

interface PackDom extends PackBase {
  leaf?: undefined;
  domain: string;
  name: string;
  count: number;
  depth: number;
  children: PackItem[];
  hue: string;
}

type PackItem = PackLeaf | PackDom;

function packDomain(d: DomainNode, D: Store): PackDom {
  const kids = d.kids.map(k => packDomain(k, D));
  const mems = d.mems.map((m): PackLeaf => ({
    mem: m, leaf: true,
    r: PACK.memR + Math.sqrt(D.degree.get(m.uid) || 0) * PACK.degR,
    x: 0, y: 0, ax: 0, ay: 0, mix: {},
  }));
  const all: PackItem[] = [...kids, ...mems];
  const gap = d.depth === 0 ? PACK.gap * 2 : PACK.gap;
  for (const c of all) c.r += gap;
  const R = all.length ? packSiblings(all) : PACK.memR;
  for (const c of all) c.r -= gap;
  const node: PackDom = {
    domain: d.path, name: d.name, count: d.count, depth: d.depth,
    children: all, r: R + (d.depth ? PACK.pad : 0),
    hue: D.hueOf(d.path || d.name), x: 0, y: 0, ax: 0, ay: 0, mix: {},
  };
  for (const c of all) c.parent = node;
  return node;
}

class Pack implements Arrangement {
  declare root: PackDom;
  declare leaves: PackLeaf[];
  declare doms: PackDom[];
  declare byUid: Map<string, PackLeaf>;

  constructor(env: Env) {
    const D = env.D;
    const root = packDomain(D.tree, D);
    (function place(node: PackItem, ox: number, oy: number) {
      node.ax = ox + node.x;
      node.ay = oy + node.y;
      if (!node.leaf) for (const c of node.children) place(c, node.ax, node.ay);
    })(root, 0, 0);

    const leaves: PackLeaf[] = [], doms: PackDom[] = [];
    (function walk(n: PackItem) {
      if (n.leaf) { leaves.push(n); return; }
      doms.push(n);
      for (const c of n.children) walk(c);
    })(root);

    /* the type mix of every subtree, so a closed disc still says WHAT it
       holds and not only how much */
    (function mix(n: PackItem): Record<string, number> {
      n.mix = {};
      if (n.leaf) { n.mix[n.mem.type] = 1; return n.mix; }
      for (const c of n.children) {
        const m = mix(c);
        for (const k in m) n.mix[k] = (n.mix[k] || 0) + m[k];
      }
      return n.mix;
    })(root);

    this.root = root;
    this.leaves = leaves;
    this.doms = doms;
    this.byUid = new Map(leaves.map(l => [l.mem.uid, l]));
  }

  get progress(): number { return 1; }

  step(): boolean { return false; }

  halt(): void {}

  box(): Box {
    const r = this.root.r;
    return { x0: -r - 20, y0: -r - 20, x1: r + 20, y1: r + 20 };
  }

  locate(uid: string) {
    const l = this.byUid.get(uid);
    return l ? { x: l.ax, y: l.ay, r: l.r } : null;
  }

  draw(ctx: CanvasRenderingContext2D, cam: Camera, env: Env): void {
    const { palette, show } = env;
    const K = cam.k;
    const board = new LabelBoard(ctx);
    board.reset(env.taken);
    const onScreen = (n: PackItem, rpx: number) => {
      const s = cam.toScreen(n.ax, n.ay);
      return rpx > 1 && s.x + rpx > -40 && s.y + rpx > -40
             && s.x - rpx < env.W + 40 && s.y - rpx < env.H + 40;
    };

    const open: PackDom[] = [], closed: PackDom[] = [], shown: PackLeaf[] = [];
    (function walk(n: PackItem) {
      const rpx = n.r * K;
      if (!onScreen(n, rpx)) return;
      if (n.leaf) { shown.push(n); return; }
      if (rpx < PACK.minPx || !n.children.length) { closed.push(n); return; }
      if (n.depth) open.push(n);
      for (const c of n.children) walk(c);
    })(this.root);

    /* shallowest first, so a child sits on top of the parent it is inside */
    for (const n of open.slice().sort((a, b) => a.depth - b.depth)) {
      const s = cam.toScreen(n.ax, n.ay), rpx = n.r * K;
      const near = env.inScope(n.domain) ? 1 : 0.35;
      ctx.beginPath();
      ctx.arc(s.x, s.y, rpx, 0, 6.2832);
      ctx.fillStyle = hexA(n.hue, (n.depth === 1 ? 0.055 : 0.05) * near);
      ctx.fill();
      ctx.strokeStyle = hexA(n.hue, (n.depth === 1 ? 0.42 : 0.22) * near);
      ctx.lineWidth = n.depth === 1 ? 1.4 : 1;
      ctx.stroke();
    }

    for (const n of closed) {
      const s = cam.toScreen(n.ax, n.ay), rpx = n.r * K;
      const mixRing = rpx > 6;
      const near = env.inScope(n.domain) ? 1 : 0.35;
      ctx.globalAlpha = near;
      ctx.beginPath();
      ctx.arc(s.x, s.y, rpx, 0, 6.2832);
      ctx.fillStyle = hexA(n.hue, mixRing ? 0.1 : 0.3);
      ctx.fill();
      if (mixRing) {
        const total = Object.values(n.mix).reduce((a, b) => a + b, 0) || 1;
        const w = clamp(rpx * 0.34, 2.2, 9);
        let a0 = -Math.PI / 2;
        ctx.lineWidth = w;
        for (const [type, v] of Object.entries(n.mix).sort((a, b) => b[1] - a[1])) {
          const a1 = a0 + (v / total) * 6.2832;
          ctx.beginPath();
          ctx.arc(s.x, s.y, rpx - w / 2 - 0.5, a0, a1);
          ctx.strokeStyle = env.colorOf(type);
          ctx.stroke();
          a0 = a1;
        }
      } else {
        ctx.strokeStyle = hexA(n.hue, 0.6);
        ctx.lineWidth = 1;
        ctx.stroke();
      }
      /* the count is what the disc is FOR: it stays when the titles do not */
      if (rpx > 14)
        board.force(String(n.count), s.x, s.y, {
          font: env.font(500, clamp(rpx * 0.5, 9, 15)),
          color: palette.ink, halo: palette.halo, haloWidth: 3.5,
        });
      ctx.globalAlpha = 1;
    }

    const lit = env.lit;
    dots(ctx, shown.map(l => {
      const s = cam.toScreen(l.ax, l.ay);
      return { sx: s.x, sy: s.y, r: clamp(l.r * K, 1.3, 14),
               fill: env.colorOf(l.mem.type),
               alpha: env.fade(l.mem.uid) * (lit && !lit.has(l.mem.uid) ? 0.4 : 1) };
    }), 0.95);

    /* Relations only for what the pointer is on; the toggle hides this highlight. */
    /* a domain under the pointer is not asking about the relations among
       what it holds: what joins it to them is the circle they sit in */
    const at = env.hover && env.hover.uid ? env.hover : env.selected;
    if (show.links && at && at.uid) {
      const from = this.byUid.get(at.uid);
      for (const e of env.D.adj.get(at.uid) || []) {
        const other = e.from_uid === at.uid ? e.to_uid : e.from_uid;
        const peer = this.byUid.get(other);
        if (!from || !peer) continue;
        const here = cam.toScreen(from.ax, from.ay), there = cam.toScreen(peer.ax, peer.ay);
        const out = e.from_uid === at.uid;
        gradLine(ctx, out ? here : there, out ? there : here,
                 relColor(palette, e.relation_type), 1.8);
        ring(ctx, there.x, there.y, 6, palette.hot, 1.4);
      }
    }

    for (const mark of env.marks) {
      const b = mark.uid ? this.byUid.get(mark.uid)
                         : this.doms.find(d => d.domain === mark.domain);
      if (!b) continue;
      const s = cam.toScreen(b.ax, b.ay);
      ring(ctx, s.x, s.y, clamp(b.r * K, 4, 400) + 3, mark.color, mark.width);
    }

    if (!show.domains && !show.names) return;
    const named = show.domains ? [...open, ...closed].sort((a, b) => b.r - a.r) : [];
    for (const n of named.slice(0, 120)) {
      const s = cam.toScreen(n.ax, n.ay), rpx = n.r * K;
      if (rpx < 16) continue;
      const inside = closed.includes(n);
      const near = env.inScope(n.domain);
      board.draw(n.name, s.x, inside ? s.y + rpx : s.y - rpx, {
        font: env.font(500, clamp(rpx * 0.24, 10, 17)),
        color: near ? (n.depth === 1 ? palette.ink : hexA(n.hue, 0.9)) : palette.ink3,
        halo: palette.halo,
        sides: inside ? ['bottom', 'top'] : ['top', 'bottom'],
        gap: 7, maxW: Math.max(90, rpx * 2),
      });
    }
    if (!show.names || K <= 2.2) return;
    for (const l of shown.slice(0, 140)) {
      if (env.fade(l.mem.uid) < 1) continue;
      const s = cam.toScreen(l.ax, l.ay);
      board.draw(l.mem.name, s.x, s.y, {
        font: env.font(400, 11.5), color: palette.ink2, halo: palette.halo,
        maxW: 200, gap: clamp(l.r * K, 3, 14) + 4,
      });
    }
  }

  /* A memory first, then the innermost domain the pointer is inside: a click
     on the ground between two memories still goes somewhere. */
  hit(x: number, y: number, cam: Camera): Hit | null {
    let best: PackLeaf | null = null, bd = Infinity;
    for (const l of this.leaves) {
      const d = (l.ax - x) ** 2 + (l.ay - y) ** 2;
      const r = Math.max(l.r, 8 / cam.k);
      if (d < r * r && d < bd) { bd = d; best = l; }
    }
    if (best) return best.mem;
    let inner: PackDom | null = null;
    for (const n of this.doms) {
      if (!n.depth) continue;
      if ((n.ax - x) ** 2 + (n.ay - y) ** 2 < n.r * n.r)
        if (!inner || n.r < inner.r) inner = n;
    }
    return inner ? { domain: inner.domain, count: inner.count } : null;
  }

  /* Click descends into a domain; a click on the ground frames the store. */
  click(hit: Hit | null, env: Env): boolean {
    if (!hit || !hit.domain || hit.uid) return false;
    const node = this.doms.find(d => d.domain === hit.domain);
    if (!node) return false;
    env.cam.goTo(node.ax, node.ay, Math.min(env.W, env.H) / (node.r * 2.4));
    return true;
  }
}

/* ----------------------------------------------------------------- atlas */

/* Every root gets a FIXED seat from a count-sized circle packing, so territories stay put as the
   store grows; the coastline is a level set of the domain's own density. */
const ATLAS = { charge: 950, seatK: 14, level: 0.2, sigma: 34, area: 74 };

interface AtlasBody extends Body { n: GraphNode; uid: string; seat: Seat; r: number }

interface AtlasPair { a: AtlasBody; b: AtlasBody; e: GraphEdge }

interface AtlasLink extends AtlasPair { cross: boolean }

interface Coast {
  root: DomainNode;
  hue: string;
  loops: Pt[][];
  n: number;
  cx: number;
  cy: number;
  span: number;
}

class Atlas implements Arrangement {
  declare bodies: AtlasBody[];
  declare byUid: Map<string, AtlasBody>;
  declare seats: Map<string, Seat>;
  declare roots: DomainNode[];
  declare hueOf: (path: string) => string;
  declare links: AtlasLink[];
  declare coasts: Coast[] | null;
  declare queue: DomainNode[] | null;
  declare sim: Sim;

  constructor(env: Env) {
    const D = env.D;
    const roots = D.tree.kids;
    const N = Math.max(1, D.nodes.length);
    const circles = roots.map(r => ({ root: r, r: ATLAS.area * Math.sqrt(r.count) / 3 + 26, x: 0, y: 0 }));
    packSiblings(circles);
    const seats = new Map<string, Seat>();
    for (const c of circles)
      seats.set(c.root.name, { x: c.x, y: c.y, name: c.root.name, room: c.r });

    const bodies: AtlasBody[] = [];
    D.nodes.forEach((n, i) => {
      const seat = seats.get(topOf(n.domain)) || { x: 0, y: 0, room: 40 };
      const s = spiral(i, N, (seat.room || 40) * 0.7);
      bodies.push({
        n, uid: n.uid, m: 1, seat,
        x: seat.x + s.x, y: seat.y + s.y, vx: 0, vy: 0,
        r: 2.4 + Math.sqrt(D.degree.get(n.uid) || 0) * 1.6,
      });
    });
    const byUid = new Map(bodies.map(b => [b.uid, b]));
    const pairs = D.edges
      .map(e => ({ a: byUid.get(e.from_uid), b: byUid.get(e.to_uid), e }))
      .filter((l): l is AtlasPair => !!(l.a && l.b));

    this.bodies = bodies; this.byUid = byUid; this.seats = seats; this.roots = roots;
    this.hueOf = D.hueOf;
    this.links = pairs.map(l => ({ ...l, cross: topOf(l.a.n.domain) !== topOf(l.b.n.domain) }));
    this.coasts = null;
    this.queue = null;
    this.sim = new Sim(bodies, pairs.map(l => ({ a: l.a, b: l.b, len: 54, k: 0.03 })), {
      charge: ATLAS.charge, groupK: ATLAS.seatK / 1000, center: false,
      decay: 0.014, alphaMin: 0.035,
    });
  }

  /* The physics owns the first nine tenths and the coastlines the last: both
     are a wait the reader is watching a bar for. */
  get progress(): number {
    if (!this.sim.settled) return this.sim.progress * 0.9;
    const total = this.roots.length || 1;
    const left = this.queue ? this.queue.length : 0;
    return 0.9 + 0.1 * ((total - left) / total);
  }

  /* Busy through the physics settle, then one territory traced per frame so frames are not
     dropped; each appears as it lands. */
  step(): boolean {
    if (!this.sim.settled) {
      this.sim.run(SLICE_MS);
      this.queue = null;
      this.coasts = null;
      return true;
    }
    if (!this.coasts || !this.queue) { this.queue = this.roots.slice(); this.coasts = []; }
    const next = this.queue.shift();
    if (!next) return false;
    const one = this._coast(next);
    if (one) this.coasts.push(one);
    if (!this.queue.length) {
      this.coasts.sort((a, b) => b.n - a.n);
      return false;
    }
    return true;
  }

  /* The settle ceiling stops the physics; the finite tracing left still runs here. */
  halt(): void {
    this.sim.halt();
    if (!this.coasts || !this.queue) { this.queue = this.roots.slice(); this.coasts = []; }
    for (let next = this.queue.shift(); next; next = this.queue.shift()) {
      const one = this._coast(next);
      if (one) this.coasts.push(one);
    }
    this.coasts.sort((a, b) => b.n - a.n);
  }

  /* The density field of one root domain, and its level set as a coastline. */
  _coast(root: DomainNode): Coast | null {
    const pts = this.bodies.filter(b => topOf(b.n.domain) === root.name)
      .map(b => ({ x: b.x, y: b.y, w: 1 }));
    if (!pts.length) return null;
    const box = robustBounds(pts, ATLAS.sigma * 2.6, 0);
    const field = density(pts, box, Math.max(5, ATLAS.sigma / 4), ATLAS.sigma);
    const loops = isolines(field, field.max * ATLAS.level).map(l => smooth(l, 2));
    let cx = 0, cy = 0;
    for (const p of pts) { cx += p.x; cy += p.y; }
    return {
      root, hue: this.hueOf(root.name), loops, n: pts.length,
      cx: cx / pts.length, cy: cy / pts.length,
      span: Math.max(box.x1 - box.x0, box.y1 - box.y0),
    };
  }

  box(): Box { return robustBounds(this.bodies, 90, 0.004); }

  locate(uid: string) {
    const b = this.byUid.get(uid);
    return b ? { x: b.x, y: b.y, r: b.r } : null;
  }

  draw(ctx: CanvasRenderingContext2D, cam: Camera, env: Env): void {
    const { palette, show } = env;
    const K = cam.k;
    const board = new LabelBoard(ctx);
    board.reset(env.taken);
    const sc = (p: Pt) => cam.toScreen(p.x, p.y);
    const lit = env.lit;

    if (this.coasts) {
      for (const c of this.coasts) {
        if (!c.loops.length) continue;
        ctx.beginPath();
        for (const loop of c.loops) {
          const s0 = sc(loop[0]);
          ctx.moveTo(s0.x, s0.y);
          for (let i = 1; i < loop.length; i++) { const s = sc(loop[i]); ctx.lineTo(s.x, s.y); }
          ctx.closePath();
        }
        const near = env.inScope(c.root.name) ? 1 : 0.3;
        ctx.fillStyle = hexA(c.hue, 0.1 * near);
        ctx.fill('evenodd');
        ctx.strokeStyle = hexA(c.hue, 0.55 * near);
        ctx.lineWidth = 1.2;
        ctx.stroke();
      }
    }

    /* A road between territories is bowed so parallel roads do not overlap; one inside a
       territory stays straight. */
    if (show.links) {
      const local: Pt[][] = [], trunk = new Map<string, Pt[][]>();
      for (const l of this.links) {
        const a = sc(l.a), b = sc(l.b);
        if (!l.cross) { local.push([a, b]); continue; }
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        const dx = b.x - a.x, dy = b.y - a.y;
        const c = { x: mx - dy * 0.11, y: my + dx * 0.11 };
        bucket(trunk, l.e.relation_type).push([a, c, b]);
      }
      lines(ctx, local, palette.tree, 1, lit ? 0.2 : 1);
      for (const [type, set] of trunk)
        lines(ctx, set, relColor(palette, type), type === 'relates_to' ? 1.1 : 1.8,
              lit ? 0.18 : 0.9);
    }
    if (lit && env.hover && env.hover.uid) {
      for (const l of this.links) {
        if (!lit.has(l.a.uid) || !lit.has(l.b.uid)) continue;
        const from = l.e.from_uid === l.a.uid ? l.a : l.b;
        const to = from === l.a ? l.b : l.a;
        gradLine(ctx, sc(from), sc(to), palette.hot, 2);
      }
    }

    dots(ctx, this.bodies.map(b => {
      const s = sc(b);
      return { sx: s.x, sy: s.y, r: clamp(b.r * K, 1.3, 10),
               fill: env.colorOf(b.n.type),
               alpha: env.fade(b.uid) * (lit && !lit.has(b.uid) ? 0.4 : 1) };
    }), 0.95);

    for (const mark of env.marks) {
      if (mark.domain) {
        /* a territory is marked along its own coast: there is no circle to
           put a ring around */
        const c = (this.coasts || []).find(one => one.root.name === topOf(mark.domain));
        if (!c) continue;
        ctx.beginPath();
        for (const loop of c.loops) {
          const s0 = sc(loop[0]);
          ctx.moveTo(s0.x, s0.y);
          for (let i = 1; i < loop.length; i++) { const s = sc(loop[i]); ctx.lineTo(s.x, s.y); }
          ctx.closePath();
        }
        ctx.strokeStyle = mark.color;
        ctx.lineWidth = mark.width;
        ctx.stroke();
        continue;
      }
      const b = mark.uid && this.byUid.get(mark.uid);
      if (!b) continue;
      const s = sc(b);
      ring(ctx, s.x, s.y, clamp(b.r * K, 3, 11) + 4, mark.color, mark.width);
    }

    if (!show.domains && !show.names) return;
    if (show.domains && this.coasts) {
      for (const c of this.coasts) {
        const s = cam.toScreen(c.cx, c.cy);
        const w = c.span * K;
        if (w < 46) continue;
        const size = clamp(w * 0.055, 10, 21);
        board.force(c.root.name.toUpperCase(), s.x, s.y, {
          font: env.font(500, size),
          color: hexA(c.hue, env.inScope(c.root.name) ? 0.95 : 0.3),
          halo: palette.halo, haloWidth: 4.5, maxW: Math.max(80, w * 0.9),
        });
        if (w > 130)
          board.force(String(c.n), s.x, s.y + size * 0.95, {
            font: `400 11px ${palette.mono}`, color: palette.ink3,
            halo: palette.halo, haloWidth: 3.5,
          });
      }
    }
    if (!show.names || K <= 0.5) return;
    const ranked = this.bodies
      .map(b => ({ b, s: sc(b), deg: env.D.degree.get(b.uid) || 0 }))
      .filter(o => o.deg > 1 && o.s.x > 0 && o.s.y > 0 && o.s.x < env.W && o.s.y < env.H)
      .filter(o => env.fade(o.b.uid) === 1)
      .sort((a, b) => b.deg - a.deg);
    for (const o of ranked.slice(0, 70))
      board.draw(o.b.n.name, o.s.x, o.s.y, {
        font: env.font(400, 11), color: palette.ink2, halo: palette.halo,
        maxW: 175, gap: clamp(o.b.r * K, 3, 10) + 5,
      });
  }

  /* A settlement first, then the territory the pointer is standing in: the
     ground between two memories is a place here, and it is the domain. */
  hit(x: number, y: number, cam: Camera): Hit | null {
    const r = 13 / cam.k;
    let best: AtlasBody | null = null, bd = r * r;
    for (const b of this.bodies) {
      const d = (b.x - x) ** 2 + (b.y - y) ** 2;
      if (d < bd) { bd = d; best = b; }
    }
    if (best) return best.n;
    for (const c of this.coasts || []) {
      if (insideLoops(c.loops, x, y)) return { domain: c.root.name, count: c.n };
    }
    return null;
  }
}

/* The three, in the order the picker offers them. `note` names the i18n key
   the legend explains each one with. */
export const ARRANGEMENTS: ArrangementSpec[] = [
  { id: 'hubs', make: env => new Hubs(env), note: 'g.mode.hubs.note', settles: true },
  { id: 'pack', make: env => new Pack(env), note: 'g.mode.pack.note', settles: false },
  { id: 'atlas', make: env => new Atlas(env), note: 'g.mode.atlas.note', settles: true },
];

export const DEFAULT_MODE = ARRANGEMENTS[0].id;

export const arrangement = (id: string): ArrangementSpec =>
  ARRANGEMENTS.find(a => a.id === id) || ARRANGEMENTS[0];
