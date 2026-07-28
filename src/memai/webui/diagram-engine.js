/* MemAI · diagram editor (vanilla JS ES module, no build step).

   A canvas editor for one type='diagram' memory. Deliberately NOT a
   force layout: every node arrives with x/y from the store, so this file
   has no layout algorithm and no fallback seeding. A node without
   coordinates is a server bug, not something to paper over here — which
   is also why two people looking at the same diagram, and a diagram
   nobody has ever opened, all show the same picture.

   Consequences of that split:
     · no physics loop. The canvas redraws when something changes
       (requestDraw), so an idle editor costs nothing.
     · dragging is optimistic: the box follows the mouse and the new
       coordinates are handed to hooks.onMove, which persists them. If
       that write fails the caller re-fetches and calls setData().
     · "auto-arrange" is a server round-trip (hooks.onRelayout), never a
       layout computed here.

   All DOM outside the canvas (the inspector, toolbar wiring, toasts)
   belongs to app.js and reaches this class through `hooks`. */

const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const cssVar = name =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();

export const NODE_SHAPES = ['start', 'step', 'decision', 'io', 'end'];

/* Box geometry, in the same abstract units the server lays out with
   (LAYOUT_COL_W 300 / LAYOUT_ROW_H 200), so a stored arrangement has
   room to breathe without any scaling here. Deliberately smaller than one
   layout cell: the air between boxes is what keeps a long flow readable. */
/* These four must match db.NODE_DEFAULT_W / NODE_DEFAULT_H /
   DECISION_DEFAULT_H / NODE_MIN_* -- the server lays out in the same units
   and clamps a resize to the same bounds. */
const NODE_W = 170;
const NODE_H = 48;
const DECISION_H = 66;
const NODE_MIN_W = 110, NODE_MAX_W = 560;
const NODE_MIN_H = 34, NODE_MAX_H = 340;
const IO_SKEW = 14;          /* the lean on an input/output parallelogram */
const HANDLE = 7;            /* half-side of a corner resize grip, world units */
const SNAP_PX = 7;           /* how near an axis has to be to pull a card onto it */
const ARROW_GAP = 5;         /* air between the box edge and the arrow tip */
const ORTH_STUB = 26;        /* how far a right-angled edge leaves its box */
const ORTH_RADIUS = 11;      /* corner rounding on a right-angled edge */
/* Two boxes almost -- but not exactly -- in line used to get a full Z
   detour for an offset of a few units, and two rounded corners that close
   together bow into an S. Below this offset the run is drawn as one
   segment: a couple of degrees off vertical reads as straight, a wiggle
   reads as a mistake. */
const ORTH_SNAP = 18;
const EDGE_PICK_PX = 11;     /* how near the pointer must be to grab a line */

export const ROUTINGS = ['orthogonal', 'curved'];
/* Label metrics live in world units alongside the box metrics above, so a
   label occupies the same fraction of its box at every zoom level. Both
   are multiplied by the diagram's stored font scale. */
const LABEL_PX = 12;
const LABEL_LH = 14;
const BADGE_PX = 10;
export const FONT_SCALES = [0.8, 1, 1.25, 1.6, 2];

/* canvas `font` takes a literal font stack -- it does not resolve the
   CSS custom properties the rest of the UI styles with */
const FONT_UI = "'Roboto', 'Segoe UI', system-ui, sans-serif";
const FONT_MONO = "'Roboto Mono', ui-monospace, Consolas, monospace";

const clampTo = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/* A node draws at its own size when it has one, its shape's default when
   it does not -- same rule as db.node_box, font scale included. Without
   that scale here, asking for bigger text would only truncate the label:
   the card it has to fit in would not have changed. */
const nodeSize = (n, fontScale = 1) => ({
  w: clampTo(Number(n.w) || NODE_W * fontScale, NODE_MIN_W, NODE_MAX_W),
  h: clampTo(Number(n.h) || (n.shape === 'decision' ? DECISION_H : NODE_H) * fontScale,
             NODE_MIN_H, NODE_MAX_H),
});

export class DiagramEditor {
  constructor(canvas, data, hooks = {}) {
    this.cv = canvas;
    this.cx = canvas.getContext('2d');
    this.hooks = hooks;
    this.scale = 1;
    this.tx = 0;
    this.ty = 0;
    this.drag = null;
    this.sizing = null;
    this.guides = null;
    this.fontScale = 1;
    this.pan = null;
    this.moved = false;
    this.hover = null;
    this.selected = null;
    this.connectMode = false;
    this.connectFrom = null;
    this.destroyed = false;
    this._frame = 0;
    /* set whenever the lane/detour solution below is out of date */
    this.routesDirty = true;
    /* Read-only until asked otherwise. Reading a flow is the common case,
       and a stray drag while reading silently rewrites a stored position
       everyone else sees -- panning, zooming and selecting stay available. */
    this.readOnly = hooks.readOnly !== false;
    this.routing = ROUTINGS.includes(hooks.routing) ? hooks.routing : 'orthogonal';
    this.hoverLabel = null;

    this.readTheme();
    /* resize() before the first fit(): fit needs the canvas size, and a
       fit that runs against an unmeasured canvas silently does nothing */
    this.resize();
    this.setData(data, { fit: true });

    this._down = e => this.onDown(e);
    this._move = e => this.onMove(e);
    this._up = () => this.onUp();
    this._wheel = e => this.onWheel(e);
    this._click = e => this.onClick(e);
    this._context = e => this.onContext(e);
    /* the pointer can leave the canvas without a last mousemove inside it --
       straight onto the inspector, or onto a dialog -- and the hover tip has
       nothing left to dismiss it */
    this._leave = () => {
      this.hooks.tipHide?.();
      if (this.hover || this.hoverLabel) { this.hover = null; this.hoverLabel = null; this.requestDraw(); }
    };
    this._resize = () => { this.resize(); this.requestDraw(); };

    canvas.addEventListener('mousedown', this._down);
    canvas.addEventListener('mousemove', this._move);
    canvas.addEventListener('wheel', this._wheel, { passive: false });
    canvas.addEventListener('click', this._click);
    canvas.addEventListener('contextmenu', this._context);
    canvas.addEventListener('mouseleave', this._leave);
    addEventListener('mouseup', this._up);

    /* Three overlapping triggers, on purpose. The stage changes size
       without the window ever resizing -- a vertical scrollbar appearing
       takes ~10px off it -- so a window-only listener misses that; but a
       ResizeObserver is the only one of the three that cannot be verified
       in a headless pane, so it is not relied on alone. The check inside
       requestDraw is the backstop that needs no events at all. */
    addEventListener('resize', this._resize);
    if (typeof ResizeObserver === 'function') {
      this._ro = new ResizeObserver(this._resize);
      this._ro.observe(canvas.parentElement);
    }

    this.requestDraw();
  }

  destroy() {
    this.destroyed = true;
    cancelAnimationFrame(this._frame);
    this.cv.removeEventListener('mousedown', this._down);
    this.cv.removeEventListener('mousemove', this._move);
    this.cv.removeEventListener('wheel', this._wheel);
    this.cv.removeEventListener('click', this._click);
    this.cv.removeEventListener('contextmenu', this._context);
    this.cv.removeEventListener('mouseleave', this._leave);
    removeEventListener('mouseup', this._up);
    removeEventListener('resize', this._resize);
    this._ro?.disconnect();
  }

