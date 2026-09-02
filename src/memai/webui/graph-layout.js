/* The 3D arrangement of the relations universe: a force layout that settles
   once and then stops.

   It runs off the main thread (see graph-layout.worker.js), so the numbers in
   here never touch the DOM and every array is typed: `pos` and `vel` hold
   three floats per node, edges are two index arrays, and adjacency is CSR.
   The engine that draws the result is graph-engine.js, which owns the camera
   and the shaders and nothing about where a node belongs.

   There is no live physics. Nothing in the universe is draggable, so the
   arrangement is computed once, uploaded to the GPU once, and the render loop
   after that is pure camera. A new relation re-renders the view, which starts
   a new layout.

   Repulsion runs over a grid of cells one cutoff wide, so a pass costs what
   the layout's own density costs rather than n^2. In three dimensions a cell
   has 26 neighbours, and a scan over every cell pairs each of them with the
   13 that come after it (see AHEAD) -- itself plus those covers every near
   pair exactly once. */

/* alpha below which the arrangement is considered at rest */
export const SETTLE = 0.02;

/* Pair checks to spend on the settle, total, and the pass count that buys.
   What one pass costs is measured on the seeded layout, so a dense store
   trades passes for the checks each of them costs. The budget is a worker's
   to spend: no frame is waiting on it. */
const SETTLE_BUDGET = 9e6;
const MIN_PASSES = 140;
const MAX_PASSES = 900;

/* Repulsion: strength, and the distance beyond which it is zero. The cutoff
   is also the grid cell size, so a node only ever feels its own cell and the
   26 around it. The strength answers to three dimensions: a shell at radius r
   holds r^2 worth of neighbours, so the same push spreads further. */
const REPEL = 2600;
const REPEL_CUT = 215 * 215;

/* Springs: the length a relation settles at, its stiffness, and how much of
   the correction each end gives way to -- a node's share is the weight of the
   OTHER end over the two, so the memory held by seven relations moves least
   and its leaves swing around it. */
const LINK_LEN = 78;
const LINK_K = .014;
const linkShare = (w, other) => 2 * other / (w + other);

/* pull toward the origin, per px of distance, and the fraction of velocity a
   node keeps between passes */
const GRAVITY = .0009;
const DAMP = .85;

/* How far a node may travel in one pass. A memory with no relations has no
   spring holding it, so repulsion is the only force with a brake -- and past
   REPEL_CUT repulsion is off. Capping the step keeps a node inside the range
   where it still answers. */
const MAX_STEP = 5;

/* Seeding: the distance from a parent to its children, and the side of the
   cube of space one node claims when whole components are packed. */
const SEED_SHELL = 86;
const SEED_SPACE = 76;

/* the golden angle, in radians: what spreads siblings around their parent's
   axis and components around the origin */
const GOLD = 2.399963;

/* The widest cone a branch may fan its children into. The root fans over the
   whole sphere; below it a cap this wide already reads as a branch, and wider
   folds children back over their own parent. */
const CONE_MAX = Math.PI * 0.42;

/* The 13 neighbouring cells that come after a cell in a scan over all of
   them: every (di,dj,dk) in {-1,0,1}^3 that is lexicographically positive. */
const AHEAD = [];
for (let dk = -1; dk <= 1; dk++) {
  for (let dj = -1; dj <= 1; dj++) {
    for (let di = -1; di <= 1; di++) {
      if (dk > 0 || (dk === 0 && dj > 0) || (dk === 0 && dj === 0 && di > 0)) {
        AHEAD.push(di, dj, dk);
      }
    }
  }
}

/* An orthonormal pair perpendicular to `axis`, written into `out` as six
   floats. The seed spins siblings around the axis in this basis. */
