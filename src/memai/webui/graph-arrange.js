/* The three arrangements of the relations graph, behind one interface.

   An arrangement owns where things ARE and how they are drawn; the engine
   (graph-2d.js) owns the camera, the pointer, the selection and the two show
   toggles, and hands all of it over in `env` on every frame. The interface is:

     new Arrangement(env)     build, from the derived store in env.D
     step(env) -> boolean     one frame's share of the work, true while busy
     progress                 0..1, what the settle bar reports
     halt()                   stop arranging where it stands
     box()                    the world box the camera frames
     locate(uid)              {x, y, r} of one memory, or null
     draw(ctx, cam, env)      screen-space drawing, camera already resolved
     hit(x, y, cam)           what is at a WORLD point, or null
     click(hit, env)          optional camera move on a click, true if it moved

   A hit is either an engine node (it carries `uid`) or a domain body, which
   carries `domain` and no uid. The engine tells them apart that way.

   The three read the same store differently: `hubs` makes every domain a node
   and hangs its memories off it, `pack` nests each domain inside its parent as
   a circle, `atlas` gives every root a fixed seat and draws the density of
   what sits in it as a coastline. */

import { clamp, packSiblings } from './graph-geom.js';
import { Sim, spiral } from './graph-force.js';
import { density, isolines, smooth } from './graph-field.js';
import { dots, lines, ring, gradLine, hexA, robustBounds, Picker, LabelBoard }
  from './graph-draw.js';

/* One frame's share of a settle, in milliseconds. What is left of a 16ms
   frame after the drawing, so the graph keeps answering the pointer while it
   condenses. */
const SLICE_MS = 11;

/* Thirteen hues that hold apart on the dashboard's ground, and a neutral for
   the tail. No palette separates thirty domains, so rank decides: the biggest
   get a hue and the rest share the blue-grey. */
const DOMAIN_HUES = [
  '#64b5f6', '#ffb74d', '#81c784', '#e57373', '#ba68c8', '#4dd0e1',
  '#fff176', '#f06292', '#aed581', '#9575cd', '#4db6ac', '#ff8a65', '#a1887f',
];
const DOMAIN_TAIL = '#78909c';

export const seg = d => (d ? String(d).split('/') : []);
export const topOf = d => (d ? String(d).split('/')[0] : '');

/* The domain tree: one node per path segment that exists, with memories hung
   off the exact path they are FILED at -- `also` paths cross the tree and
   would double-count every memory that carries one. `count` is the subtree
   total, which is what every area-based arrangement divides space by. */
export function buildTree(nodes) {
  const root = { name: '', path: '', depth: 0, kids: [], kidMap: new Map(), mems: [], count: 0 };
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
  (function tally(d) {
    d.count = d.mems.length;
    d.kids.forEach(k => { tally(k); d.count += k.count; });
    d.kids.sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
    return d.count;
  })(root);
  return root;
}

const flatten = root => {
  const out = [];
  (function walk(d) { if (d.depth) out.push(d); d.kids.forEach(walk); })(root);
  return out;
};

/* Everything the three arrangements need derived from the payload once: the
   adjacency, the degree, the domain tree, and the hue per root domain. */
export function deriveStore(nodes, edges) {
  const byUid = new Map(nodes.map(n => [n.uid, n]));
  const live = edges.filter(e => byUid.has(e.from_uid) && byUid.has(e.to_uid));
  const adj = new Map(nodes.map(n => [n.uid, []]));
  for (const e of live) { adj.get(e.from_uid).push(e); adj.get(e.to_uid).push(e); }
  const degree = new Map(nodes.map(n => [n.uid, adj.get(n.uid).length]));
  const tree = buildTree(nodes);
  const domainOf = new Map(flatten(tree).map(d => [d.path, d]));
  const domColor = new Map();
  tree.kids.forEach((k, i) =>
    domColor.set(k.name, i < DOMAIN_HUES.length ? DOMAIN_HUES[i] : DOMAIN_TAIL));
  const hueOf = path => domColor.get(topOf(path)) || DOMAIN_TAIL;
  return { nodes, edges: live, byUid, adj, degree, tree, domainOf, domColor, hueOf };
}

