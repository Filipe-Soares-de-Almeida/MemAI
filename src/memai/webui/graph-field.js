/* A scalar field over points and the isolines that turn it into terrain.

   The atlas arrangement accumulates a domain's memories into a density grid,
   traces the level sets with marching squares and draws the result as a
   coastline. The grid is coarse: a contour that follows every dot is a blob
   per dot, and what has to read is the territory. */

/* Accumulate a Gaussian kernel per point onto a grid. `cell` is the grid step
   in world units, `sigma` the kernel radius; a point's `w` is its weight,
   defaulting to 1. Returns the grid with its origin, size and maximum. */
export function density(points, box, cell, sigma) {
  const cols = Math.max(2, Math.ceil((box.x1 - box.x0) / cell) + 1);
  const rows = Math.max(2, Math.ceil((box.y1 - box.y0) / cell) + 1);
  const g = new Float32Array(cols * rows);
  const reach = Math.ceil((sigma * 2.2) / cell);
  const inv = 1 / (2 * sigma * sigma);
  for (const p of points) {
    const cx = (p.x - box.x0) / cell, cy = (p.y - box.y0) / cell;
    const i0 = Math.max(0, Math.floor(cx - reach)), i1 = Math.min(cols - 1, Math.ceil(cx + reach));
    const j0 = Math.max(0, Math.floor(cy - reach)), j1 = Math.min(rows - 1, Math.ceil(cy + reach));
    const w = p.w == null ? 1 : p.w;
    for (let j = j0; j <= j1; j++)
      for (let i = i0; i <= i1; i++) {
        const dx = (i - cx) * cell, dy = (j - cy) * cell;
        g[j * cols + i] += w * Math.exp(-(dx * dx + dy * dy) * inv);
      }
  }
  let max = 0;
  for (let i = 0; i < g.length; i++) if (g[i] > max) max = g[i];
  return { g, cols, rows, cell, x0: box.x0, y0: box.y0, max };
}

/* Marching squares over one threshold, in world coordinates. Cell edges are
   interpolated linearly, so the coast is smooth rather than stepped. */
export function isolines(f, level) {
  const { g, cols, rows, cell, x0, y0 } = f;
  const at = (i, j) => g[j * cols + i];
  const segs = [];
  const px = (i, j) => ({ x: x0 + i * cell, y: y0 + j * cell });
  const mid = (pa, va, pb, vb) => {
    const t = (level - va) / ((vb - va) || 1e-9);
    return { x: pa.x + (pb.x - pa.x) * t, y: pa.y + (pb.y - pa.y) * t };
  };
  for (let j = 0; j < rows - 1; j++)
    for (let i = 0; i < cols - 1; i++) {
      const v0 = at(i, j), v1 = at(i + 1, j), v2 = at(i + 1, j + 1), v3 = at(i, j + 1);
      const p0 = px(i, j), p1 = px(i + 1, j), p2 = px(i + 1, j + 1), p3 = px(i, j + 1);
      let code = 0;
      if (v0 > level) code |= 1;
      if (v1 > level) code |= 2;
      if (v2 > level) code |= 4;
      if (v3 > level) code |= 8;
      if (code === 0 || code === 15) continue;
      const T = () => mid(p0, v0, p1, v1);
      const R = () => mid(p1, v1, p2, v2);
      const B = () => mid(p3, v3, p2, v2);
      const L = () => mid(p0, v0, p3, v3);
      switch (code) {
        case 1: case 14: segs.push([L(), T()]); break;
        case 2: case 13: segs.push([T(), R()]); break;
        case 3: case 12: segs.push([L(), R()]); break;
        case 4: case 11: segs.push([R(), B()]); break;
        case 6: case 9:  segs.push([T(), B()]); break;
        case 7: case 8:  segs.push([L(), B()]); break;
        case 5:  segs.push([L(), T()], [R(), B()]); break;
        case 10: segs.push([T(), R()], [L(), B()]); break;
      }
    }
  return stitch(segs);
}

/* Join loose segments end to end into polylines.

   Marching squares emits each segment in whatever order its case table
   produced, so a chain has to be followed through EITHER endpoint: indexing
   only the start point leaves most of a contour in fragments, and a fragment
   closed with closePath fills as a crescent.

   A cell edge is shared by exactly two cells, so equal endpoints come out of
   the same arithmetic; rounding the key absorbs the last bit of it. */
function stitch(segs) {
  const key = p => `${Math.round(p.x * 16)},${Math.round(p.y * 16)}`;
  const ends = new Map();
  const add = (k, s) => {
    if (!ends.has(k)) ends.set(k, []);
    ends.get(k).push(s);
  };
  for (const s of segs) { add(key(s[0]), s); add(key(s[1]), s); }

  const used = new Set();
  const out = [];
  const walk = (line, tail) => {
    let at = tail, guard = 0;
    while (guard++ < 100000) {
      const cand = (ends.get(key(at)) || []).find(c => !used.has(c));
      if (!cand) return at;
      used.add(cand);
      const next = key(cand[0]) === key(at) ? cand[1] : cand[0];
      line.push(next);
      at = next;
      if (key(at) === key(line[0])) return at;
    }
    return at;
  };

  for (const s of segs) {
    if (used.has(s)) continue;
    used.add(s);
    const line = [s[0], s[1]];
    const end = walk(line, s[1]);
    if (key(end) !== key(line[0])) {
      /* open at both ends: extend backwards too and keep it as an open line */
      const back = [line[0]];
      walk(back, line[0]);
      back.shift();
      back.reverse();
      line.unshift(...back);
    }
    if (line.length > 4) out.push(line);
  }
  return out;
}

/* Chaikin smoothing: two rounds turn a marching-squares staircase into a
   coastline, within a fraction of a cell of where it was. */
export function smooth(line, rounds = 2) {
  let pts = line;
  for (let r = 0; r < rounds; r++) {
    const next = [pts[0]];
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i], b = pts[i + 1];
      next.push({ x: a.x * 0.75 + b.x * 0.25, y: a.y * 0.75 + b.y * 0.25 },
                { x: a.x * 0.25 + b.x * 0.75, y: a.y * 0.25 + b.y * 0.75 });
    }
    next.push(pts[pts.length - 1]);
    pts = next;
  }
  return pts;
}