function basis(ax, ay, az, out) {
  /* the smallest component of the axis makes the most stable cross product */
  let ux = 0, uy = 0, uz = 0;
  const bx = Math.abs(ax), by = Math.abs(ay), bz = Math.abs(az);
  if (bx <= by && bx <= bz) ux = 1;
  else if (by <= bz) uy = 1;
  else uz = 1;
  /* u = axis x pick */
  let px = ay * uz - az * uy, py = az * ux - ax * uz, pz = ax * uy - ay * ux;
  const pl = Math.hypot(px, py, pz) || 1;
  px /= pl; py /= pl; pz /= pl;
  /* v = axis x u, already unit because both are */
  out[0] = px; out[1] = py; out[2] = pz;
  out[3] = ay * pz - az * py;
  out[4] = az * px - ax * pz;
  out[5] = ax * py - ay * px;
}

export class Layout {
  /* `degree`, `ea` and `eb` are per-node and per-edge index arrays; nothing
     in here knows a uid. Edges pointing outside 0..count-1 are the caller's
     mistake and would corrupt the pass, so they are dropped on the way in. */
  constructor(count, degree, ea, eb) {
    this.n = count;
    this.pos = new Float32Array(count * 3);
    this.vel = new Float32Array(count * 3);
    this.wt = new Float32Array(count);
    for (let i = 0; i < count; i++) this.wt[i] = 1 + degree[i];
    this.degree = degree;

    const keep = [];
    for (let e = 0; e < ea.length; e++) {
      const a = ea[e], b = eb[e];
      if (a >= 0 && a < count && b >= 0 && b < count && a !== b) keep.push(e);
    }
    this.ea = new Int32Array(keep.length);
    this.eb = new Int32Array(keep.length);
    keep.forEach((e, i) => { this.ea[i] = ea[e]; this.eb[i] = eb[e]; });
    this.m = keep.length;

    this._csr();
    this.alpha = 1;
    this.passes = 0;
    this.total = 0;
    /* grid scratch, sized once: a pass rewrites these rather than allocating */
    this.cellOf = new Int32Array(count);
    this.order = new Int32Array(count);
    this.cellId = new Map();
    this.cellAt = new Int32Array(0);
    this.cellStart = new Int32Array(0);
    this.ncells = 0;
  }

  /* Adjacency as compressed rows: `adjStart[i]..adjStart[i+1]` indexes
     `adjList` for the neighbours of node i. Built once; the seed walks it and
     nothing else needs it. */
  _csr() {
    const n = this.n, deg = new Int32Array(n + 1);
    for (let e = 0; e < this.m; e++) { deg[this.ea[e]]++; deg[this.eb[e]]++; }
    const start = new Int32Array(n + 1);
    for (let i = 0; i < n; i++) start[i + 1] = start[i] + deg[i];
    const at = start.slice(0, n);
    const list = new Int32Array(start[n]);
    for (let e = 0; e < this.m; e++) {
      list[at[this.ea[e]]++] = this.eb[e];
      list[at[this.eb[e]]++] = this.ea[e];
    }
    this.adjStart = start;
    this.adjList = list;
  }

  /* Starting positions. Each set of memories that relations connect is grown
     as a tree out from its busiest member -- one shell per step away from it,
     children fanned into a cone around the direction their parent arrived
     from -- and the sets are packed biggest first over a golden-angle sphere.
     A relation therefore starts near its rest length instead of across the
     universe, which is what the settle then has passes to polish.

     What sets a set's distance from the origin is the VOLUME its nodes take,
     counted from the middle of its own share. A tree is mostly empty space,
     so claiming the ball its radius spans pushes every later set outward and
     leaves the middle of the universe thin. */
  seed() {
    const comps = this._components();
    comps.sort((a, b) => b.length - a.length);
    const m = comps.length;
    let vol = 0;
    for (let c = 0; c < m; c++) {
      const comp = comps[c];
      this._tree(comp);
      const own = comp.length * SEED_SPACE * SEED_SPACE * SEED_SPACE;
      const r = Math.cbrt(3 * (vol + own / 2) / (4 * Math.PI));
      vol += own;
      /* one point of a Fibonacci sphere per set */
      const y = m > 1 ? 1 - 2 * (c + .5) / m : 0;
      const ring = Math.sqrt(Math.max(0, 1 - y * y));
      const a = c * GOLD;
      const cx = Math.cos(a) * ring * r, cy = y * r, cz = Math.sin(a) * ring * r;
      for (const i of comp) {
        this.pos[i * 3] += cx;
        this.pos[i * 3 + 1] += cy;
        this.pos[i * 3 + 2] += cz;
      }
    }
    /* the pass count comes from what a pass actually costs on the seeded
       layout, and the cooling rate comes from the pass count: alpha lands on
       SETTLE exactly as the last pass ends, at any size */
    this.total = Math.max(MIN_PASSES,
      Math.min(MAX_PASSES, Math.floor(SETTLE_BUDGET / Math.max(1, this.cost()))));
    this.cool = Math.pow(SETTLE, 1 / this.total);
  }