  readTheme() {
    this.colNode = cssVar('--t-diagram') || '#009688';
    this.colSurface = cssVar('--surface') || '#1e1e1e';
    this.colInk = cssVar('--ink') || 'rgba(255,255,255,.87)';
    this.colInk2 = cssVar('--ink-2') || 'rgba(255,255,255,.6)';
    this.colLine = cssVar('--line') || 'rgba(255,255,255,.12)';
    this.colAccent = cssVar('--accent') || '#bb86fc';
    this.colWarn = cssVar('--warn') || '#ffd54f';
  }

  /* ── data ──────────────────────────────────────────────────────── */

  setData(data, { fit = false } = {}) {
    this.data = data;
    this.fontScale = clampTo(Number(data.font_scale) || 1, 0.7, 2.5);
    this.nodes = (data.nodes || []).map(n => {
      const shape = NODE_SHAPES.includes(n.shape) ? n.shape : 'step';
      return {
        key: n.key,
        label: n.label || '',
        note: n.note || '',
        shape,
        x: n.x,
        y: n.y,
        sized: n.w != null || n.h != null,   /* has a size of its own */
        ...nodeSize({ ...n, shape }, this.fontScale),
      };
    });
    this.byKey = Object.fromEntries(this.nodes.map(n => [n.key, n]));
    this.edges = (data.edges || []).filter(e => this.byKey[e.from] && this.byKey[e.to]);
    this.linkCount = {};
    for (const l of data.links || []) {
      this.linkCount[l.node_key] = (this.linkCount[l.node_key] || 0) + 1;
    }
    if (this.selected && !this.byKey[this.selected]) this.selected = null;
    this.connectFrom = null;
    this.hover = null;
    this.sizing = null;
    this.guides = null;
    /* structural, so compute once here rather than per frame -- dragging
       redraws on every mousemove and cannot change reachability */
    this.orphans = new Set(this.orphanKeys());
    /* before the first fit, not only per draw: fit has to know how far the
       lanes reach or it frames the boxes and cuts the corridors off */
    this.laneEdges();
    this.routesDirty = false;
    if (fit) this.fit();
    this.requestDraw();
  }

  /* Lanes: corridors down the side of the diagram, for edges that cannot be
     drawn between their ends without running over what sits in between.

     Every loop closer needs one by definition -- it travels back up the
     page. So does any long forward edge: a branch that dead-ends into the
     terminal step eight rows below was drawn straight down the middle
     column, through eight boxes and along every other line in that column,
     which is most of what "the arrows are tangled" looks like.

     Alternating sides and stacking lanes matters: one shared bow direction
     and magnitude piles a routine's loops on top of each other.

     `detours` is the expensive half: for every forward edge that would
     cross a box it re-routes the edge against up to ~22 candidate
     detours, and each of those tests the whole route against every other
     box. That is fine once, and far too much per mousemove, so a drag
     asks for it to be skipped -- see draw(). */
  laneEdges({ detours = true } = {}) {
    const maxX = Math.max(...this.nodes.map(n => n.x + n.w / 2), 0);
    const minX = Math.min(...this.nodes.map(n => n.x - n.w / 2), 0);
    let taken = 0;
    let outRight = 0, outLeft = 0;
    const assign = e => {
      const a = this.byKey[e.from], b = this.byKey[e.to];
      const side = taken % 2 === 0 ? 1 : -1;
      const lane = Math.floor(taken / 2);
      const clearance = side > 0 ? maxX - Math.max(a.x, b.x) : Math.min(a.x, b.x) - minX;
      const reach = 70 + lane * 45;
      e.lane = side;
      e.bow = side * (Math.max(clearance, 0) + reach);
      if (side > 0) outRight = Math.max(outRight, reach);
      else outLeft = Math.max(outLeft, reach);
      taken++;
    };

    for (const e of this.edges) {
      const a = this.byKey[e.from], b = this.byKey[e.to];
      e.lane = 0;
      e.bow = 0;
      /* A cheap pass keeps whatever detour was solved last, rather than
         snapping every routed line straight the moment a drag starts:
         slightly stale beats a diagram that redraws itself under the
         pointer. The next full pass corrects it. */
      if (detours) e.via = null;
      /* `back` is geometry: does this edge currently run UP the canvas, so
         it needs a lane and enters from below. `loops` is the graph saying
         the edge closes a cycle, and that is what gets drawn dashed -- the
         two used to be one flag, which is why dragging a card above its
         source turned an ordinary edge dashed. */
      e.back = b.y <= a.y;
    }
    /* loop closers first, so their lanes stay nearest the diagram: a reader
       looking for the retry path finds it in the same place every time */
    for (const e of this.edges) if (e.back) assign(e);
    /* A forward edge that would cross a box tries to get out of the way
       WITHOUT leaving the flow first: move the crossbar into a clear gap,
       or run up a corridor just past the obstacle. Sending every one of
       them to the margin is what a hand-arranged flow punishes -- on a
       34-step one, 16 forward edges collide, and 16 detours around the
       whole diagram read worse than the collisions did. */
    for (const e of this.edges) {
      if (e.back || !detours) continue;
      if (!this.routeHitsABox(e)) continue;
      let cleared = false;
      for (const via of this.detours(e)) {
        e.via = via;
        if (!this.routeHitsABox(e)) { cleared = true; break; }
      }
      if (!cleared) { e.via = null; assign(e); }
    }

    /* what fit() has to leave room for, so a lane is not drawn off-screen */
    this.laneSpan = { left: minX - outLeft, right: maxX + outRight };
  }

  /* Detours to try, nearest first. Crossbars come before corridors: moving
     the sideways leg of a Z keeps the edge between its own two cards, while
     a corridor takes it around them. */
  detours(e) {
    const a = this.byKey[e.from], b = this.byKey[e.to];
    const midY = (a.y + b.y) / 2;
    const loY = Math.min(a.y, b.y), hiY = Math.max(a.y, b.y);
    const bars = [];
    const corridors = [];
    for (const n of this.nodes) {
      for (const y of [n.y - n.h / 2 - ORTH_STUB, n.y + n.h / 2 + ORTH_STUB]) {
        if (y > loY && y < hiY) bars.push({ crossY: y });
      }
      for (const x of [n.x - n.w / 2 - ORTH_STUB * 1.5, n.x + n.w / 2 + ORTH_STUB * 1.5]) {
        corridors.push({ corridor: x });
      }
    }
    bars.sort((u, v) => Math.abs(u.crossY - midY) - Math.abs(v.crossY - midY));
    const near = x => Math.min(Math.abs(x - a.x), Math.abs(x - b.x));
    corridors.sort((u, v) => near(u.corridor) - near(v.corridor));
    return [...bars.slice(0, 10), ...corridors.slice(0, 12)];
  }

  /* Would this edge, routed between its ends, be drawn over a box that is
     not one of those ends? Sampled rather than solved: a bounding-box
     reject throws out almost every pair, and a handful of points along
     what survives is enough to catch a run down an occupied column. */
  routeHitsABox(e) {
    const { pts, curve } = this.route(e);
    const flat = DiagramEditor.flatten(pts, curve);
    for (const n of this.nodes) {
      if (n.key === e.from || n.key === e.to) continue;
      for (let i = 1; i < flat.length; i++) {
        if (DiagramEditor.segHitsBox(flat[i - 1], flat[i], n)) return true;
      }
    }
    return false;
  }

