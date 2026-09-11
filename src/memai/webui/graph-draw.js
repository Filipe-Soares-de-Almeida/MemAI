/* The canvas kit the graph arrangements draw with, written for the frame.

   Fills are BATCHED: one path per colour-and-alpha with every dot of that
   pair in it, then one fill. Ten thousand dots in six colours cost six fills.
   Below a pixel and a half a dot is drawn as a RECT -- an arc that small
   antialiases into the same grey square and the rect is several times
   cheaper.

   Everything here takes SCREEN coordinates: the camera has already been
   applied, so a radius is in CSS pixels and a stroke width is the width it
   will read as. */

/* `hex` at alpha `a`. An `rgb(...)` or `rgba(...)` string is handed back
   unchanged, since it carries its own alpha. */
export const hexA = (hex, a) => {
  const s = String(hex || '');
  if (s.startsWith('rgb')) return s;
  const h = s.replace('#', '');
  const n = h.length === 3 ? h.split('').map(c => c + c).join('') : h;
  const v = parseInt(n, 16);
  if (!Number.isFinite(v)) return s;
  return `rgba(${(v >> 16) & 255},${(v >> 8) & 255},${v & 255},${a})`;
};

/* Filled dots. Each item needs {sx, sy, r, fill} and may carry its own
   `alpha`; `alpha` here is the default for the ones that do not. */
export function dots(ctx, items, alpha = 1) {
  const buckets = new Map();
  for (const it of items) {
    const a = it.alpha == null ? alpha : it.alpha;
    if (a <= 0) continue;
    const key = `${it.fill}|${a}`;
    let bucket = buckets.get(key);
    if (!bucket) { bucket = { fill: it.fill, alpha: a, items: [] }; buckets.set(key, bucket); }
    bucket.items.push(it);
  }
  for (const bucket of buckets.values()) {
    ctx.globalAlpha = bucket.alpha;
    ctx.fillStyle = bucket.fill;
    ctx.beginPath();
    for (const it of bucket.items) {
      const r = it.r;
      if (r < 1.5) ctx.rect(it.sx - r, it.sy - r, r * 2, r * 2);
      else { ctx.moveTo(it.sx + r, it.sy); ctx.arc(it.sx, it.sy, r, 0, 6.2832); }
    }
    ctx.fill();
  }
  ctx.globalAlpha = 1;
}

/* `color` at alpha `a`, whichever notation it arrived in: a hex from this
   file's own palette or an `rgba()` from a CSS custom property. The alpha
   REPLACES whatever the colour carried. */
export function withAlpha(color, a) {
  const s = String(color || '').trim();
  const m = s.match(/^rgba?\(([^)]+)\)$/i);
  if (m) {
    const p = m[1].split(/[,\s/]+/).filter(Boolean).map(Number);
    return `rgba(${p[0] || 0},${p[1] || 0},${p[2] || 0},${a})`;
  }
  return hexA(s, a);
}

/* One relation, faint at the end it leaves and bright at the end it points
   to. It costs a gradient per line, so it is for the few under the pointer
   and never for the whole store. */
export function gradLine(ctx, a, b, color, width) {
  const g = ctx.createLinearGradient(a.x, a.y, b.x, b.y);
  g.addColorStop(0, withAlpha(color, 0.12));
  g.addColorStop(1, withAlpha(color, 0.95));
  ctx.strokeStyle = g;
  ctx.lineWidth = width;
  ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.moveTo(a.x, a.y);
  ctx.lineTo(b.x, b.y);
  ctx.stroke();
}

/* A ring around a dot: the hovered one, the selected one, the link source. */
export function ring(ctx, x, y, r, color, w = 2) {
  ctx.beginPath();
  ctx.arc(x, y, r, 0, 6.2832);
  ctx.strokeStyle = color;
  ctx.lineWidth = w;
  ctx.stroke();
}

/* Many polylines that share a colour and a width, in one path. */
export function lines(ctx, polys, color, width, alpha = 1) {
  if (!polys.length) return;
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineCap = 'round';
  ctx.beginPath();
  for (const p of polys) {
    if (p.length < 2) continue;
    ctx.moveTo(p[0].x, p[0].y);
    for (let i = 1; i < p.length; i++) ctx.lineTo(p[i].x, p[i].y);
  }
  ctx.stroke();
  ctx.globalAlpha = 1;
}

/* The bounding box of positioned things, padded. `key` is the property
   holding each one's radius. */
export function bounds(items, pad = 0, key = 'r') {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const it of items) {
    const r = (key && it[key]) || 0;
    if (it.x - r < x0) x0 = it.x - r;
    if (it.x + r > x1) x1 = it.x + r;
    if (it.y - r < y0) y0 = it.y - r;
    if (it.y + r > y1) y1 = it.y + r;
  }
  if (!Number.isFinite(x0)) return { x0: -100, y0: -100, x1: 100, y1: 100 };
  return { x0: x0 - pad, y0: y0 - pad, x1: x1 + pad, y1: y1 + pad };
}

/* The box holding MOST of an arrangement: `q` of each axis is cut off either
   end. A force layout flings the odd loose memory far out, and the absolute
   box of that frames the store as a thumbnail in one corner. The outlier is
   still drawn, outside the initial frame. */
export function robustBounds(items, pad = 0, q = 0.012) {
  if (items.length < 20) return bounds(items, pad);
  const xs = items.map(i => i.x).sort((a, b) => a - b);
  const ys = items.map(i => i.y).sort((a, b) => a - b);
  const lo = Math.floor(items.length * q), hi = Math.ceil(items.length * (1 - q)) - 1;
  return { x0: xs[lo] - pad, x1: xs[hi] + pad, y0: ys[lo] - pad, y1: ys[hi] + pad };
}