  /* The node indices of each set of memories that relations connect, one
     array per set. A memory with no relations comes back as an array of one. */
  _components() {
    const seen = new Uint8Array(this.n), out = [];
    for (let s = 0; s < this.n; s++) {
      if (seen[s]) continue;
      seen[s] = 1;
      const q = [s];
      for (let h = 0; h < q.length; h++) {
        const u = q[h];
        for (let k = this.adjStart[u]; k < this.adjStart[u + 1]; k++) {
          const v = this.adjList[k];
          if (!seen[v]) { seen[v] = 1; q.push(v); }
        }
      }
      out.push(q);
    }
    return out;
  }

  /* One set of connected memories, as a tree around its busiest member.
     Writes positions LOCAL to the set -- seed() translates them. */
  _tree(comp) {
    let root = comp[0];
    for (const i of comp) if (this.degree[i] > this.degree[root]) root = i;

    /* breadth-first, so a parent is placed before any of its children */
    const order = [root], parent = new Map([[root, -1]]), kids = new Map();
    for (let h = 0; h < order.length; h++) {
      const u = order[h];
      const own = [];
      for (let k = this.adjStart[u]; k < this.adjStart[u + 1]; k++) {
        const v = this.adjList[k];
        if (parent.has(v)) continue;
        parent.set(v, u); own.push(v); order.push(v);
      }
      kids.set(u, own);
    }
    /* the node itself plus everything hanging off it, deepest first: what
       sizes the cone a branch gets */
    const span = new Map();
    for (let h = order.length - 1; h >= 0; h--) {
      const u = order[h];
      let s = 1;
      for (const v of kids.get(u)) s += span.get(v);
      span.set(u, s);
    }

    /* the direction each node arrived from, so its children fan away from it */
    const axis = new Map([[root, [0, 0, 1]]]);
    const half = new Map([[root, Math.PI]]);
    const uv = new Float32Array(6);
    this.pos[root * 3] = this.pos[root * 3 + 1] = this.pos[root * 3 + 2] = 0;

    for (const u of order) {
      const own = kids.get(u);
      if (!own.length) continue;
      const [ax, ay, az] = axis.get(u);
      const ha = half.get(u);
      basis(ax, ay, az, uv);
      const cosHa = Math.cos(ha), below = span.get(u) - 1;
      own.forEach((v, i) => {
        /* uniform over the cap: cos(theta) linear in i, phi by golden angle.
           At half = PI that is the whole sphere, which is what the root wants. */
        const cosT = 1 - (i + .5) / own.length * (1 - cosHa);
        const sinT = Math.sqrt(Math.max(0, 1 - cosT * cosT));
        const phi = i * GOLD;
        const cp = Math.cos(phi), sp = Math.sin(phi);
        const dx = ax * cosT + (uv[0] * cp + uv[3] * sp) * sinT;
        const dy = ay * cosT + (uv[1] * cp + uv[4] * sp) * sinT;
        const dz = az * cosT + (uv[2] * cp + uv[5] * sp) * sinT;
        this.pos[v * 3] = this.pos[u * 3] + dx * SEED_SHELL;
        this.pos[v * 3 + 1] = this.pos[u * 3 + 1] + dy * SEED_SHELL;
        this.pos[v * 3 + 2] = this.pos[u * 3 + 2] + dz * SEED_SHELL;
        axis.set(v, [dx, dy, dz]);
        half.set(v, Math.min(CONE_MAX, Math.max(.18,
          CONE_MAX * Math.sqrt(span.get(v) / Math.max(1, below)))));
      });
    }
  }

