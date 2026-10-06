/* The 2D force layout: Barnes-Hut quadtree, Plummer softening, no repulsion cutoff (a cutoff
   collapses the cloud to its own scale). Centring translates the centroid, never a spring. */

/* the Barnes-Hut opening angle, squared: 0.9^2 */
const THETA2 = 0.81;

/* Depth is capped and full cells keep a bucket, so coincident bodies cannot recurse forever. */
const MAX_DEPTH = 26;

/* Softening length squared: repulsion divides by `d2 + SOFT2`, capping it smoothly so bodies
   seeded on top of each other do not fly apart. */
const SOFT2 = 16;

export interface Body {
  x: number;
  y: number;
  vx: number;
  vy: number;
  m: number;
  fixed?: boolean;
  seat?: { x: number; y: number } | null;
}

export interface Link { a: Body; b: Body; len?: number | null; k?: number | null }

export interface Force { fx: number; fy: number }

export interface SimOptions {
  charge: number;
  linkK: number;
  linkLen: number;
  damp: number;
  groupK: number;
  center: boolean;
  alpha: number;
  decay: number;
  alphaMin: number;
}

class Quad {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  depth: number;
  kids: Quad[] | null = null;
  body: Body | null = null;
  bucket: Body[] | null = null;
  mass = 0;
  cx = 0;
  cy = 0;

  constructor(x0: number, y0: number, x1: number, y1: number, depth: number) {
    this.x0 = x0; this.y0 = y0; this.x1 = x1; this.y1 = y1; this.depth = depth;
  }

  insert(b: Body): void {
    if (this.bucket) { this.bucket.push(b); return; }
    if (!this.kids) {
      const held = this.body;
      if (!held) { this.body = b; return; }
      const mx = (this.x0 + this.x1) / 2;
      if (this.depth >= MAX_DEPTH || !(mx > this.x0 && mx < this.x1)) {
        this.bucket = [held, b];
        this.body = null;
        return;
      }
      this.body = null;
      this.split();
      this.quadFor(held).insert(held);
    }
    this.quadFor(b).insert(b);
  }

  split(): void {
    const mx = (this.x0 + this.x1) / 2, my = (this.y0 + this.y1) / 2, d = this.depth + 1;
    this.kids = [
      new Quad(this.x0, this.y0, mx, my, d), new Quad(mx, this.y0, this.x1, my, d),
      new Quad(this.x0, my, mx, this.y1, d), new Quad(mx, my, this.x1, this.y1, d),
    ];
  }

  /* the child cell `b` falls in; split() has run */
  quadFor(b: Body): Quad {
    const mx = (this.x0 + this.x1) / 2, my = (this.y0 + this.y1) / 2;
    return this.kids![(b.y >= my ? 2 : 0) + (b.x >= mx ? 1 : 0)];
  }

  summarize(): void {
    if (this.body) { this.mass = this.body.m; this.cx = this.body.x; this.cy = this.body.y; return; }
    if (this.bucket) {
      let m = 0, sx = 0, sy = 0;
      for (const b of this.bucket) { m += b.m; sx += b.x * b.m; sy += b.y * b.m; }
      this.mass = m;
      if (m) { this.cx = sx / m; this.cy = sy / m; }
      return;
    }
    if (!this.kids) return;
    let m = 0, sx = 0, sy = 0;
    for (const k of this.kids) {
      k.summarize();
      if (!k.mass) continue;
      m += k.mass; sx += k.cx * k.mass; sy += k.cy * k.mass;
    }
    this.mass = m;
    if (m) { this.cx = sx / m; this.cy = sy / m; }
  }
}

/* The tree over `bodies`, summarized and ready to be read by `repel`. */
export function buildQuad(bodies: Body[]): Quad | null {
  if (!bodies.length) return null;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const b of bodies) {
    /* a body that has gone non-finite makes every bound NaN and every split
       degenerate: park it at the origin rather than poison the tree */
    if (!Number.isFinite(b.x) || !Number.isFinite(b.y)) { b.x = 0; b.y = 0; b.vx = 0; b.vy = 0; }
    if (b.x < x0) x0 = b.x;
    if (b.x > x1) x1 = b.x;
    if (b.y < y0) y0 = b.y;
    if (b.y > y1) y1 = b.y;
  }
  const span = Math.max(x1 - x0, y1 - y0, 1) * 1.02;
  const q = new Quad(x0 - 1, y0 - 1, x0 - 1 + span + 2, y0 - 1 + span + 2, 0);
  for (const b of bodies) q.insert(b);
  q.summarize();
  return q;
}

function pair(b: Body, o: Body, k: number, out: Force): void {
  if (o === b) return;
  let dx = b.x - o.x, dy = b.y - o.y;
  let d2 = dx * dx + dy * dy;
  if (d2 < 1e-6) {
    const a = Math.random() * 6.2832;
    dx = Math.cos(a); dy = Math.sin(a); d2 = 1;
  }
  const f = (k * o.m) / (d2 + SOFT2), d = Math.sqrt(d2);
  out.fx += (dx / d) * f; out.fy += (dy / d) * f;
}