/* Nearest item within `radius` WORLD units, over a grid so the pointer costs
   the same at ten thousand nodes. */
export class Picker {
  constructor(items, cell = 40) {
    this.cell = cell;
    this.g = new Map();
    for (const it of items) {
      const k = `${Math.floor(it.x / cell)},${Math.floor(it.y / cell)}`;
      if (!this.g.has(k)) this.g.set(k, []);
      this.g.get(k).push(it);
    }
  }

  at(x, y, radius) {
    const c = this.cell;
    const reach = Math.ceil(radius / c);
    const i0 = Math.floor(x / c), j0 = Math.floor(y / c);
    let best = null, bd = radius * radius;
    for (let j = j0 - reach; j <= j0 + reach; j++)
      for (let i = i0 - reach; i <= i0 + reach; i++) {
        const cell = this.g.get(`${i},${j}`);
        if (!cell) continue;
        for (const it of cell) {
          const d = (it.x - x) ** 2 + (it.y - y) ** 2;
          if (d < bd) { bd = d; best = it; }
        }
      }
    return best;
  }
}

const CELL = 48;

/* Labels that do not land on each other or under the floating chrome.

   Candidates arrive in priority order and any that would collide with
   something already placed is refused. The chrome rectangles come from the
   page: where a panel sits is a stylesheet's decision, and no drawing can
   work it out. */
export class LabelBoard {
  constructor(ctx) {
    this.ctx = ctx;
    this.grid = new Map();
    this.placed = [];
  }

  /* `taken` is [{x, y, w, h}] in canvas coordinates. */
  reset(taken = []) {
    this.grid.clear();
    this.placed.length = 0;
    for (const r of taken) this.occupy(r);
  }

  occupy(r) {
    const i0 = Math.floor(r.x / CELL), i1 = Math.floor((r.x + r.w) / CELL);
    const j0 = Math.floor(r.y / CELL), j1 = Math.floor((r.y + r.h) / CELL);
    for (let j = j0; j <= j1; j++)
      for (let i = i0; i <= i1; i++) {
        const k = `${i},${j}`;
        if (!this.grid.has(k)) this.grid.set(k, []);
        this.grid.get(k).push(r);
      }
  }

  free(r) {
    const i0 = Math.floor(r.x / CELL), i1 = Math.floor((r.x + r.w) / CELL);
    const j0 = Math.floor(r.y / CELL), j1 = Math.floor((r.y + r.h) / CELL);
    for (let j = j0; j <= j1; j++)
      for (let i = i0; i <= i1; i++) {
        const cell = this.grid.get(`${i},${j}`);
        if (!cell) continue;
        for (const o of cell)
          if (r.x < o.x + o.w && o.x < r.x + r.w && r.y < o.y + o.h && o.y < r.y + r.h)
            return false;
      }
    return true;
  }

  /* Trim `text` to `maxW` pixels in the font already set on the context. */
  _fit(text, maxW) {
    const ctx = this.ctx;
    let label = String(text == null ? '' : text);
    if (ctx.measureText(label).width <= maxW) return label;
    let lo = 1, hi = label.length;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (ctx.measureText(label.slice(0, mid) + '…').width <= maxW) lo = mid; else hi = mid - 1;
    }
    return label.slice(0, lo).trimEnd() + '…';
  }

  /* Draw `text` anchored at (x, y), on the first side of `sides` that fits.
     Returns whether it was drawn. */
  draw(text, x, y, opts = {}) {
    const {
      font, color, halo, gap = 9, sides = ['right', 'left', 'top', 'bottom'],
      maxW = 220, haloWidth = 3.5,
    } = opts;
    const ctx = this.ctx;
    ctx.font = font;
    const label = this._fit(text, maxW);
    if (!label) return false;
    const w = ctx.measureText(label).width;
    const h = 13;
    for (const side of sides) {
      let bx, by;
      if (side === 'right') { bx = x + gap; by = y - h / 2; }
      else if (side === 'left') { bx = x - gap - w; by = y - h / 2; }
      else if (side === 'top') { bx = x - w / 2; by = y - gap - h; }
      else { bx = x - w / 2; by = y + gap; }
      const rect = { x: bx - 2, y: by - 1, w: w + 4, h: h + 2 };
      if (!this.free(rect)) continue;
      this.occupy(rect);
      this.placed.push(rect);
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      const ty = by + h / 2;
      if (haloWidth > 0) {
        ctx.lineWidth = haloWidth;
        ctx.strokeStyle = halo;
        ctx.lineJoin = 'round';
        ctx.strokeText(label, bx, ty);
      }
      ctx.fillStyle = color;
      ctx.fillText(label, bx, ty);
      return true;
    }
    return false;
  }

  /* A label that appears wherever it lands: the name of a region, which has
     no second side to try. */
  force(text, x, y, opts = {}) {
    const {
      font, color, halo, align = 'center', baseline = 'middle',
      haloWidth = 4, maxW = 1e9,
    } = opts;
    const ctx = this.ctx;
    ctx.font = font;
    const label = this._fit(text, maxW);
    if (!label) return null;
    const w = ctx.measureText(label).width;
    ctx.textAlign = align;
    ctx.textBaseline = baseline;
    if (haloWidth > 0) {
      ctx.lineWidth = haloWidth;
      ctx.strokeStyle = halo;
      ctx.lineJoin = 'round';
      ctx.strokeText(label, x, y);
    }
    ctx.fillStyle = color;
    ctx.fillText(label, x, y);
    const bx = align === 'center' ? x - w / 2 : align === 'right' ? x - w : x;
    const rect = { x: bx - 2, y: y - 8, w: w + 4, h: 16 };
    this.occupy(rect);
    return rect;
  }
}
