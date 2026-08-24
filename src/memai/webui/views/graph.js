/* The relations graph: a small canvas force layout.

   Repulsion runs over a grid of cells one cutoff wide, so a pass costs what
   the layout's own density costs rather than n²; the pre-paint settle counts
   that on the seeded layout and spends a budget of pair checks on it. The
   layout is computed here and never stored -- unlike the diagram editor,
   whose coordinates come from the store; see diagram.js for that contrast. */

import { $, esc, fmtInt, cssVar, debounce } from '../core/dom.js';
import { api, query } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, failed, tipShow, tipHide, openModal, closeModal, setPressed } from '../core/ui.js';
import { typeTag, typeColor, uidChip, statusTag, confPill, wireCopyChips,
         getDomains, TYPE_ORDER, TYPE_LABEL, typeItems, REL_SUGGEST, relTypeField,
         wireRelTypeField } from '../core/shared.js';
import { pickerFor, wirePicker, fixedItems } from '../core/pick.js';
import { domainPickerHTML, wireDomainPicker } from '../core/domain-picker.js';
import { go, refreshBehind } from '../core/router.js';
import { onTeardown } from '../core/lifecycle.js';
import { openRecord } from './record.js';
import { t } from '../i18n.js';

/* alpha below which the simulation is considered at rest */
const SETTLE = 0.02;
/* Pair checks to spend on the pre-paint settle, total, and the pass count it
   may buy. What one pass costs is measured on the seeded layout, so a dense
   store trades passes for the checks each of them costs. */
const SETTLE_BUDGET = 3e6;
const MIN_PASSES = 120;
const MAX_PASSES = 900;
/* per-frame alpha decay once the graph is live and a kick has stirred it */
const COOL = .996;
/* the golden angle, in radians: the spiral seed() packs groups along, and the
   float phases nodes by */
const GOLD = 2.399963;
/* Repulsion: strength, and the distance beyond which it is zero. The cutoff
   is also the grid cell size the pass buckets nodes into, so a node only ever
   feels the cell it is in and the eight around it. */
const REPEL = 900;
const REPEL_CUT = 250 * 250;
/* Springs: the length a relation settles at, its stiffness, and how much of
   its correction each end gives way to -- a node's share is the weight of the
   OTHER end over the two, so the memory held by seven relations moves least
   and its leaves swing around it. */
const LINK_LEN = 85;
const LINK_K = .012;
const linkShare = (w, other) => 2 * other / (w + other);
/* pull toward the origin, per px of distance, and the fraction of velocity a
   node keeps between passes */
const GRAVITY = .0006;
const DAMP = .86;
/* How far a node may travel in one pass, px. A memory with no relations has
   no spring holding it, so repulsion is the only force with a brake -- and
   past REPEL_CUT repulsion is off. Capping the step keeps a node inside the
   range where it still answers. */
const MAX_STEP = 4;
/* Seeding: the ring spacing between one depth of a group and the next, and
   the side of the square of canvas one node claims in the packing. */
const SEED_RING = 95;
const SEED_SPACE = 86;
/* the four neighbouring cells a bucket looks at: itself plus these covers
   every near pair exactly once */
const AHEAD = [[1, 0], [-1, 1], [0, 1], [1, 1]];
/* The idle float. Purely a drawing offset: `place()` writes px/py and nothing
   ever writes back into x/y, so the layout the reader arranged is the layout
   that stays -- and the physics still stops at rest instead of running
   forever behind a permanent alpha floor.

   The amplitude is in SCREEN px, divided by the zoom on the way in. In world
   units it was invisible at the zoom the view opens at: fit() lands around
   .5x on a few hundred nodes, so a 2px drift arrived as one pixel. Constant
   on screen means the drift reads the same whether you are looking at the
   whole store or three nodes -- with a world-space ceiling, because at a far
   zoom-out "3px of screen" is a large distance in the layout and the nodes
   would swim away from their own edges. */
const FLOAT_SCREEN = 3.4;       /* px on screen, at any zoom */
const FLOAT_MAX = 9;            /* px in world units, the zoom-out ceiling */
const FLOAT_RATE = 9e-4;        /* radians per ms -- one lap in ~7s */
const FLOAT_STEP = 33;          /* ms between idle frames: a 3px drift does not need 60fps */
/* How much of the float a node keeps at each degree: the drift reads as slack
   in the springs, so a memory held by seven relations should have almost
   none, and an unlinked one should have all of it. */
const FLOAT_SLACK = d => 1 - .62 * Math.min(1, d / 5);
/* A gesture warms the nodes AROUND it, not the whole store -- `heat`, below.
   Touching one memory used to raise the global alpha, which re-ran the
   repulsion over every pair: the whole graph shuddered because you nudged a
   node in the corner of it. Reach is a Gaussian in px; a dragged node's own
   relations stay warm however far away they are, because the spring pulling
   them is the whole point of dragging it. */
const HEAT_REACH = 170;
const HEAT_LINKED = .55;
/* Gesture energy: px of drag per unit of alpha, and the ceiling it can reach.
   Proportional on purpose -- a tap is not a rearrangement, so it should not
   look like one. */