  static segHitsBox(p, q, n, pad = 6) {
    const x0 = n.x - n.w / 2 - pad, x1 = n.x + n.w / 2 + pad;
    const y0 = n.y - n.h / 2 - pad, y1 = n.y + n.h / 2 + pad;
    if (Math.max(p.x, q.x) < x0 || Math.min(p.x, q.x) > x1) return false;
    if (Math.max(p.y, q.y) < y0 || Math.min(p.y, q.y) > y1) return false;
    /* an axis-aligned leg -- which is every leg of a right-angled route --
       is fully decided by the two range checks above, so the sampling
       below only ever runs for a straight diagonal or a flattened curve */
    if (Math.abs(p.x - q.x) < 0.01 || Math.abs(p.y - q.y) < 0.01) return true;
    const steps = 24;
    for (let i = 0; i <= steps; i++) {
      const t = i / steps;
      const x = p.x + (q.x - p.x) * t, y = p.y + (q.y - p.y) * t;
      if (x >= x0 && x <= x1 && y >= y0 && y <= y1) return true;
    }
    return false;
  }

  /* Steps the flow cannot reach from its start. Almost always a missing
     edge, so the canvas marks them instead of leaving them looking normal. */
  orphanKeys() {
    const start = this.nodes.find(n => n.shape === 'start');
    if (!start) return this.nodes.map(n => n.key);
    const out = {};
    for (const e of this.edges) (out[e.from] = out[e.from] || []).push(e.to);
    const reached = new Set([start.key]);
    const queue = [start.key];
    while (queue.length) {
      for (const to of out[queue.shift()] || []) {
        if (!reached.has(to)) { reached.add(to); queue.push(to); }
      }
    }
    return this.nodes.filter(n => !reached.has(n.key)).map(n => n.key);
  }

  /* ── viewport ──────────────────────────────────────────────────── */