/* Sum the repulsion a whole tree exerts on `b` into `out`. */
export function repel(q: Quad | null, b: Body, k: number, out: Force): void {
  if (!q || !q.mass) return;
  if (q.body) { pair(b, q.body, k, out); return; }
  if (q.bucket) { for (const o of q.bucket) pair(b, o, k, out); return; }
  const dx = b.x - q.cx, dy = b.y - q.cy;
  const d2 = dx * dx + dy * dy;
  const w = q.x1 - q.x0;
  if (d2 > 1e-9 && (w * w) / d2 < THETA2) {
    const f = (k * q.mass) / (d2 + SOFT2), d = Math.sqrt(d2);
    out.fx += (dx / d) * f; out.fy += (dy / d) * f;
    return;
  }
  if (q.kids) for (const kid of q.kids) repel(kid, b, k, out);
}

/* A simulation over `bodies` ({x, y, m, fixed}) and `links` ({a, b, len, k}). `groupK` pulls
   toward `b.seat`, which must be fixed or the arrangement rides it inward. */
export class Sim {
  bodies: Body[];
  links: Link[];
  o: SimOptions;

  constructor(bodies: Body[], links: Link[], opts: Partial<SimOptions> = {}) {
    this.bodies = bodies;
    this.links = links;
    this.o = {
      charge: 900,        /* repulsion per unit mass */
      linkK: 0.06,        /* spring stiffness */
      linkLen: 44,
      damp: 0.86,
      groupK: 0,          /* cohesion toward a fixed seat */
      center: true,
      alpha: 1, decay: 0.018, alphaMin: 0.02,
      ...opts,
    };
    for (const b of bodies) { b.vx = b.vx || 0; b.vy = b.vy || 0; b.m = b.m || 1; }
  }

  get settled(): boolean { return this.o.alpha <= this.o.alphaMin; }

  get progress(): number {
    const { alpha, alphaMin } = this.o;
    return Math.min(1, Math.max(0, (1 - alpha) / (1 - alphaMin)));
  }

  pass(): void {
    const o = this.o, bodies = this.bodies;
    const tree = buildQuad(bodies);
    const acc = { fx: 0, fy: 0 };
    for (const b of bodies) {
      if (b.fixed) { b.vx = b.vy = 0; continue; }
      acc.fx = 0; acc.fy = 0;
      repel(tree, b, o.charge, acc);
      b.vx += (acc.fx / b.m) * o.alpha;
      b.vy += (acc.fy / b.m) * o.alpha;
    }
    for (const l of this.links) {
      const a = l.a, b = l.b;
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.hypot(dx, dy) || 1e-6;
      const rest = l.len != null ? l.len : o.linkLen;
      const f = ((d - rest) / d) * (l.k != null ? l.k : o.linkK) * o.alpha;
      const ax = dx * f, ay = dy * f;
      const ma = a.m, mb = b.m, sum = ma + mb;
      if (!a.fixed) { a.vx += ax * (mb / sum); a.vy += ay * (mb / sum); }
      if (!b.fixed) { b.vx -= ax * (ma / sum); b.vy -= ay * (ma / sum); }
    }
    if (o.groupK) {
      for (const b of bodies) {
        if (b.fixed || !b.seat) continue;
        b.vx += (b.seat.x - b.x) * o.groupK * o.alpha;
        b.vy += (b.seat.y - b.y) * o.groupK * o.alpha;
      }
    }
    for (const b of bodies) {
      if (b.fixed) continue;
      b.vx *= o.damp; b.vy *= o.damp;
      b.x += b.vx; b.y += b.vy;
    }
    if (o.center) {
      let sx = 0, sy = 0;
      for (const b of bodies) { sx += b.x; sy += b.y; }
      const n = bodies.length || 1;
      const ox = sx / n, oy = sy / n;
      for (const b of bodies) { b.x -= ox; b.y -= oy; }
    }
    o.alpha -= o.decay * o.alpha + 0.0004;
    if (o.alpha < 0) o.alpha = 0;
  }

  /* Passes until settled, until `maxPasses`, or until the millisecond budget
     runs out -- one frame's share of the arrangement. Returns how many ran. */
  run(budgetMs = 12, maxPasses = 400): number {
    const t0 = performance.now();
    let n = 0;
    while (!this.settled && n < maxPasses && performance.now() - t0 < budgetMs) {
      this.pass(); n++;
    }
    return n;
  }

  /* Stop the arrangement where it stands: `settled` reads true from here. */
  halt(): void { this.o.alpha = 0; }
}

/* A phyllotaxis spiral. Seeding from it leaves a force layout no accidental
   symmetry to sit in, and on its own it fills a disc evenly. */
export function spiral(i: number, n: number, radius: number): { x: number; y: number } {
  const g = Math.PI * (3 - Math.sqrt(5));
  const r = radius * Math.sqrt((i + 0.5) / n);
  return { x: Math.cos(i * g) * r, y: Math.sin(i * g) * r };
}