/* The relation colours. Nearly every relation in a store is `relates_to`, so
   it takes the neutral line colour and the other three are the ones that read
   as something. */
const relColor = (palette, type) => palette.rel[type] || palette.rel.relates_to;

/* Whether a point is inside a set of closed loops, even-odd -- a territory
   with an island and a hole is several loops and one place. */
function insideLoops(loops, x, y) {
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

/* Every domain that holds more than one thing is a node; a memory hangs off
   the nearest surviving ancestor of the domain it is filed at. A domain
   holding one memory and nothing else is COLLAPSED -- a hub with a single
   leaf is a node that says nothing, and a store filed deeply is mostly those.

   Roots are seeded from a circle packing sized by count, never from one
   global spiral: the physics would have to transport every subtree across the
   field and alpha runs out mid-transit, which freezes the arrangement with
   one root ejected and the rest in a knot. */
const HUBS = {
  charge: 900, linkK: 0.1, linkLen: 40, center: 14,
};

class Hubs {
  constructor(env) {
    const D = env.D;
    const bodies = [], links = [], hubs = [], mems = [];

    const keep = new Map();
    for (const d of D.domainOf.values()) {
      if (d.depth === 1 || d.mems.length + d.kids.length > 1) keep.set(d.path, d);
    }
    const anchorFor = d => {
      for (let x = d; x; x = x.parent) if (keep.has(x.path)) return keep.get(x.path);
      return null;
    };

    const rootSeat = new Map();
    const circles = D.tree.kids.map(k => ({ k, r: 18 + Math.sqrt(k.count) * 13 }));
    packSiblings(circles);
    for (const c of circles) rootSeat.set(c.k.name, { x: c.x, y: c.y, room: c.r });

    const seedR = Math.sqrt(Math.max(1, D.nodes.length)) * 26;
    const seedFor = (path, i, n) => {
      const seat = rootSeat.get(topOf(path)) || { x: 0, y: 0, room: seedR * 0.3 };
      const s = spiral(i, Math.max(1, n), seat.room * 0.7);
      return { x: seat.x + s.x, y: seat.y + s.y };
    };

    let at = 0;
    for (const [, d] of keep) {
      const b = {
        domain: d.path, name: d.name, count: d.count, depth: d.depth,
        hue: D.hueOf(d.path),
        r: 4 + Math.sqrt(d.count) * 2.1,
        m: 2 + Math.sqrt(d.count) * 1.5,
        ...seedFor(d.path, at++, keep.size),
      };
      d._body = b; hubs.push(b); bodies.push(b);
    }
    for (const [, d] of keep) {
      const up = d.parent && anchorFor(d.parent);
      if (up && up !== d) {
        d._body.up = up._body;
        links.push({ a: d._body, b: up._body, len: HUBS.linkLen * 2.1, k: HUBS.linkK * 1.4 });
      }
    }

    const N = D.nodes.length;
    const byUid = new Map();
    D.nodes.forEach((n, i) => {
      const b = {
        n, uid: n.uid,
        r: 2.2 + Math.sqrt(D.degree.get(n.uid) || 0) * 1.5, m: 1,
        ...seedFor(n.domain, i, N),
      };
      byUid.set(n.uid, b);
      mems.push(b); bodies.push(b);
      const host = D.domainOf.get(n.domain);
      const anchor = host && anchorFor(host);
      if (anchor) {
        b.up = anchor._body;
        links.push({ a: b, b: anchor._body, len: HUBS.linkLen, k: HUBS.linkK * 1.6 });
      }
    });

    const rel = [];
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

  get progress() { return this.sim.progress; }

  step() {
    if (this.sim.settled) return false;
    this.sim.run(SLICE_MS);
    this.picker = null;
    return !this.sim.settled;
  }

  halt() { this.sim.halt(); }

  box() { return robustBounds(this.bodies, 26); }

  locate(uid) {
    const b = this.byUid.get(uid);
    return b ? { x: b.x, y: b.y, r: b.r } : null;
  }

  draw(ctx, cam, env) {
    const { palette, show } = env;
    const K = cam.k;
    const board = new LabelBoard(ctx);
    board.reset(env.taken);
    const lit = env.lit;
    const sc = b => cam.toScreen(b.x, b.y);

    /* The tree, the quietest thing on screen: it is the scaffolding the
       arrangement stands on, not a relation. What the pointer is lighting
       draws its own, brighter: in this arrangement a domain IS those lines. */
    const leafLines = [], hubLines = [], hotTree = [];
    for (const b of this.mems) {
      if (!b.up) continue;
      (lit && lit.has(b.uid) ? hotTree : leafLines).push([sc(b), sc(b.up)]);
    }
    for (const h of this.hubs) if (h.up) hubLines.push([sc(h), sc(h.up)]);
    lines(ctx, leafLines, palette.tree, 1, lit ? 0.45 : 1);
    lines(ctx, hubLines, palette.treeHi, 1.4, lit ? 0.5 : 1);
    lines(ctx, hotTree, palette.treeHot, 1.2);

    if (show.links) {
      const byType = new Map();
      for (const r of this.rel) {
        const k = r.e.relation_type;
        if (!byType.has(k)) byType.set(k, []);
        byType.get(k).push([sc(r.a), sc(r.b)]);
      }
      for (const [type, segs] of byType)
        lines(ctx, segs, relColor(palette, type), type === 'relates_to' ? 1 : 1.6,
              lit ? 0.25 : 1);
    }
    /* the hovered memory's own relations, each drawn faint at the end it
       leaves: which way a relation points is only ever asked about one */
    if (lit) {
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

    if (!show.titles) return;
    const named = [...this.hubs].sort((a, b) => b.count - a.count);
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
    if (K <= 1.1) return;
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

  hit(x, y, cam) {
    if (!this.picker) this.picker = new Picker([...this.mems, ...this.hubs], 34);
    const found = this.picker.at(x, y, 16 / cam.k);
    return found ? (found.n || { domain: found.domain, count: found.count }) : null;
  }
}

/* ------------------------------------------------------------------ pack */

/* The domain tree packed as tangent circles: a domain is a body holding its
   children and its own memories, sized by count. Two domains cannot overlap,
   so telling them apart is geometry rather than tuning.

   Nothing is drawn below a screen radius: a domain too small to open draws
   once, as a disc carrying its count and the mix of types inside it. That is
   what makes the first frame cost the same at six hundred memories and at ten
   thousand. */
const PACK = { gap: 2, pad: 5, memR: 3, degR: 1.5, minPx: 18 };

function packDomain(d, D) {
  const kids = d.kids.map(k => packDomain(k, D));
  const mems = d.mems.map(m => ({
    mem: m, leaf: true,
    r: PACK.memR + Math.sqrt(D.degree.get(m.uid) || 0) * PACK.degR,
  }));
  const all = [...kids, ...mems];
  const gap = d.depth === 0 ? PACK.gap * 2 : PACK.gap;
  for (const c of all) c.r += gap;
  const R = all.length ? packSiblings(all) : PACK.memR;
  for (const c of all) c.r -= gap;
  const node = {
    domain: d.path, name: d.name, count: d.count, depth: d.depth,
    children: all, r: R + (d.depth ? PACK.pad : 0),
    hue: D.hueOf(d.path || d.name), x: 0, y: 0,
  };
  for (const c of all) c.parent = node;
  return node;
}

class Pack {
  constructor(env) {
    const D = env.D;
    const root = packDomain(D.tree, D);
    (function place(node, ox, oy) {
      node.ax = ox + node.x;
      node.ay = oy + node.y;
      if (node.children) for (const c of node.children) place(c, node.ax, node.ay);
    })(root, 0, 0);

    const leaves = [], doms = [];
    (function walk(n) {
      if (n.leaf) { leaves.push(n); return; }
      doms.push(n);
      for (const c of n.children) walk(c);
    })(root);

    /* the type mix of every subtree, so a closed disc still says WHAT it
       holds and not only how much */
    (function mix(n) {
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

  get progress() { return 1; }

  step() { return false; }

  halt() {}

  box() {
    const r = this.root.r;
    return { x0: -r - 20, y0: -r - 20, x1: r + 20, y1: r + 20 };
  }

  locate(uid) {
    const l = this.byUid.get(uid);
    return l ? { x: l.ax, y: l.ay, r: l.r } : null;
  }

  draw(ctx, cam, env) {
    const { palette, show } = env;
    const K = cam.k;
    const board = new LabelBoard(ctx);
    board.reset(env.taken);
    const onScreen = (n, rpx) => {
      const s = cam.toScreen(n.ax, n.ay);
      return rpx > 1 && s.x + rpx > -40 && s.y + rpx > -40
             && s.x - rpx < env.W + 40 && s.y - rpx < env.H + 40;
    };

    const open = [], closed = [], shown = [];
    (function walk(n) {
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

    /* Relations, only for what the pointer is on. Drawing all of them over
       nested circles is the tangle this arrangement exists to avoid, so the
       toggle cannot turn them all on -- what it hides is this highlight. */
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

    if (!show.titles) return;
    const named = [...open, ...closed].sort((a, b) => b.r - a.r);
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
    if (K <= 2.2) return;
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
  hit(x, y, cam) {
    let best = null, bd = Infinity;
    for (const l of this.leaves) {
      const d = (l.ax - x) ** 2 + (l.ay - y) ** 2;
      const r = Math.max(l.r, 8 / cam.k);
      if (d < r * r && d < bd) { bd = d; best = l; }
    }
    if (best) return best.mem;
    let inner = null;
    for (const n of this.doms) {
      if (!n.depth) continue;
      if ((n.ax - x) ** 2 + (n.ay - y) ** 2 < n.r * n.r)
        if (!inner || n.r < inner.r) inner = n;
    }
    return inner ? { domain: inner.domain, count: inner.count } : null;
  }

  /* Click descends into a domain; a click on the ground frames the store. */
  click(hit, env) {
    if (!hit || !hit.domain || hit.uid) return false;
    const node = this.doms.find(d => d.domain === hit.domain);
    if (!node) return false;
    env.cam.goTo(node.ax, node.ay, Math.min(env.W, env.H) / (node.r * 2.4));
    return true;
  }
}

/* ----------------------------------------------------------------- atlas */

/* Every root domain gets a FIXED seat, so a territory does not move when the
   store grows and the map can be learned. The seats come from packing one
   circle per root sized by count: evenly spaced seats put a root of a hundred
   and fifty memories the same distance from its neighbour as a root of one,
   and the large ones then overlap in the middle.

   The coastline is a level set of the domain's own density, so a territory
   has a shape rather than a radius, and it grows on its own. */
const ATLAS = { charge: 950, seatK: 14, level: 0.2, sigma: 34, area: 74 };

class Atlas {
  constructor(env) {
    const D = env.D;
    const roots = D.tree.kids;
    const N = Math.max(1, D.nodes.length);
    const circles = roots.map(r => ({ root: r, r: ATLAS.area * Math.sqrt(r.count) / 3 + 26 }));
    packSiblings(circles);
    const seats = new Map();
    for (const c of circles)
      seats.set(c.root.name, { x: c.x, y: c.y, name: c.root.name, room: c.r });

    const bodies = [];
    D.nodes.forEach((n, i) => {
      const seat = seats.get(topOf(n.domain)) || { x: 0, y: 0, room: 40 };
      const s = spiral(i, N, (seat.room || 40) * 0.7);
      bodies.push({
        n, uid: n.uid, m: 1, seat,
        x: seat.x + s.x, y: seat.y + s.y,
        r: 2.4 + Math.sqrt(D.degree.get(n.uid) || 0) * 1.6,
      });
    });
    const byUid = new Map(bodies.map(b => [b.uid, b]));
    const pairs = D.edges
      .map(e => ({ a: byUid.get(e.from_uid), b: byUid.get(e.to_uid), e }))
      .filter(l => l.a && l.b);

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
  get progress() {
    if (!this.sim.settled) return this.sim.progress * 0.9;
    const total = this.roots.length || 1;
    const left = this.queue ? this.queue.length : 0;
    return 0.9 + 0.1 * ((total - left) / total);
  }

  /* Busy stays true through the pass that settles the physics, and then
     through one territory per frame: tracing every coast in a single pass is a
     60ms frame at thirty roots, which is a dropped one. Each is drawn as it
     lands, so the land appears rather than arriving all at once. */
  step() {
    if (!this.sim.settled) {
      this.sim.run(SLICE_MS);
      this.queue = null;
      this.coasts = null;
      return true;
    }
    if (!this.coasts) { this.queue = this.roots.slice(); this.coasts = []; }
    if (!this.queue.length) return false;
    const one = this._coast(this.queue.shift());
    if (one) this.coasts.push(one);
    if (!this.queue.length) {
      this.coasts.sort((a, b) => b.n - a.n);
      return false;
    }
    return true;
  }

  /* The settle ceiling stops the PHYSICS, which is the part that could spin
     forever. What is left of the tracing is finite work and it runs here: a
     territory with no coastline is dots on nothing. */
  halt() {
    this.sim.halt();
    if (!this.coasts) { this.queue = this.roots.slice(); this.coasts = []; }
    while (this.queue.length) {
      const one = this._coast(this.queue.shift());
      if (one) this.coasts.push(one);
    }
    this.coasts.sort((a, b) => b.n - a.n);
  }

  /* The density field of one root domain, and its level set as a coastline. */
  _coast(root) {
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

  box() { return robustBounds(this.bodies, 90, 0.004); }

  locate(uid) {
    const b = this.byUid.get(uid);
    return b ? { x: b.x, y: b.y, r: b.r } : null;
  }

  draw(ctx, cam, env) {
    const { palette, show } = env;
    const K = cam.k;
    const board = new LabelBoard(ctx);
    board.reset(env.taken);
    const sc = p => cam.toScreen(p.x, p.y);
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

    /* A road between two territories is bowed off the straight line, so two
       roads between the same pair do not lie on top of each other; a road
       inside one stays a short straight thing. */
    if (show.links) {
      const local = [], trunk = new Map();
      for (const l of this.links) {
        const a = sc(l.a), b = sc(l.b);
        if (!l.cross) { local.push([a, b]); continue; }
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        const dx = b.x - a.x, dy = b.y - a.y;
        const c = { x: mx - dy * 0.11, y: my + dx * 0.11 };
        const type = l.e.relation_type;
        if (!trunk.has(type)) trunk.set(type, []);
        trunk.get(type).push([a, c, b]);
      }
      lines(ctx, local, palette.tree, 1, lit ? 0.2 : 1);
      for (const [type, set] of trunk)
        lines(ctx, set, relColor(palette, type), type === 'relates_to' ? 1.1 : 1.8,
              lit ? 0.18 : 0.9);
    }
    if (lit) {
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

    if (!show.titles) return;
    if (this.coasts) {
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
    if (K <= 0.5) return;
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
  hit(x, y, cam) {
    const r = 13 / cam.k;
    let best = null, bd = r * r;
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
export const ARRANGEMENTS = [
  { id: 'hubs', make: env => new Hubs(env), note: 'g.mode.hubs.note', settles: true },
  { id: 'pack', make: env => new Pack(env), note: 'g.mode.pack.note', settles: false },
  { id: 'atlas', make: env => new Atlas(env), note: 'g.mode.atlas.note', settles: true },
];

export const DEFAULT_MODE = ARRANGEMENTS[0].id;

export const arrangement = id =>
  ARRANGEMENTS.find(a => a.id === id) || ARRANGEMENTS[0];