  resize() {
    /* clientWidth/Height, not getBoundingClientRect: the canvas is
       inset:0 inside the stage, so it fills the PADDING box -- measuring
       the border box would size it a border wider than the space it has. */
    const stage = this.cv.parentElement;
    const dpr = devicePixelRatio || 1;
    this.w = stage.clientWidth;
    this.h = stage.clientHeight;
    this.cv.width = this.w * dpr;
    this.cv.height = this.h * dpr;
    this.cx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  /* Fit into the part of the stage that is actually free. The toolbar and
     the hint strip float ON the canvas, so a plain centre-and-scale parks
     the first row of a re-arranged flow behind the buttons -- the caller
     reports how much room they take (hooks.insets) and the fit centres in
     what is left, not in the whole stage. */
  fit() {
    if (!this.nodes.length || !this.w) return;
    const xs = this.nodes.flatMap(n => [n.x - n.w / 2, n.x + n.w / 2]);
    const ys = this.nodes.flatMap(n => [n.y - n.h / 2, n.y + n.h / 2]);
    if (this.laneSpan) xs.push(this.laneSpan.left, this.laneSpan.right);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const pad = 22;
    const ins = this.hooks.insets?.() || {};
    const top = pad + (ins.top || 0), bottom = pad + (ins.bottom || 0);
    const left = pad + (ins.left || 0), right = pad + (ins.right || 0);
    const roomW = Math.max(40, this.w - left - right);
    const roomH = Math.max(40, this.h - top - bottom);
    this.scale = Math.min(1.4, Math.min(roomW / (maxX - minX), roomH / (maxY - minY)));
    this.tx = left + roomW / 2 - (minX + maxX) / 2 * this.scale;
    this.ty = top + roomH / 2 - (minY + maxY) / 2 * this.scale;
    this.requestDraw();
  }

  toWorld(e) {
    const r = this.cv.getBoundingClientRect();
    return {
      x: (e.clientX - r.left - this.tx) / this.scale,
      y: (e.clientY - r.top - this.ty) / this.scale,
    };
  }

  static inside(n, p) {
    const dx = Math.abs(p.x - n.x), dy = Math.abs(p.y - n.y);
    /* a decision is drawn as a diamond, so hit-test the diamond */
    if (n.shape === 'decision') return dx / (n.w / 2) + dy / (n.h / 2) <= 1;
    return dx <= n.w / 2 && dy <= n.h / 2;
  }

  nodeAt(p) {
    for (let i = this.nodes.length - 1; i >= 0; i--) {
      if (DiagramEditor.inside(this.nodes[i], p)) return this.nodes[i];
    }
    return null;
  }

  /* ── interaction ───────────────────────────────────────────────── */

  setRouting(mode) {
    this.routing = ROUTINGS.includes(mode) ? mode : 'orthogonal';
    /* which edges cross a box depends on how they are drawn */
    this.routesDirty = true;
    this.requestDraw();
  }

  setReadOnly(on) {
    this.readOnly = on;
    if (on) {
      this.connectMode = false;
      this.connectFrom = null;
      this.drag = null;
      this.sizing = null;
      this.guides = null;
      this.cv.classList.remove('linkmode');
    }
    this.requestDraw();
  }

  onDown(e) {
    if (this.connectMode) return;
    const p = this.toWorld(e);
    /* a grip wins over the card under it: the grips sit ON the outline, so
       the card is always under them too */
    const grip = this.readOnly ? null : this.handleAt(p);
    if (grip) {
      const node = this.byKey[this.selected];
      /* the opposite corner stays put, so a resize does not walk the card */
      this.sizing = {
        node, grip,
        fx: node.x - grip.sx * node.w / 2,
        fy: node.y - grip.sy * node.h / 2,
      };
      return;
    }
    const n = this.readOnly ? null : this.nodeAt(p);
    if (n) {
      this.drag = { node: n, dx: n.x - p.x, dy: n.y - p.y };
    } else {
      /* still pannable while read-only: moving the view is not editing */
      this.pan = { x: e.clientX - this.tx, y: e.clientY - this.ty };
      this.cv.classList.add('dragging');
    }
  }

  onMove(e) {
    if (this.sizing) {
      const p = this.toWorld(e);
      const { node, fx, fy } = this.sizing;
      const w = clampTo(Math.abs(p.x - fx), NODE_MIN_W, NODE_MAX_W);
      const h = clampTo(Math.abs(p.y - fy), NODE_MIN_H, NODE_MAX_H);
      node.w = w;
      node.h = h;
      node.x = fx + Math.sign(p.x - fx || 1) * w / 2;
      node.y = fy + Math.sign(p.y - fy || 1) * h / 2;
      node.sized = true;
      this.moved = true;
      this.hooks.tipHide?.();
      this.requestDraw();
      return;
    }
    if (this.drag) {
      const p = this.toWorld(e);
      const snapped = this.snapToNeighbours(
        this.drag.node, p.x + this.drag.dx, p.y + this.drag.dy);
      this.drag.node.x = snapped.x;
      this.drag.node.y = snapped.y;
      this.guides = snapped.guides;
      this.moved = true;
      this.hooks.tipHide?.();
      this.requestDraw();
      return;
    }
    if (this.pan) {
      this.tx = e.clientX - this.pan.x;
      this.ty = e.clientY - this.pan.y;
      this.moved = true;
      this.hooks.tipHide?.();
      this.requestDraw();
      return;
    }
    const world = this.toWorld(e);
    const grip = this.readOnly ? null : this.handleAt(world);
    const n = this.nodeAt(world);
    const lab = (!n && !this.readOnly) ? this.labelAt(world) : null;
    if (n !== this.hover || lab !== this.hoverLabel) {
      this.hover = n; this.hoverLabel = lab; this.requestDraw();
    }
    this.cv.style.cursor = this.connectMode ? 'crosshair'
      : grip ? (grip.id === 'nw' || grip.id === 'se' ? 'nwse-resize' : 'nesw-resize')
      : lab ? 'text'
      : n ? (this.readOnly ? 'pointer' : 'grab') : 'grab';
    if (n && n.note) {
      this.hooks.tipShow?.(
        `<b>${esc(n.key)}</b><br>${esc(n.note.slice(0, 220))}`, e.clientX, e.clientY);
    } else {
      this.hooks.tipHide?.();
    }
  }

  onUp() {
    /* Clearing state is not enough: the last frame painted was the one WITH
       the alignment guides on it, and nothing else here asks for a repaint,
       so a guide stayed drawn across the whole canvas after the drag ended. */
    const painted = !!this.guides?.length || !!(this.drag || this.sizing);
    /* the geometry has stopped moving, so the full lane/detour solve is
       worth paying for again -- see draw() */
    if (this.drag || this.sizing) this.routesDirty = true;
    if (this.drag) {
      const { node } = this.drag;
      this.drag = null;
      this.guides = null;
      /* optimistic: the box already moved, persistence catches up */
      this.hooks.onMove?.({ [node.key]: { x: Math.round(node.x), y: Math.round(node.y) } });
    }
    if (this.sizing) {
      const { node } = this.sizing;
      this.sizing = null;
      /* size rides the same call as the position: both are geometry, and
         resizing from a corner moves the centre too */
      this.hooks.onMove?.({ [node.key]: {
        x: Math.round(node.x), y: Math.round(node.y),
        w: Math.round(node.w), h: Math.round(node.h) } });
    }
    this.guides = null;
    this.pan = null;
    this.cv.classList.remove('dragging');
    if (painted) this.requestDraw();
  }

  /* Alignment snap. A dragged card pulls onto another card's centre line
     once it comes within a few SCREEN pixels, so two steps can be put in
     the same column or the same row without eyeballing it -- and the guide
     line says which card it lined up with. */
  snapToNeighbours(node, x, y) {
    const tol = SNAP_PX / this.scale;
    const guides = [];
    let onX = null, onY = null;
    for (const m of this.nodes) {
      if (m === node) continue;
      if (Math.abs(m.x - x) <= tol && (onX === null || Math.abs(m.x - x) < Math.abs(onX - x))) onX = m.x;
      if (Math.abs(m.y - y) <= tol && (onY === null || Math.abs(m.y - y) < Math.abs(onY - y))) onY = m.y;
    }
    if (onX !== null) { x = onX; guides.push({ axis: 'x', at: onX }); }
    if (onY !== null) { y = onY; guides.push({ axis: 'y', at: onY }); }
    return { x, y, guides };
  }

  drawGuides() {
    const { cx } = this;
    if (!this.guides?.length) return;
    const left = -this.tx / this.scale, right = (this.w - this.tx) / this.scale;
    const top = -this.ty / this.scale, bottom = (this.h - this.ty) / this.scale;
    cx.save();
    cx.strokeStyle = this.colAccent;
    cx.lineWidth = 1 / this.scale;
    cx.setLineDash([6 / this.scale, 5 / this.scale]);
    for (const g of this.guides) {
      cx.beginPath();
      if (g.axis === 'x') { cx.moveTo(g.at, top); cx.lineTo(g.at, bottom); }
      else { cx.moveTo(left, g.at); cx.lineTo(right, g.at); }
      cx.stroke();
    }
    cx.restore();
  }

  onWheel(e) {
    e.preventDefault();
    const r = this.cv.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const f = e.deltaY < 0 ? 1.13 : 1 / 1.13;
    const ns = Math.min(3, Math.max(0.15, this.scale * f));
    this.tx = mx - (mx - this.tx) * (ns / this.scale);
    this.ty = my - (my - this.ty) * (ns / this.scale);
    this.scale = ns;
    this.requestDraw();
  }

  onClick(e) {
    if (this.moved) { this.moved = false; return; }
    const world = this.toWorld(e);
    const n = this.nodeAt(world);
    /* the condition written on a line is edited by clicking that text --
       checked before the nodes, since a label never sits inside a box */
    if (!n && !this.readOnly && !this.connectMode) {
      const lab = this.labelAt(world);
      if (lab) { this.hooks.onEditEdgeLabel?.(lab); return; }
    }
    if (this.connectMode) {
      if (!n) return;
      if (!this.connectFrom) {
        this.connectFrom = n;
        this.hooks.onConnectProgress?.(n);
      } else if (n !== this.connectFrom) {
        const from = this.connectFrom;
        this.connectFrom = null;
        this.hooks.onConnect?.(from.key, n.key);
      }
      this.requestDraw();
      return;
    }
    this.selected = n ? n.key : null;
    this.requestDraw();
    this.hooks.onSelect?.(n || null);
  }

  /* Right-click. Whatever is under the pointer decides what the menu can
     offer, so app.js gets the hit and not the coordinates alone. A line
     is picked by the LINE, not by its label -- which is the only way to
     reach an edge that has no label yet. */
  onContext(e) {
    if (!this.hooks.onContextMenu) return;
    e.preventDefault();
    const world = this.toWorld(e);
    const node = this.nodeAt(world);
    const edge = node ? null : (this.labelAt(world) || this.edgeAt(world));
    if (node && node.key !== this.selected) {
      this.selected = node.key;
      this.requestDraw();
      this.hooks.onSelect?.(node);
    }
    this.hooks.onContextMenu({ world, x: e.clientX, y: e.clientY, node, edge });
  }

  /* Begin a connection with one end already chosen (the context menu route
     in; the toolbar button leaves both ends to be clicked). */
  startConnectFrom(key) {
    const n = this.byKey[key];
    if (this.readOnly || !n) return;
    this.toggleConnectMode(true);
    this.connectFrom = n;
    this.hooks.onConnectProgress?.(n);
    this.requestDraw();
  }

  toggleConnectMode(on = !this.connectMode) {
    if (this.readOnly) on = false;
    this.connectMode = on;
    this.connectFrom = null;
    this.cv.classList.toggle('linkmode', on);
    this.requestDraw();
    this.hooks.onConnectProgress?.(null);
  }

  select(key) {
    this.selected = this.byKey[key] ? key : null;
    this.requestDraw();
  }

  /* ── drawing ───────────────────────────────────────────────────── */

  requestDraw() {
    if (this.destroyed || this._frame) return;
    this._frame = requestAnimationFrame(() => {
      this._frame = 0;
      if (this.destroyed) return;
      /* self-heal: re-measure if the stage moved under us since the last
         draw, so a missed observer tick costs one blurry frame at worst
         instead of leaving the backing store permanently wrong */
      const stage = this.cv.parentElement;
      if (stage.clientWidth !== this.w || stage.clientHeight !== this.h) this.resize();
      this.draw();
    });
  }

  /* The four points an edge may attach to: the centre of each side.
     Per shape, because the old approach -- clip a ray from the centre to
     the bounding rectangle -- lands in empty space beside a diamond,
     whose sides do not follow that rectangle at all. */
  static anchors(n) {
    const hw = n.w / 2, hh = n.h / 2;
    if (n.shape === 'io') {
      /* Top and bottom attach on the card's CENTRE LINE, not at the middle
         of those two edges. The parallelogram leans, so the middle of its
         top edge sits half a skew right of centre and the middle of its
         bottom edge half a skew left -- 14 units apart. A line arriving
         from above and the line leaving underneath then came in and out at
         different x, and a step you just pass through looked kinked.
         Centre is still ON both edges (the lean is smaller than the card),
         and it is where the flow reads as passing straight through.

         The slanted sides are different: their middle IS half a lean in,
         and nothing lines up against them. */
      const s = IO_SKEW / 2;
      return {
        top: { x: n.x, y: n.y - hh }, bottom: { x: n.x, y: n.y + hh },
        left: { x: n.x - hw + s, y: n.y }, right: { x: n.x + hw - s, y: n.y },
      };
    }
    /* rectangle, stadium and diamond all attach at the middle of a side --
       and for a diamond that middle IS its vertex */
    return {
      top: { x: n.x, y: n.y - hh }, bottom: { x: n.x, y: n.y + hh },
      left: { x: n.x - hw, y: n.y }, right: { x: n.x + hw, y: n.y },
    };
  }

  /* Which side each end leaves from, taken from where the boxes sit
     relative to each other: mostly-vertical runs use top/bottom, mostly
     horizontal ones left/right. An edge routed through a corridor always
     leaves and enters vertically -- it turns sideways in the gap BETWEEN
     two rows, where there is nothing to run over, rather than out of the
     side of a box across whatever else shares that row. */
  static sides(a, b, viaCorridor) {
    const dx = b.x - a.x, dy = b.y - a.y;
    if (viaCorridor) return dy >= 0 ? ['bottom', 'top'] : ['top', 'bottom'];
    if (Math.abs(dy) >= Math.abs(dx)) return dy >= 0 ? ['bottom', 'top'] : ['top', 'bottom'];
    return dx >= 0 ? ['right', 'left'] : ['left', 'right'];
  }

  /* How far along its own side an anchor may be nudged, as [lo, hi] in the
     axis that side runs along -- or null for a side that cannot take one.

     This exists because "the middle of the side" and "in line with the card
     below" are not the same point. A parallelogram leans, so the middle of
     its bottom edge sits half a skew off its centre line, and a run into the
     card underneath came out a couple of degrees off vertical: a visibly
     crooked line in a drawing where everything else is square. A diamond's
     vertex is one point and cannot move; a stadium's left and right are the
     apex of a curve, so those cannot either. */
  static slideSpan(n, side) {
    const hw = n.w / 2, hh = n.h / 2, inset = 12;
    const vertical = side === 'top' || side === 'bottom';
    if (n.shape === 'decision') return null;
    if (n.shape === 'io') {
      if (!vertical) return null;                 /* the slanted sides */
      const lo = side === 'top' ? n.x - hw + IO_SKEW : n.x - hw;
      const hi = side === 'top' ? n.x + hw : n.x + hw - IO_SKEW;
      return [lo + inset, hi - inset];
    }
    const round = n.shape === 'start' || n.shape === 'end' ? hh : 10;
    if (vertical) return [n.x - hw + round + inset, n.x + hw - round - inset];
    if (round === hh) return null;                /* a stadium's round cap */
    return [n.y - hh + round + inset, n.y + hh - round - inset];
  }

  /* The anchor's coordinate moved towards `to` along its side, if the side
     allows it and the move stays small. null when it cannot line up. */
  static slide(n, side, from, to) {
    const span = DiagramEditor.slideSpan(n, side);
    if (!span || span[0] >= span[1]) return null;
    if (Math.abs(to - from) > ORTH_SNAP) return null;
    const landed = Math.min(span[1], Math.max(span[0], to));
    return Math.abs(landed - to) < 0.01 ? landed : null;
  }

  /* Push an anchor outwards so the arrow tip stops short of the box. */
  static offset(p, side, by) {
    if (side === 'top') return { x: p.x, y: p.y - by };
    if (side === 'bottom') return { x: p.x, y: p.y + by };
    if (side === 'left') return { x: p.x - by, y: p.y };
    return { x: p.x + by, y: p.y };
  }

  /* The vertical line an edge runs along instead of going straight between
     its ends: a side lane for a loop closer, or a local corridor for one
     that would otherwise cross a box. null for the ordinary case. */
  corridorFor(e) {
    if (e.lane) {
      const a = this.byKey[e.from], b = this.byKey[e.to];
      const reach = Math.abs(e.bow || ORTH_STUB);
      return e.lane > 0 ? Math.max(a.x, b.x) + reach : Math.min(a.x, b.x) - reach;
    }
    return e.via?.corridor ?? null;
  }

  /* The polyline an edge follows, ends included. */
  route(e) {
    const a = this.byKey[e.from], b = this.byKey[e.to];
    const corridorX = this.corridorFor(e);
    const [sideFrom, sideTo] = DiagramEditor.sides(a, b, corridorX !== null);
    const start = DiagramEditor.anchors(a)[sideFrom];
    const end = DiagramEditor.anchors(b)[sideTo];
    const p = DiagramEditor.offset(start, sideFrom, 0);
    const q = DiagramEditor.offset(end, sideTo, ARROW_GAP);

    if (this.routing === 'curved') return { pts: [p, q], curve: this.curveCtrl(p, q, e) };

    const out = [p];
    if (corridorX !== null) {
      /* clear of the box, sideways in the row gap, along the corridor, and
         back into the far end the same way -- five legs, all axis-aligned */
      const leave = DiagramEditor.offset(p, sideFrom, ORTH_STUB);
      const enter = DiagramEditor.offset(q, sideTo, ORTH_STUB);
      out.push(leave, { x: corridorX, y: leave.y },
               { x: corridorX, y: enter.y }, enter, q);
      return { pts: out, curve: null };
    }
    const vertical = sideFrom === 'top' || sideFrom === 'bottom';
    /* Nearly in line? Slide one anchor along its own side so the run is
       EXACTLY straight, instead of drawing the near-miss as a slant. Either
       end will do, so try the tail first and fall back to the head. */
    const axis = vertical ? 'x' : 'y';
    if (Math.abs(p[axis] - q[axis]) > 0.01) {
      const tail = DiagramEditor.slide(a, sideFrom, p[axis], q[axis]);
      if (tail !== null) p[axis] = tail;
      else {
        const head = DiagramEditor.slide(b, sideTo, q[axis], p[axis]);
        if (head !== null) q[axis] = head;
      }
    }
    const across = vertical ? Math.abs(p.x - q.x) : Math.abs(p.y - q.y);
    const along = vertical ? Math.abs(p.y - q.y) : Math.abs(p.x - q.x);
    /* Straight through when the offset is too small to read as a detour, or
       when the run is too short to fit corners in -- both otherwise produce
       the same wiggle. */
    const bar = e.via?.crossY;
    if (bar == null && (across <= ORTH_SNAP || along <= ORTH_RADIUS * 2)) {
      out.push(q);
      return { pts: out, curve: null };
    }
    if (vertical) {
      if (sideTo === 'top' || sideTo === 'bottom') {
        /* Z: down, across, down. The crossbar is normally half way, but a
           detour may have moved it into a gap where nothing is in the way. */
        const midY = bar ?? (p.y + q.y) / 2;
        out.push({ x: p.x, y: midY }, { x: q.x, y: midY });
      } else {
        out.push({ x: p.x, y: q.y });               /* L: down, then across */
      }
    } else if (sideTo === 'left' || sideTo === 'right') {
      const midX = (p.x + q.x) / 2;                /* Z: across, down, across */
      out.push({ x: midX, y: p.y }, { x: midX, y: q.y });
    } else {
      out.push({ x: q.x, y: p.y });                 /* L: across, then down */
    }
    out.push(q);
    return { pts: out, curve: null };
  }

  curveCtrl(p, q, e) {
    const mx = (p.x + q.x) / 2, my = (p.y + q.y) / 2;
    /* the same edges that take a corridor in right-angled mode bow here: a
       straight chord through six boxes is no better drawn as a curve. The
       control point is twice the offset, so the curve itself reaches the
       corridor at its halfway point. */
    const corridor = this.corridorFor(e);
    return corridor === null ? null : { x: mx + (corridor - mx) * 2, y: my };
  }

  /* A quadratic sampled into a polyline, so hit-testing and measuring work
     the same whether an edge was drawn curved or right-angled. */
  static flatten(pts, curve) {
    if (!curve) return pts;
    const [p, q] = pts;
    const out = [];
    for (let i = 0; i <= 12; i++) {
      const t = i / 12, u = 1 - t;
      out.push({
        x: u * u * p.x + 2 * u * t * curve.x + t * t * q.x,
        y: u * u * p.y + 2 * u * t * curve.y + t * t * q.y,
      });
    }
    return out;
  }

  static distToSeg(p, a, b) {
    const vx = b.x - a.x, vy = b.y - a.y;
    const len2 = vx * vx + vy * vy;
    const t = len2 ? Math.max(0, Math.min(1, ((p.x - a.x) * vx + (p.y - a.y) * vy) / len2)) : 0;
    return Math.hypot(p.x - (a.x + vx * t), p.y - (a.y + vy * t));
  }

  /* The edge whose LINE passes under a world point. Routed fresh rather
     than read off the last frame, so a line can be picked at the position
     it is actually drawn at even mid-drag. */
  edgeAt(p) {
    const tol = EDGE_PICK_PX / this.scale;
    let best = null, bestD = Infinity;
    for (const e of this.edges) {
      if (!this.byKey[e.from] || !this.byKey[e.to]) continue;
      const { pts, curve } = this.route(e);
      const flat = DiagramEditor.flatten(pts, curve);
      for (let i = 1; i < flat.length; i++) {
        const d = DiagramEditor.distToSeg(p, flat[i - 1], flat[i]);
        if (d < bestD) { bestD = d; best = e; }
      }
    }
    return bestD <= tol ? best : null;
  }

  /* Point half way along a polyline, for placing the label. */
  static midpoint(pts) {
    let total = 0;
    const segs = [];
    for (let i = 1; i < pts.length; i++) {
      const len = Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
      segs.push(len); total += len;
    }
    let walked = 0;
    for (let i = 0; i < segs.length; i++) {
      if (walked + segs[i] >= total / 2) {
        const f = segs[i] ? (total / 2 - walked) / segs[i] : 0;
        return { x: pts[i].x + (pts[i + 1].x - pts[i].x) * f,
                 y: pts[i].y + (pts[i + 1].y - pts[i].y) * f };
      }
      walked += segs[i];
    }
    return pts[0];
  }

  /* `grow` traces the outline `grow` units OUTSIDE the shape, which is how
     the selection ring is drawn without transforming the canvas mid-path.

     A parallel offset, not a bigger bounding box. Scaling half-width and
     half-height by the same amount is not the same curve: on a diamond,
     whose left and right vertices are acute, it leaves the ring touching
     the shape at the tips while standing clear along the sides -- which is
     exactly what "the border isn't going around the card" looked like. */
  shapePath(n, grow = 0) {
    const { cx } = this;
    cx.beginPath();
    if (n.shape === 'decision') {
      /* the edge is x/a + y/b = 1, so offsetting it by `grow` scales the
         whole diamond by 1 + grow*|(1/a, 1/b)| -- at the sharp tips that
         reaches much further out along the axis than `grow` itself */
      const a = n.w / 2, b = n.h / 2;
      const k = 1 + grow * Math.hypot(1 / a, 1 / b);
      const hw = a * k, hh = b * k;
      cx.moveTo(n.x, n.y - hh);
      cx.lineTo(n.x + hw, n.y);
      cx.lineTo(n.x, n.y + hh);
      cx.lineTo(n.x - hw, n.y);
      cx.closePath();
      return;
    }
    if (n.shape === 'io') {
      /* A slanted side stays PARALLEL only if the lean grows with the
         height: keeping the skew fixed tilts the ring slightly against the
         card, so the gap ends up wider at one end than the other. The lines
         are x + m*y = c, so an offset of `grow` moves c by grow*√(1+m²) --
         and the extra m*grow puts the widened height back on the width. */
      const m = IO_SKEW / (n.h || 1);
      const hh = n.h / 2 + grow;
      const hw = n.w / 2 + grow * (Math.sqrt(1 + m * m) + m);
      const skew = IO_SKEW * (hh / (n.h / 2));
      cx.moveTo(n.x - hw + skew, n.y - hh);
      cx.lineTo(n.x + hw, n.y - hh);
      cx.lineTo(n.x + hw - skew, n.y + hh);
      cx.lineTo(n.x - hw, n.y + hh);
      cx.closePath();
      return;
    }
    const hw = n.w / 2 + grow, hh = n.h / 2 + grow;
    const box = [n.x - hw, n.y - hh, hw * 2, hh * 2];
    /* a rounded corner offsets into a rounded corner of radius r + grow;
       leaving the radius alone is what made the ring's corners read as a
       different shape from the card's */
    const terminal = n.shape === 'start' || n.shape === 'end';
    if (cx.roundRect) cx.roundRect(...box, terminal ? hh : 10 + grow);
    else cx.rect(...box);
  }

  /* Half the shape's width at a given offset from its centre line. What
     keeps a corner marker inside a shape whose corners are not where its
     bounding box says they are. */
  static halfWidthAt(n, dy) {
    const hw = n.w / 2, hh = n.h / 2;
    const t = Math.min(1, Math.abs(dy) / (hh || 1));
    if (n.shape === 'decision') return hw * (1 - t);
    if (n.shape === 'io') return hw - IO_SKEW * (dy + hh) / (n.h || 1);
    if (n.shape === 'start' || n.shape === 'end') {
      /* a stadium: the caps are semicircles of radius hh */
      return hw - hh + Math.sqrt(Math.max(0, hh * hh - dy * dy));
    }
    return hw;   /* a rounded rectangle, near enough for a 10-unit radius */
  }

  wrap(text, maxWidth, maxLines) {
    const { cx } = this;
    const words = String(text).split(/\s+/).filter(Boolean);
    const lines = [];
    let line = '';
    for (const word of words) {
      const next = line ? `${line} ${word}` : word;
      if (cx.measureText(next).width <= maxWidth || !line) {
        line = next;
      } else {
        lines.push(line);
        line = word;
        if (lines.length === maxLines) break;
      }
    }
    if (lines.length < maxLines && line) lines.push(line);
    if (lines.length === maxLines) {
      const consumed = lines.join(' ').length;
      if (consumed < String(text).trim().length) {
        lines[maxLines - 1] = this.clamp(`${lines[maxLines - 1]}…`, maxWidth);
      }
    }
    /* A single word with no break opportunity is forced onto its line
       above, so it can still be wider than the box -- and a routine or
       function name is exactly that: one long CamelCase token. Clamp
       every line by character, not just the last one. */
    return lines.map(l => this.clamp(l, maxWidth));
  }

  /* Shorten one line by characters until it fits, ellipsis included. */
  clamp(line, maxWidth) {
    const { cx } = this;
    if (cx.measureText(line).width <= maxWidth) return line;
    let cut = line.replace(/…$/, '');
    while (cut && cx.measureText(`${cut}…`).width > maxWidth) cut = cut.slice(0, -1);
    return `${cut}…`;
  }

  /* `hot` is an edge of the selected step -- drawn over the others, in the
     selection colour, with its condition readable at any zoom. A step with
     six lines through it is unreadable otherwise: this is what says which
     of them are its own. `dim` is everything else while that lasts. */
  drawEdge(e, { hot = false, dim = false } = {}) {
    const { cx } = this;
    const a = this.byKey[e.from], b = this.byKey[e.to];
    if (!a || !b) { e.hit = null; return; }
    /* dashed + amber says ONE thing: this edge closes a cycle. It comes
       from the graph (see db.loop_edges), not from which way the line
       happens to point after someone dragged a card. */
    const back = !!e.loops;
    const { pts, curve } = this.route(e);
    const from = pts[0], to = pts[pts.length - 1];
    const strong = back ? this.colWarn : (hot ? this.colAccent : this.colInk2);

    cx.save();
    if (dim) cx.globalAlpha = 0.3;
    cx.strokeStyle = hot ? strong : (back ? this.colWarn : this.colLine);
    cx.lineWidth = (hot ? 2.6 : back ? 1.4 : 1.7) / this.scale;
    if (back) cx.setLineDash([5 / this.scale, 4 / this.scale]);
    cx.beginPath();
    cx.moveTo(from.x, from.y);
    if (curve) {
      cx.quadraticCurveTo(curve.x, curve.y, to.x, to.y);
    } else if (pts.length === 2) {
      cx.lineTo(to.x, to.y);
    } else {
      /* arcTo rounds each corner for us, so a right-angled run reads as a
         drawn line rather than a staircase of hard pixels. The radius is
         capped at half of each neighbouring leg: a fixed radius on a short
         leg eats past the next corner and bows the whole run. */
      for (let i = 1; i < pts.length - 1; i++) {
        const prev = pts[i - 1], here = pts[i], next = pts[i + 1];
        const r = Math.min(ORTH_RADIUS,
          Math.hypot(here.x - prev.x, here.y - prev.y) / 2,
          Math.hypot(next.x - here.x, next.y - here.y) / 2);
        cx.arcTo(here.x, here.y, next.x, next.y, Math.max(0, r));
      }
      cx.lineTo(to.x, to.y);
    }
    cx.stroke();
    cx.setLineDash([]);

    /* arrowhead along whatever the last stretch was heading */
    const prev = curve || pts[pts.length - 2];
    const tanX = to.x - prev.x, tanY = to.y - prev.y;
    const d = Math.hypot(tanX, tanY) || 1;
    const ux = tanX / d, uy = tanY / d;
    const s = hot ? 11 : 9;
    cx.beginPath();
    cx.moveTo(to.x, to.y);
    cx.lineTo(to.x - ux * s - uy * s * 0.45, to.y - uy * s + ux * s * 0.45);
    cx.lineTo(to.x - ux * s + uy * s * 0.45, to.y - uy * s - ux * s * 0.45);
    cx.closePath();
    cx.fillStyle = strong;
    cx.fill();

    e.hit = null;
    /* a selected step's own conditions stay legible even zoomed out, which
       is the whole point of the highlight */
    if (!e.label || (this.scale <= 0.4 && !hot)) { cx.restore(); return; }
    const at = curve
      ? { x: (from.x + 2 * curve.x + to.x) / 4, y: (from.y + 2 * curve.y + to.y) / 4 }
      : DiagramEditor.midpoint(pts);
    /* world units, same reason as node labels -- except on a highlighted
       edge, which is sized to stay readable at whatever zoom you are at */
    const base = BADGE_PX * this.fontScale;
    const px = hot ? Math.max(base, (base + 1) / this.scale) : base;
    cx.font = `${px}px ${FONT_MONO}`;
    const text = e.label.length > 34 ? `${e.label.slice(0, 33)}…` : e.label;
    const wide = cx.measureText(text).width;
    const pad = px * 0.4;
    const box = { x: at.x - wide / 2 - pad, y: at.y - px * 0.8,
                  w: wide + pad * 2, h: px * 1.6 };
    cx.fillStyle = this.colSurface;
    cx.fillRect(box.x, box.y, box.w, box.h);
    if (!this.readOnly || hot) {
      /* an outline says the text itself is the thing you click to change */
      cx.strokeStyle = (e === this.hoverLabel || hot) ? this.colAccent : this.colLine;
      cx.lineWidth = 1 / this.scale;
      cx.strokeRect(box.x, box.y, box.w, box.h);
    }
    cx.fillStyle = hot ? this.colInk : (back ? this.colWarn : this.colInk2);
    cx.textAlign = 'center';
    cx.textBaseline = 'middle';
    cx.fillText(text, at.x, at.y);
    cx.restore();
    e.hit = box;                         /* for labelAt(), see onClick */
  }

  /* The edge whose label sits under a world point, if any. */
  labelAt(p) {
    for (const e of this.edges) {
      const h = e.hit;
      if (h && p.x >= h.x && p.x <= h.x + h.w && p.y >= h.y && p.y <= h.y + h.h) return e;
    }
    return null;
  }

  drawNode(n, orphan) {
    const { cx } = this;
    const terminal = n.shape === 'start' || n.shape === 'end';
    this.shapePath(n);
    cx.fillStyle = terminal ? this.colNode : this.colSurface;
    cx.fill();
    this.shapePath(n);
    cx.strokeStyle = orphan ? this.colWarn : this.colNode;
    cx.lineWidth = (orphan ? 2 : 1.6) / this.scale;
    if (orphan) cx.setLineDash([4 / this.scale, 3 / this.scale]);
    cx.stroke();
    cx.setLineDash([]);

    if (n.key === this.selected || n === this.connectFrom) {
      this.shapePath(n, 6);
      cx.strokeStyle = this.colAccent;
      cx.lineWidth = 1.6 / this.scale;
      if (n === this.connectFrom) cx.setLineDash([4 / this.scale, 3 / this.scale]);
      cx.stroke();
      cx.setLineDash([]);
    }

    if (this.scale < 0.3) return;
    /* World units, NOT 11.5/scale. The boxes are in world units, so a font
       sized to stay constant on screen grows relative to its box as you
       zoom out and the label spills over the edges -- which is exactly
       what happened. Scaling with the box keeps text inside it at every
       zoom, at the cost of small text when zoomed far out (hence the
       early return above). The diagram's font scale multiplies both. */
    const px = LABEL_PX * this.fontScale;
    const lh = LABEL_LH * this.fontScale;
    cx.font = `${px}px ${FONT_UI}`;
    cx.fillStyle = terminal ? '#000' : this.colInk;
    cx.textAlign = 'center';
    cx.textBaseline = 'middle';
    /* as many lines as the card is tall enough for: a resized card is only
       worth resizing if the text it was resized for actually appears */
    const maxLines = Math.max(1, Math.floor((n.h - 8) / lh));
    const room = DiagramEditor.halfWidthAt(n, 0) * 2
      - (n.shape === 'decision' ? n.w * 0.32 : 20);
    const lines = this.wrap(n.label, room, maxLines);
    const top = n.y - (lines.length - 1) * lh / 2;
    lines.forEach((line, i) => cx.fillText(line, n.x, top + i * lh));

    /* Attached memories carry a marker, because nothing else on the canvas
       hints at them. A note does NOT: hovering the card shows it, and a
       glyph on most of the cards of a documented flow is just noise. */
    const count = this.linkCount[n.key];
    if (count && this.scale > 0.45) {
      /* save/restore because this is the last thing drawn on a card: the
         font and textAlign set below otherwise leak into the next frame,
         where every consumer happens to set its own -- happens to, which
         is not something to leave a later edit standing on */
      cx.save();
      const bpx = BADGE_PX * this.fontScale;
      const color = terminal ? 'rgba(0,0,0,.6)' : this.colInk2;
      /* inside the SHAPE, not inside its bounding box -- the top-right
         corner of the box is empty space on a diamond and outside the
         curve on a stadium, which is where this used to land */
      const dy = -n.h / 2 + bpx * 0.9;
      const right = n.x + DiagramEditor.halfWidthAt(n, dy) - 5;
      const midY = n.y + dy + bpx / 2;
      cx.font = `${bpx}px ${FONT_MONO}`;
      cx.fillStyle = color;
      cx.textAlign = 'right';
      cx.fillText(String(count), right, midY);
      this.drawLinkMark(right - cx.measureText(String(count)).width - bpx * 0.3, midY, bpx, color);
      cx.restore();
    }

    if (this.resizable(n)) this.drawHandles(n);
  }

  /* The "has memories attached" mark, DRAWN rather than typed.

     It used to be U+21F1, which Roboto does not have and most monospace
     faces do not either: it fell through to whatever the system happened
     to ship, and to a tofu box where nothing did. Canvas cannot use the
     SVG set in core/icons.js, so this is the same idea by hand -- two
     nodes and a tie, matching that set's `graph` icon, which is what a
     linked memory is. Drawn right-to-left from `right`, so it sits beside
     a count of unknown width. */
  drawLinkMark(right, midY, size, color) {
    const { cx } = this;
    const r = size * 0.15;
    const ax = right - size * 0.62, ay = midY + size * 0.22;
    const bx = right - size * 0.1, by = midY - size * 0.24;
    cx.save();
    cx.strokeStyle = color;
    cx.fillStyle = color;
    cx.lineWidth = Math.max(0.5, size * 0.1);
    cx.beginPath();
    cx.moveTo(ax, ay);
    cx.lineTo(bx, by);
    cx.stroke();
    for (const [px, py] of [[ax, ay], [bx, by]]) {
      cx.beginPath();
      cx.arc(px, py, r, 0, 7);
      cx.fill();
    }
    cx.restore();
  }

  /* Corner grips, on the selected card only and only while editing: four
     more things to hit on every card would make dragging the flow harder,
     not easier. */
  resizable(n) {
    return !this.readOnly && !this.connectMode && n.key === this.selected;
  }

  static handles(n) {
    const hw = n.w / 2, hh = n.h / 2;
    return [
      { id: 'nw', x: n.x - hw, y: n.y - hh, sx: -1, sy: -1 },
      { id: 'ne', x: n.x + hw, y: n.y - hh, sx: 1, sy: -1 },
      { id: 'sw', x: n.x - hw, y: n.y + hh, sx: -1, sy: 1 },
      { id: 'se', x: n.x + hw, y: n.y + hh, sx: 1, sy: 1 },
    ];
  }

  drawHandles(n) {
    const { cx } = this;
    const s = HANDLE / Math.max(this.scale, 0.35);   /* stays grabbable zoomed out */
    cx.fillStyle = this.colAccent;
    cx.strokeStyle = this.colSurface;
    cx.lineWidth = 1.4 / this.scale;
    for (const h of DiagramEditor.handles(n)) {
      cx.beginPath();
      cx.rect(h.x - s / 2, h.y - s / 2, s, s);
      cx.fill();
      cx.stroke();
    }
  }

  handleAt(p) {
    const n = this.byKey[this.selected];
    if (!n || !this.resizable(n)) return null;
    const s = HANDLE / Math.max(this.scale, 0.35);
    return DiagramEditor.handles(n).find(h =>
      Math.abs(p.x - h.x) <= s && Math.abs(p.y - h.y) <= s) || null;
  }

  draw() {
    const { cx } = this;
    cx.clearRect(0, 0, this.w, this.h);
    cx.save();
    cx.translate(this.tx, this.ty);
    cx.scale(this.scale, this.scale);

    /* Lanes are geometry, so they have to follow a drag: moving a step can
       turn a forward edge into a loop closer, or park it over a box it now
       runs through. They cannot be cached per node either -- dragging one
       card can make an unrelated edge cross it.

       But the full solve is the most expensive thing in this file and a
       drag redraws on every mousemove, so while something is being moved
       only the cheap half runs (loop closers still get their lanes;
       forward edges are drawn straight, crossings and all). onUp marks the
       routes dirty and the next frame solves them properly. */
    const interacting = !!(this.drag || this.sizing);
    if (this.routesDirty || interacting) {
      this.laneEdges({ detours: !interacting });
      this.routesDirty = interacting;
    }
    /* the selected step's lines go last, so they sit over the ones they
       cross instead of disappearing under them */
    const sel = this.selected;
    const isHot = e => !!sel && (e.from === sel || e.to === sel);
    const cool = sel ? this.edges.filter(e => !isHot(e)) : this.edges;
    for (const e of cool) this.drawEdge(e, { dim: !!sel });
    if (sel) for (const e of this.edges.filter(isHot)) this.drawEdge(e, { hot: true });
    for (const n of this.nodes) this.drawNode(n, this.orphans.has(n.key));
    this.drawGuides();   /* over the cards: it is about where they line up */

    cx.restore();
  }
}