const HEAT_PER_PX = 1 / 90;
const HEAT_MAX = .3;
/* what a node the spotlight missed fades to. Low enough that the matches read
   as the figure, high enough that the store's overall shape survives -- the
   whole reason this view is a canvas. */
const DIM = 0.14;

export async function renderGraph(view, params, ctx) {
  const state = {
    status: params.has('status') ? params.get('status') : 'active',
    domain: params.get('domain') || '',
    type: params.get('type') || '',
  };
  const [domains, data] = await Promise.all([
    getDomains().catch(() => []),
    api(`/api/graph?${query(state)}`),
  ]);
  if (ctx.stale()) return;

  const counts = {};
  data.nodes.forEach(n => { counts[n.type] = (counts[n.type] || 0) + 1; });
  const types = typeItems({ any: t('common.allTypes') });

  /* A cap that says nothing is a cap that reads as "this is everything". */
  const capNote = data.truncated
    ? ` · <span style="color:var(--warn)">${t('g.truncated', { n: fmtInt(data.nodes.length), total: fmtInt(data.total) })}</span>`
    : '';

  view.innerHTML = `<div class="anim">
    <div class="view-head">
      <h2 class="view-title">${t('g.title')}</h2>
      <div class="view-sub">${t('g.sub', { n: fmtInt(data.nodes.length), m: fmtInt(data.edges.length) })}${capNote}</div>
    </div>
    <div class="graph-wrap" id="gWrap">
      <!-- A drawing, and labelled as one. The same records are in Memories as
           a list, which is what the label points at: a force layout has no
           reading order to expose, so pretending otherwise would be worse
           than saying where the text version lives. -->
      <canvas id="gCanvas" role="img"
              aria-label="${esc(t('g.canvasAlt', { n: fmtInt(data.nodes.length), m: fmtInt(data.edges.length) }))}"></canvas>
      <div class="graph-controls">
        <!-- The spotlight, and it leads the row because it is the filter that
             answers the most questions. This view exists for the macro read --
             the shape of the store, where the clusters and the loose ends are
             -- so finding one memory must not destroy that shape: matching
             nodes keep their colour and radius and everything else fades.
             Nothing is removed, no request is made, the layout never moves. -->
        <input type="search" id="gFind" class="graph-find" spellcheck="false" autocomplete="off"
               placeholder="${t('g.find')}" aria-label="${t('g.find')}">
        ${domainPickerHTML({ id: 'gDomain', value: state.domain, ariaLabel: t('common.allDomains') })}
        ${pickerFor({ id: 'gType', value: state.type, items: types, ariaLabel: t('common.allTypes') })}
        <div class="seg" role="group" aria-label="${t('mem.status.aria')}">
          <button type="button" data-v="active" aria-pressed="${state.status === 'active'}">${t('common.active')}</button>
          <button type="button" data-v="" aria-pressed="${state.status === ''}">${t('common.all')}</button>
        </div>
        <button type="button" class="btn btn-sm" id="gLink" aria-pressed="false">${icon('pencil')}${t('g.linkMode')}</button>
        <button type="button" class="btn btn-sm" id="gFit">${t('g.center')}</button>
        <!-- last, and pushed to the far end: a count that appeared between two
             controls would shove the whole row sideways on the first keystroke -->
        <span id="gFindCount" class="graph-find-count" aria-live="polite"></span>
      </div>
      <div class="graph-legend">
        ${TYPE_ORDER.filter(tp => counts[tp]).map(tp =>
          `<span class="legend-item"><span class="dot" style="--c:${typeColor(tp)}"></span>${TYPE_LABEL[tp]} <b>${counts[tp]}</b></span>`).join('') || t('g.emptyLegend')}
      </div>
      <div id="gBanner" class="link-banner" hidden></div>
      <div id="gCard" hidden></div>
    </div>
  </div>`;

  const nav = patch => {
    const p = { ...state, ...patch };
    const out = {};
    for (const [k, v] of Object.entries(p)) if (v) out[k] = v;
    if (p.status === '') out.status = '';
    if (p.status === 'active') delete out.status;
    go('graph', out);
  };
  wireDomainPicker(view, { id: 'gDomain', domains, onPick: domain => nav({ domain }) });
  wirePicker(view, { id: 'gType', items: fixedItems(types), onPick: type => nav({ type }) });
  view.querySelectorAll('.graph-controls .seg button').forEach(b =>
    b.addEventListener('click', () => nav({ status: b.dataset.v })));

  let engine;
  try {
    engine = new ForceGraph($('#gCanvas'), data.nodes, data.edges);
  } catch (err) {
    toast(t('g.err', { msg: err.message }), 'bad');
    return;
  }
  /* the canvas goes with the view's innerHTML, but the window listeners
     and the animation frame do not */
  onTeardown(() => engine.destroy());
  $('#gFit').addEventListener('click', () => engine.fit());
  $('#gLink').addEventListener('click', () => engine.toggleLinkMode());

  /* Every term has to match, the same as the diagram list's filter -- one
     boolean for every client-side filter in the app rather than one each. */
  const find = $('#gFind');
  find.addEventListener('input', debounce(() => {
    const n = engine.spotlight(find.value);
    $('#gFindCount').textContent = find.value.trim()
      ? t('g.findCount', { n: fmtInt(n), total: fmtInt(engine.nodes.length) }) : '';
  }, 160));
}