  /* Bucket every node into a cell one repulsion cutoff wide. Cells are keyed
     by a flat index over the current bounding box, which is an integer rather
     than a string and cannot collide the way a spatial hash can -- two nodes
     in cells further apart than a neighbour must never be handed to repel().

     Leaves the buckets in `order` (node indices grouped by cell),
     `cellStart` (where each group begins) and `cellAt` (three cell
     coordinates per group), with `cellId` mapping a key to a group. */
  _grid() {
    const n = this.n, size = Math.sqrt(REPEL_CUT), pos = this.pos;
    let minX = Infinity, minY = Infinity, minZ = Infinity;
    let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
    for (let i = 0; i < n; i++) {
      const x = pos[i * 3], y = pos[i * 3 + 1], z = pos[i * 3 + 2];
      if (x < minX) minX = x; if (x > maxX) maxX = x;
      if (y < minY) minY = y; if (y > maxY) maxY = y;
      if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
    }
    const nj = Math.floor((maxY - minY) / size) + 1;
    const nk = Math.floor((maxZ - minZ) / size) + 1;
    this.gmin = [minX, minY, minZ];
    this.gspan = [nj, nk];

    const id = this.cellId;
    id.clear();
    const coords = [];
    for (let i = 0; i < n; i++) {
      const ci = Math.floor((pos[i * 3] - minX) / size);
      const cj = Math.floor((pos[i * 3 + 1] - minY) / size);
      const ck = Math.floor((pos[i * 3 + 2] - minZ) / size);
      const key = (ci * nj + cj) * nk + ck;
      let cid = id.get(key);
      if (cid === undefined) {
        cid = coords.length / 3;
        id.set(key, cid);
        coords.push(ci, cj, ck);
      }
      this.cellOf[i] = cid;
    }
    const ncells = coords.length / 3;
    if (this.cellStart.length !== ncells + 1) this.cellStart = new Int32Array(ncells + 1);
    else this.cellStart.fill(0);
    if (this.cellAt.length !== coords.length) this.cellAt = new Int32Array(coords.length);
    this.cellAt.set(coords);

    const start = this.cellStart;
    for (let i = 0; i < n; i++) start[this.cellOf[i] + 1]++;
    for (let c = 0; c < ncells; c++) start[c + 1] += start[c];
    /* start is now the END of each group; walk it back down as items land */
    const at = start.slice(0, ncells);
    for (let i = 0; i < n; i++) this.order[at[this.cellOf[i]]++] = i;
    this.ncells = ncells;
  }

  /* How many pairs one repulsion pass visits on the layout as it stands. */
  cost() {
    this._grid();
    const { cellStart: start, cellAt: co, cellId: id, ncells } = this;
    const [nj, nk] = this.gspan;
    let pairs = 0;
    for (let c = 0; c < ncells; c++) {
      const len = start[c + 1] - start[c];
      pairs += len * (len - 1) / 2;
      const ci = co[c * 3], cj = co[c * 3 + 1], ck = co[c * 3 + 2];
      for (let o = 0; o < AHEAD.length; o += 3) {
        const other = id.get(((ci + AHEAD[o]) * nj + cj + AHEAD[o + 1]) * nk + ck + AHEAD[o + 2]);
        if (other !== undefined) pairs += len * (start[other + 1] - start[other]);
      }
    }
    return pairs;
  }

  /* One pass of the whole arrangement. Returns false once it is at rest. */
  pass() {
    if (this.alpha < SETTLE) return false;
    const { pos, vel, order, cellStart: start, cellAt: co, cellId: id } = this;
    const [nj, nk] = this.gspan;
    const alpha = this.alpha;

    this._grid();
    for (let c = 0; c < this.ncells; c++) {
      const s = start[c], e = start[c + 1];
      for (let a = s; a < e; a++) {
        for (let b = a + 1; b < e; b++) this._repel(order[a], order[b], alpha);
      }
      const ci = co[c * 3], cj = co[c * 3 + 1], ck = co[c * 3 + 2];
      for (let o = 0; o < AHEAD.length; o += 3) {
        const other = id.get(((ci + AHEAD[o]) * nj + cj + AHEAD[o + 1]) * nk + ck + AHEAD[o + 2]);
        if (other === undefined) continue;
        const os = start[other], oe = start[other + 1];
        for (let a = s; a < e; a++) {
          for (let b = os; b < oe; b++) this._repel(order[a], order[b], alpha);
        }
      }
    }

    for (let k = 0; k < this.m; k++) {
      const a = this.ea[k] * 3, b = this.eb[k] * 3;
      const dx = pos[b] - pos[a], dy = pos[b + 1] - pos[a + 1], dz = pos[b + 2] - pos[a + 2];
      const d = Math.max(1, Math.hypot(dx, dy, dz));
      const f = (d - LINK_LEN) * LINK_K * alpha / d;
      const wa = this.wt[this.ea[k]], wb = this.wt[this.eb[k]];
      const sa = linkShare(wa, wb) * f, sb = linkShare(wb, wa) * f;
      vel[a] += dx * sa; vel[a + 1] += dy * sa; vel[a + 2] += dz * sa;
      vel[b] -= dx * sb; vel[b + 1] -= dy * sb; vel[b + 2] -= dz * sb;
    }

    const g = GRAVITY * alpha;
    for (let i = 0; i < this.n; i++) {
      const o = i * 3;
      vel[o] -= pos[o] * g; vel[o + 1] -= pos[o + 1] * g; vel[o + 2] -= pos[o + 2] * g;
      vel[o] *= DAMP; vel[o + 1] *= DAMP; vel[o + 2] *= DAMP;
      /* escape velocity, capped -- see MAX_STEP */
      const v = Math.hypot(vel[o], vel[o + 1], vel[o + 2]);
      if (v > MAX_STEP) {
        const s = MAX_STEP / v;
        vel[o] *= s; vel[o + 1] *= s; vel[o + 2] *= s;
      }
      pos[o] += vel[o]; pos[o + 1] += vel[o + 1]; pos[o + 2] += vel[o + 2];
    }

    this.alpha *= this.cool;
    this.passes++;
    return this.alpha >= SETTLE;
  }

  /* Push one pair apart, if they are close enough to feel each other. */
  _repel(i, j, alpha) {
    const pos = this.pos, a = i * 3, b = j * 3;
    let dx = pos[a] - pos[b], dy = pos[a + 1] - pos[b + 1], dz = pos[a + 2] - pos[b + 2];
    let d2 = dx * dx + dy * dy + dz * dz;
    if (d2 < 1) {
      dx = Math.random() - .5; dy = Math.random() - .5; dz = Math.random() - .5;
      d2 = 1;
    }
    if (d2 > REPEL_CUT) return;
    const f = REPEL / d2 * alpha / Math.sqrt(d2);
    const vel = this.vel;
    vel[a] += dx * f; vel[a + 1] += dy * f; vel[a + 2] += dz * f;
    vel[b] -= dx * f; vel[b + 1] -= dy * f; vel[b + 2] -= dz * f;
  }

  /* 0..1, for a progress bar. Passes, not alpha: alpha is exponential and a
     bar that runs to 90% in a tenth of the work is a bar that lies. */
  get progress() {
    return this.total ? Math.min(1, this.passes / this.total) : 1;
  }
}