class ForceGraph {
  constructor(canvas, nodes, edges) {
    this.cv = canvas;
    this.cx = canvas.getContext('2d');
    this.nodes = nodes.map((n, i) => ({
      ...n,
      /* seed() writes x/y; px/py are where the node is DRAWN, x/y plus the
         idle float, see place() */
      x: 0, y: 0, px: 0, py: 0,
      vx: 0, vy: 0,
      r: 5.5 + Math.min(8, n.degree * 1.5),
      /* what it weighs against the other end of a relation, see linkShare */
      w: 1 + n.degree,
      /* float phase and rate, from the index: a cloud that breathed in
         unison would read as the canvas itself wobbling */
      fp: i * GOLD, fq: .7 + (i % 7) * .07,
      fs: FLOAT_SLACK(n.degree),
      /* how much of the current alpha this node feels; see heatAround() */
      heat: 1,
    }));
    this.byUid = Object.fromEntries(this.nodes.map(n => [n.uid, n]));
    this.edges = edges.filter(e => this.byUid[e.from_uid] && this.byUid[e.to_uid]);
    /* who is one relation away, for heatAround() and for the seed */
    this.adj = Object.fromEntries(this.nodes.map(n => [n.uid, new Set()]));
    for (const e of this.edges) {
      this.adj[e.from_uid].add(e.to_uid);
      this.adj[e.to_uid].add(e.from_uid);
    }
    this.seed();
    this.tx = 0; this.ty = 0; this.scale = 1;
    this.fitScale = null;   /* set by fit(); it is the zoom-out floor, see zoomAt */
    this.alpha = 1;
    this.hover = null; this.selected = null;
    this.linkMode = false; this.linkFrom = null;
    this.drag = null; this.pan = null;
    this.running = true;
    this.raf = 0;
    /* a drift nobody asked for is exactly what this setting turns off */
    this.float = !matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.floatAt = 0;
    this.dirty = false;
    /* theme colors resolved once from CSS custom properties */
    this.colAccent = cssVar('--accent') || '#bb86fc';
    this.colRing = cssVar('--bg') || '#121212';
    this.colEdge = cssVar('--canvas-edge') || 'rgba(255,255,255,.25)';
    this.colArrow = cssVar('--canvas-arrow') || 'rgba(255,255,255,.38)';
    this.colLabel = cssVar('--ink') || 'rgba(255,255,255,.87)';
    /* pointers currently down, by id: one drags or pans, two pinch */
    this.pointers = new Map();
    this.pinch = null;

    /* bound before resize(), which asks for a frame and so needs a callable
       frame handler already in place */
    this.loop = this.loop.bind(this);
    this._resize = this.resize.bind(this);
    addEventListener('resize', this._resize);
    this.resize();

    /* Settle synchronously so the graph is born calm -- and painted at
       least once even where rAF is throttled (headless, hidden tabs).

       The pass count comes from what a pass actually costs on the seeded
       layout, and the cooling rate comes from the pass count: alpha lands on
       SETTLE exactly as the last pass ends, at any size, so the graph opens
       at rest instead of finishing its arrangement in front of the reader. */
    const per = Math.max(1, this.cost());
    const passes = Math.max(MIN_PASSES, Math.min(MAX_PASSES, Math.floor(SETTLE_BUDGET / per)));
    const cool = Math.min(COOL, Math.pow(SETTLE, 1 / passes));
    this.heatAll();     /* the one pass where the whole layout is in play */
    for (let k = 0; k < passes && this.alpha > SETTLE; k++) this.physics(cool);
    this.fit();
    this.draw();

    /* Pointer events, not mouse events: the same three handlers then serve a
       mouse, a pen and a finger. On touch this canvas used to do nothing at
       all -- no pan, no zoom, no drag, no selection -- while the rail beside
       it collapsed for narrow screens and promised otherwise. */
    canvas.addEventListener('pointerdown', e => this.onDown(e));
    canvas.addEventListener('pointermove', e => this.onMove(e));
    this._up = e => this.onUp(e);
    addEventListener('pointerup', this._up);
    addEventListener('pointercancel', this._up);
    canvas.addEventListener('wheel', e => this.onWheel(e), { passive: false });
    canvas.addEventListener('click', e => this.onClick(e));

    this.wake();
  }
  destroy() {
    this.running = false;
    cancelAnimationFrame(this.raf);
    this.raf = 0;
    removeEventListener('resize', this._resize);
    removeEventListener('pointerup', this._up);
    removeEventListener('pointercancel', this._up);
  }
  /* Starting positions. Each group of memories that relations connect is laid
     out around its busiest member, and the groups are packed biggest first
     along a golden-angle spiral: the connected structure lands in the middle,
     and a memory with no relation at all is a group of one, so the loose ones
     fill the band around it. A relation therefore starts near its rest length
     instead of across the canvas, which is what the settle then has passes to
     polish.

     What sets a group's radius is the area its NODES take, counted from the
     middle of its own share, not the disc its radius spans. A tree is mostly
     empty canvas, so claiming the disc pushes every later group outward and
     leaves the middle of the layout thin. */
  seed() {
    const groups = this.groups().map(c => ({ c, rad: this.radial(c) }));
    groups.sort((a, b) => b.c.length - a.c.length || b.rad - a.rad);
    let area = 0;
    groups.forEach(({ c }, i) => {
      const own = c.length * SEED_SPACE * SEED_SPACE;
      const r = Math.sqrt((area + own / 2) / Math.PI), a = i * GOLD;
      area += own;
      const cx = Math.cos(a) * r, cy = Math.sin(a) * r;
      for (const uid of c) {
        const n = this.byUid[uid];
        n.x = n.px = n.x + cx;
        n.y = n.py = n.y + cy;
      }
    });
  }
  /* The uids of each set of memories that relations connect, one array per
     set. A memory with no relations comes back as an array of one. */
  groups() {
    const seen = new Set(), out = [];
    for (const n of this.nodes) {
      if (seen.has(n.uid)) continue;
      const q = [n.uid];
      seen.add(n.uid);
      for (let i = 0; i < q.length; i++) {
        for (const v of this.adj[q[i]]) if (!seen.has(v)) { seen.add(v); q.push(v); }
      }
      out.push(q);
    }
    return out;
  }
  /* One group, as a radial tree around its busiest member: one ring per step
     out from it, each subtree owning a slice of the circle sized by how many
     nodes hang off it. Writes coordinates LOCAL to the group -- seed()
     translates them -- and returns the radius they span. */
  radial(c) {
    const root = c.reduce((a, u) => this.byUid[u].degree > this.byUid[a].degree ? u : a, c[0]);
    const order = [root], depth = { [root]: 0 }, kids = {}, seen = new Set([root]);
    for (let i = 0; i < order.length; i++) {
      const u = order[i];
      kids[u] = [];
      for (const v of this.adj[u]) if (!seen.has(v)) {
        seen.add(v); depth[v] = depth[u] + 1; kids[u].push(v); order.push(v);
      }
    }
    /* the node itself plus everything hanging off it, deepest first */
    const span = {};
    for (let i = order.length - 1; i >= 0; i--) {
      span[order[i]] = 1 + kids[order[i]].reduce((s, v) => s + span[v], 0);
    }
    let rad = 0;
    const put = (u, a0, a1) => {
      const n = this.byUid[u], r = depth[u] * SEED_RING, a = (a0 + a1) / 2;
      n.x = Math.cos(a) * r; n.y = Math.sin(a) * r;
      rad = Math.max(rad, r);
      let at = a0;
      for (const v of kids[u]) {
        const slice = (a1 - a0) * span[v] / (span[u] - 1);
        put(v, at, at + slice);
        at += slice;
      }
    };
    put(root, 0, Math.PI * 2);
    return rad;
  }
  /* Resume the simulation, optionally stirring it back up first. Every caller
     that stirs it has to say WHERE, by setting heat first -- alpha alone is a
     store-wide shudder. */
  kick(alpha = 0) {
    if (alpha) this.alpha = Math.max(this.alpha, alpha);
    this.wake();
  }
  /* The whole layout is in play: the pre-paint settle, and nothing else. */
  heatAll() {
    for (const n of this.nodes) n.heat = 1;
  }
  /* Warm what the gesture can plausibly have disturbed: nodes near where the
     dragged one is NOW (so the warm patch travels with the pointer instead of
     staying where the drag began), plus its own relations at any distance.
     Everything else keeps its position -- the graph is not re-deciding itself
     because one node moved. */
  heatAround(src) {
    const linked = this.adj[src.uid];
    const k = 2 * HEAT_REACH * HEAT_REACH;
    for (const n of this.nodes) {
      const dx = n.x - src.x, dy = n.y - src.y;
      const near = Math.exp(-(dx * dx + dy * dy) / k);
      n.heat = Math.max(near, linked.has(n.uid) ? HEAT_LINKED : 0);
    }
    src.heat = 1;
  }
  wake() {
    if (!this.running || this.raf) return;
    this.raf = requestAnimationFrame(this.loop);
  }
  /* One frame, for a change that is only visual (hover, selection, pan,
     zoom) and must not restart the physics. */
  requestDraw() {
    /* The float loop usually already owns the next frame, which would make
       this a no-op and leave a hover waiting out the idle throttle. Marking
       the frame dirty is what gets it painted at once. */
    this.dirty = true;
    if (!this.running || this.raf) return;
    this.raf = requestAnimationFrame(() => { this.raf = 0; this.dirty = false; this.draw(); });
  }
  resize() {
    const r = this.cv.parentElement.getBoundingClientRect();
    const dpr = devicePixelRatio || 1;
    this.w = r.width; this.h = r.height;
    this.cv.width = r.width * dpr; this.cv.height = r.height * dpr;
    this.cx.setTransform(dpr, 0, 0, dpr, 0, 0);
    /* A wider window is not a different layout. This used to kick the physics,
       so dragging the window edge rearranged the store. Setting the canvas
       size cleared it, though, so the next frame has to paint: dirty, and
       kick() with no alpha, which only makes sure the loop is running. */
    this.dirty = true;
    this.kick();
  }
  /* Frames x/y, not the floated px/py: the float is a couple of px and would
     only make the framing breathe along with the nodes. */
  fit() {
    if (!this.nodes.length) return;
    const xs = this.nodes.map(n => n.x), ys = this.nodes.map(n => n.y);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const spanX = Math.max(80, maxX - minX), spanY = Math.max(80, maxY - minY);
    /* remembered as the zoom-out floor, same reason as the diagram engine: fit()
       writes the scale directly, so a fixed floor in zoomAt can end up ABOVE the
       level that shows the whole graph */
    this.fitScale = Math.min(2, Math.min(this.w / (spanX + 140), this.h / (spanY + 140)));
    this.scale = this.fitScale;
    this.tx = this.w / 2 - (minX + maxX) / 2 * this.scale;
    this.ty = this.h / 2 - (minY + maxY) / 2 * this.scale;
    this.requestDraw();
  }
  toWorld(e) {
    const r = this.cv.getBoundingClientRect();
    return { x: (e.clientX - r.left - this.tx) / this.scale, y: (e.clientY - r.top - this.ty) / this.scale };
  }
  /* px/py, not x/y: the float moves what the reader is aiming at, so the
     target has to move with it */
  nodeAt(p) {
    for (let i = this.nodes.length - 1; i >= 0; i--) {
      const n = this.nodes[i];
      const d2 = (n.px - p.x) ** 2 + (n.py - p.y) ** 2;
      if (d2 < (n.r + 4) ** 2) return n;
    }
    return null;
  }
  /* The two-finger gesture, frozen at the moment it started: every move is
     measured against this rather than against the previous frame, so the
     zoom cannot drift and the midpoint stays put under the fingers. */
  pinchFrom() {
    const [a, b] = [...this.pointers.values()];
    const r = this.cv.getBoundingClientRect();
    return {
      dist: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)),
      mx: (a.x + b.x) / 2 - r.left,
      my: (a.y + b.y) / 2 - r.top,
      scale: this.scale, tx: this.tx, ty: this.ty,
    };
  }
  /* Zoom about a point in canvas space -- shared by the wheel and the pinch,
     which used to be the wheel's arithmetic written out once. */
  zoomAt(mx, my, next) {
    const ns = Math.min(4, Math.max(Math.min(.12, this.fitScale ?? .12), next));
    this.tx = mx - (mx - this.tx) * (ns / this.scale);
    this.ty = my - (my - this.ty) * (ns / this.scale);
    this.scale = ns;
  }
  onDown(e) {
    /* same guard as the diagram engine: capture throws for a pointer the
       browser does not consider active, and that must not cost the drag */
    try { this.cv.setPointerCapture(e.pointerId); } catch { /* not capturable */ }
    this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (this.pointers.size === 2) {
      this.drag = null;
      this.pan = null;
      this.cv.classList.remove('dragging');
      this.pinch = this.pinchFrom();
      return;
    }
    if (this.pointers.size > 2) return;
    const n = this.nodeAt(this.toWorld(e));
    /* Taking hold of a node is not yet a change to the layout: no alpha, no
       heat, only the loop running so the frames come. Pressing one used to
       kick the physics store-wide, so the graph shuddered before the pointer
       had moved a pixel. */
    if (n) { this.drag = n; this.kick(); }
    else {
      this.pan = { x: e.clientX - this.tx, y: e.clientY - this.ty };
      this.cv.classList.add('dragging');
    }
  }
  onMove(e) {
    if (this.pointers.has(e.pointerId)) {
      this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    }
    if (this.pinch && this.pointers.size >= 2) {
      const [a, b] = [...this.pointers.values()];
      const dist = Math.max(1, Math.hypot(a.x - b.x, a.y - b.y));
      const p = this.pinch;
      this.scale = p.scale; this.tx = p.tx; this.ty = p.ty;
      this.zoomAt(p.mx, p.my, p.scale * (dist / p.dist));
      this.moved = true;
      tipHide();
      this.requestDraw();
      return;
    }
    if (this.drag) {
      const p = this.toWorld(e);
      /* Energy from the gesture itself: how far the node actually travelled
         this frame decides how much of the layout gets to react, and where
         is decided by heatAround(). A 2px nudge is worth less alpha than
         SETTLE, so it moves the node and nothing else -- which is what a 2px
         nudge looks like. */
      const step = Math.hypot(p.x - this.drag.x, p.y - this.drag.y);
      this.drag.x = p.x; this.drag.y = p.y;
      this.drag.vx = this.drag.vy = 0;
      this.moved = true;
      tipHide();
      this.heatAround(this.drag);
      this.kick(Math.min(HEAT_MAX, step * HEAT_PER_PX));
      return;
    }
    if (this.pan) {
      this.tx = e.clientX - this.pan.x; this.ty = e.clientY - this.pan.y;
      this.moved = true;
      tipHide();
      this.requestDraw();
      return;
    }
    const n = this.nodeAt(this.toWorld(e));
    if (n !== this.hover) { this.hover = n; this.requestDraw(); }
    this.cv.style.cursor = this.linkMode ? 'crosshair' : n ? 'pointer' : 'grab';
    /* a finger has no hover: leaving a tip behind after a tap is a label
       stuck on the canvas with nothing to dismiss it */
    if (e.pointerType === 'touch') { tipHide(); return; }
    if (n) tipShow(
      `<b>${esc(n.type)}</b> · ${esc(n.uid)}<br>${esc(n.label)}${n.domain ? `<br><span style="color:var(--ink-3)">${esc(n.domain)}</span>` : ''}`,
      e.clientX, e.clientY);
    else tipHide();
  }
  onUp(e) {
    if (e) this.pointers.delete(e.pointerId);
    if (this.pointers.size < 2) this.pinch = null;
    /* a second finger lifting off a pinch does not end the gesture */
    if (this.pointers.size) return;
    const wasDragging = !!(this.drag || this.pan);
    this.drag = null; this.pan = null;
    this.cv.classList.remove('dragging');
    if (wasDragging) this.requestDraw();
  }
  onWheel(e) {
    e.preventDefault();
    const r = this.cv.getBoundingClientRect();
    this.zoomAt(e.clientX - r.left, e.clientY - r.top,
                this.scale * (e.deltaY < 0 ? 1.13 : 1 / 1.13));
    this.requestDraw();
  }
  onClick(e) {
    if (this.moved) { this.moved = false; return; }
    const n = this.nodeAt(this.toWorld(e));
    if (this.linkMode && n) {
      if (!this.linkFrom) {
        this.linkFrom = n;
        $('#gBanner').textContent = t('g.banner.target', { uid: n.uid });
        this.requestDraw();
      } else if (n !== this.linkFrom) {
        this.promptLink(this.linkFrom, n);
      }
      return;
    }
    this.selected = n;
    this.requestDraw();
    this.renderCard();
  }
  toggleLinkMode() {
    this.linkMode = !this.linkMode;
    this.linkFrom = null;
    this.cv.classList.toggle('linkmode', this.linkMode);
    const b = $('#gBanner');
    b.hidden = !this.linkMode;
    if (this.linkMode) b.textContent = t('g.banner.source');
    setPressed($('#gLink'), this.linkMode);
    this.requestDraw();
  }
  /* Marks which nodes are OUT rather than filtering any out: the physics, the
     positions and the legend counts all stay exactly as they were, so what
     you learned from the shape before typing is still true after. Returns how
     many matched. */
  spotlight(qRaw) {
    const terms = qRaw.toLowerCase().split(/\s+/).filter(Boolean);
    let hit = 0;
    for (const n of this.nodes) {
      if (!terms.length) { n.dim = false; hit++; continue; }
      /* every domain it belongs to, not only the one it is filed at: a node
         cross-listed into a subject is findable by that subject's name */
      const hay = `${n.label} ${n.domain || ''} ${(n.also || []).join(' ')} ${n.tags || ''}`
        .toLowerCase();
      n.dim = !terms.every(w => hay.includes(w));
      if (!n.dim) hit++;
    }
    this.requestDraw();
    return hit;
  }
  promptLink(a, b) {
    const modal = openModal({
      title: t('g.modal.title'),
      bodyHTML: `
        <div class="gl-peers">
          <div><span class="dot" style="--c:${typeColor(a.type)};display:inline-block;margin-right:6px"></span>${esc(a.uid)} · ${esc(a.label)}</div>
          <div style="color:var(--accent);padding-left:2px;--ico:15px">${icon('arrow-down')}</div>
          <div><span class="dot" style="--c:${typeColor(b.type)};display:inline-block;margin-right:6px"></span>${esc(b.uid)} · ${esc(b.label)}</div>
        </div>
        <div class="field"><label for="glType">${t('g.modal.relType')}</label>
          <div class="act-row">${relTypeField({
            selId: 'glType', customId: 'glTypeCustom', options: REL_SUGGEST,
            value: 'relates_to', ariaLabel: t('g.modal.relType') })}</div></div>
        <div class="field"><label for="glNote">${t('g.modal.note')}</label><input type="text" id="glNote"></div>`,
      footHTML: `<button class="btn" data-x>${t('common.cancel')}</button><button class="btn btn-solid" data-ok>${t('g.modal.create')}</button>`,
    });
    const mq = s => modal.querySelector(s);
    const glRelValue = wireRelTypeField(modal, {
      selId: 'glType', customId: 'glTypeCustom', options: REL_SUGGEST });
    mq('[data-x]').onclick = () => { closeModal(); this.linkFrom = null; this.requestDraw(); };
    mq('[data-ok]').onclick = async () => {
      try {
        await api('/api/relations', { body: {
          from_uid: a.uid, to_uid: b.uid,
          relation_type: glRelValue() || 'relates_to',
          note: mq('#glNote').value } });
        closeModal();
        toast(t('dr.rel.created'), 'ok');
        this.toggleLinkMode();
        refreshBehind();
      } catch (err) { failed('err.relation', err); }
    };
  }
  renderCard() {
    const card = $('#gCard');
    if (!this.selected) { card.hidden = true; card.innerHTML = ''; return; }
    const n = this.selected;
    card.className = 'graph-card';
    card.hidden = false;
    card.innerHTML = `
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        ${typeTag(n.type)} ${uidChip(n.uid)} ${statusTag(n.status)} ${confPill(n.confidence)}
      </div>
      <div class="snippet">${esc(n.label)}</div>
      <div class="act-row">
        <button class="btn btn-sm btn-solid" data-openrec>${t('common.openRecord')}</button>
        ${n.domain ? `<span class="chip">${esc(n.domain)}</span>` : ''}
        ${(n.also || []).map(p => `<span class="chip">${esc(p)}</span>`).join('')}
        <span class="chip">${t('g.links', { n: n.degree })}</span>
      </div>`;
    wireCopyChips(card);
    card.querySelector('[data-openrec]').addEventListener('click', () => openRecord(n.uid));
  }
  /* `cool` is per-pass, so the pre-paint settle can spend its whole budget
     reaching rest while a live kick keeps the gentler decay that reads as
     the graph reacting to what the reader just did. */
  physics(cool = COOL) {
    if (this.alpha < SETTLE) return;
    /* Every force below is scaled by the RECEIVING node's heat, not by a
       single global alpha: that is what keeps a drag local. It costs the
       symmetry of the pair -- a warm node pushed by a cold one gives nothing
       back -- which is fine here and is in fact the effect wanted: the cold
       part of the layout is not participating. */
    const cells = this.cells();
    for (const [key, b] of cells) {
      for (let i = 0; i < b.length; i++) {
        for (let j = i + 1; j < b.length; j++) this.repel(b[i], b[j]);
      }
      for (const o of this.ahead(cells, key)) {
        for (const a of b) for (const q of o) this.repel(a, q);
      }
    }
    for (const e of this.edges) {
      const a = this.byUid[e.from_uid], b = this.byUid[e.to_uid];
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.max(1, Math.hypot(dx, dy));
      const f = (d - LINK_LEN) * LINK_K * this.alpha;
      const sa = linkShare(a.w, b.w), sb = linkShare(b.w, a.w);
      a.vx += dx / d * f * sa * a.heat; a.vy += dy / d * f * sa * a.heat;
      b.vx -= dx / d * f * sb * b.heat; b.vy -= dy / d * f * sb * b.heat;
    }
    for (const n of this.nodes) {
      n.vx -= n.x * GRAVITY * this.alpha * n.heat;
      n.vy -= n.y * GRAVITY * this.alpha * n.heat;
      if (n === this.drag) continue;
      n.vx *= DAMP; n.vy *= DAMP;
      /* escape velocity, capped -- see MAX_STEP */
      const v = Math.hypot(n.vx, n.vy);
      if (v > MAX_STEP) { n.vx *= MAX_STEP / v; n.vy *= MAX_STEP / v; }
      n.x += n.vx; n.y += n.vy;
    }
    this.alpha *= cool;
  }
  /* The nodes bucketed into cells one repulsion cutoff wide, keyed
     "column,row" -- two nodes in cells further apart than a neighbour cannot
     be within REPEL_CUT of each other. */
  cells() {
    const size = Math.sqrt(REPEL_CUT), out = new Map();
    for (const n of this.nodes) {
      const k = Math.floor(n.x / size) + ',' + Math.floor(n.y / size);
      const b = out.get(k);
      if (b) b.push(n); else out.set(k, [n]);
    }
    return out;
  }
  /* The four neighbours of one cell that a scan over every cell has not
     already paired it with. */
  ahead(cells, key) {
    const c = key.indexOf(',');
    const col = +key.slice(0, c), row = +key.slice(c + 1), out = [];
    for (const [dc, dr] of AHEAD) {
      const o = cells.get((col + dc) + ',' + (row + dr));
      if (o) out.push(o);
    }
    return out;
  }
  /* How many pairs one repulsion pass visits on the layout as it stands. */
  cost() {
    const cells = this.cells();
    let n = 0;
    for (const [key, b] of cells) {
      n += b.length * (b.length - 1) / 2;
      for (const o of this.ahead(cells, key)) n += b.length * o.length;
    }
    return n;
  }
  /* Push one pair apart, if they are close enough to feel each other and
     either of them is warm enough to answer. */
  repel(a, b) {
    let dx = a.x - b.x, dy = a.y - b.y;
    let d2 = dx * dx + dy * dy;
    if (d2 < 1) { dx = (Math.random() - .5); dy = (Math.random() - .5); d2 = 1; }
    if (d2 > REPEL_CUT) return;
    if (a.heat < SETTLE && b.heat < SETTLE) return;
    const f = REPEL / d2 * this.alpha;
    const d = Math.sqrt(d2);
    dx /= d; dy /= d;
    a.vx += dx * f * a.heat; a.vy += dy * f * a.heat;
    b.vx -= dx * f * b.heat; b.vy -= dy * f * b.heat;
  }
  /* px/py for every node: the settled position plus the idle float. The node
     under the pointer does not float -- it has to stay under the finger that
     is dragging it. */
  place(t) {
    /* screen px into world px, so the drift looks the same at every zoom */
    const amp = this.float ? Math.min(FLOAT_MAX, FLOAT_SCREEN / this.scale) : 0;
    for (const n of this.nodes) {
      if (!amp || n === this.drag) { n.px = n.x; n.py = n.y; continue; }
      const a = t * FLOAT_RATE * n.fq + n.fp;
      /* two rates, so the path is a slow figure rather than a circle */
      n.px = n.x + Math.cos(a) * amp * n.fs;
      n.py = n.y + Math.sin(a * 1.13 + n.fp) * amp * n.fs;
    }
  }
  draw() {
    const { cx } = this;
    this.place(performance.now());
    cx.clearRect(0, 0, this.w, this.h);
    cx.save();
    cx.translate(this.tx, this.ty);
    cx.scale(this.scale, this.scale);

    cx.strokeStyle = this.colEdge;
    cx.lineWidth = 1 / this.scale;
    for (const e of this.edges) {
      const a = this.byUid[e.from_uid], b = this.byUid[e.to_uid];
      /* an edge is only as bright as its dimmer end: a match keeps the lines
         that reach it, so you still see what it is connected to */
      cx.globalAlpha = (a.dim || b.dim) ? DIM : 1;
      cx.beginPath(); cx.moveTo(a.px, a.py); cx.lineTo(b.px, b.py); cx.stroke();
      const d = Math.hypot(b.px - a.px, b.py - a.py) || 1;
      const ux = (b.px - a.px) / d, uy = (b.py - a.py) / d;
      const px = b.px - ux * (b.r + 4), py = b.py - uy * (b.r + 4);
      const s = 4 / Math.sqrt(this.scale);
      cx.beginPath();
      cx.moveTo(px, py);
      cx.lineTo(px - ux * s - uy * s * .6, py - uy * s + ux * s * .6);
      cx.lineTo(px - ux * s + uy * s * .6, py - uy * s - ux * s * .6);
      cx.closePath();
      cx.fillStyle = this.colArrow;
      cx.fill();
    }
    cx.globalAlpha = 1;

    for (const n of this.nodes) {
      const color = typeColor(n.type);
      cx.globalAlpha = n.dim ? DIM : 1;
      cx.beginPath();
      if (n.type === 'anti_pattern') {          /* diamond: secondary encoding for the red↔green CVD pair */
        cx.moveTo(n.px, n.py - n.r); cx.lineTo(n.px + n.r, n.py);
        cx.lineTo(n.px, n.py + n.r); cx.lineTo(n.px - n.r, n.py);
        cx.closePath();
      } else {
        cx.arc(n.px, n.py, n.r, 0, 7);
      }
      if (n.status === 'archived') {
        cx.fillStyle = this.colRing; cx.fill();
        cx.strokeStyle = color; cx.lineWidth = 1.4 / this.scale; cx.stroke();
      } else {
        cx.fillStyle = color; cx.fill();
        cx.strokeStyle = this.colRing; cx.lineWidth = 2 / this.scale; cx.stroke();  /* surface ring */
      }
      if (n === this.selected || n === this.linkFrom) {
        cx.beginPath();
        cx.arc(n.px, n.py, n.r + 5, 0, 7);
        cx.strokeStyle = this.colAccent;
        cx.lineWidth = 1.6 / this.scale;
        if (n === this.linkFrom) cx.setLineDash([4 / this.scale, 3 / this.scale]);
        cx.stroke();
        cx.setLineDash([]);
      }
    }

    /* full strength even on a faded node: you hover to check what something
       is, and the answer must not be faded too */
    cx.globalAlpha = 1;
    if (this.hover && this.scale > .35) {
      const n = this.hover;
      cx.font = `${11 / this.scale}px 'Roboto Mono', monospace`;
      cx.fillStyle = this.colLabel;
      cx.fillText(n.label.slice(0, 46), n.px + n.r + 7 / this.scale, n.py + 4 / this.scale);
    }
    cx.restore();
  }
  loop() {
    this.raf = 0;
    if (!this.running) return;
    const t = performance.now();
    /* At rest, the only thing still moving is the float, and it is a 2px
       drift -- 30fps spends half the frames on it and looks the same. The
       physics is skipped along with the frame: it would return at the alpha
       check anyway, and this keeps the two in step.

       The old loop stopped dead once alpha fell below SETTLE, because before
       the float there was genuinely nothing left to paint. That guard is now
       `this.float`: reduced-motion turns the drift off, and with it the loop
       goes back to stopping, with kick()/requestDraw() as the way back in. */
    const live = this.alpha > SETTLE || this.drag || this.pan;
    if (live || this.dirty || t - this.floatAt >= FLOAT_STEP) {
      this.floatAt = t;
      this.dirty = false;
      this.physics();
      this.draw();
    }
    if (live || this.float) this.raf = requestAnimationFrame(this.loop);
  }
}
