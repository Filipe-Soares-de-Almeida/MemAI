/* The relations graph: a small canvas force layout.

   Repulsion is O(n²) per pass, which is why the server caps how many
   nodes it will hand over (see admin.graph) and why the initial settle
   below is a budget rather than a fixed pass count. The layout is
   computed here and never stored -- unlike the diagram editor, whose
   coordinates come from the store; see diagram.js for that contrast. */

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
/* Pair-distance checks to spend on the pre-paint settle, total. Worth more
   than it looks: the budget is what decides how converged the layout is when
   the view opens, and the whole run stays under ~90ms even at 1000 nodes
   because the pass floor caps it. */
const SETTLE_BUDGET = 8e6;
/* per-frame alpha decay once the graph is live and a kick has stirred it */
const COOL = .996;
/* How far a node may travel in one pass, px. THIS IS THE FIX for the node
   that opened the view sitting alone hundreds of px outside the cloud, and
   it is not a cosmetic clamp -- the layout had an escape velocity.

   A memory with no relations has no spring holding it. Born inside the dense
   middle, it feels repulsion from ~40 neighbours and nothing else, so it
   accelerates outward, and once it is more than 400px from every other node
   the `d2 > 160000` cutoff below turns repulsion off -- losing the brake at
   the same moment as the push. It coasts (one real store: out to r=1845 at
   ~9.7px per pass), and the only force left to bring it home is gravity,
   which is scaled by alpha. Alpha is already decaying, so the return trip
   runs out of passes and the node lands wherever it happens to be.

   Capping the step keeps it inside the range where repulsion still answers,
   so it settles into the cloud like everything else. Measured over the real
   store plus 30 synthetic graphs (40..800 nodes, several link densities):
   49 stranded nodes before, 2 after, and the ratio of the outermost node to
   the p90 radius drops from 3.4x to ~1.1x. Costs one hypot per node per
   pass, against an O(n²) repulsion. */
const MAX_STEP = 4;
/* The idle float. Purely a drawing offset: `place()` writes px/py and nothing
   ever writes back into x/y, so the layout the reader arranged is the layout
   that stays -- and the O(n²) physics still stops at rest instead of running
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
    const R = Math.sqrt(nodes.length + 1) * 60;
    this.nodes = nodes.map((n, i) => {
      const x = Math.cos(i * 2.399963) * R * Math.sqrt((i + 1) / (nodes.length + 1));
      const y = Math.sin(i * 2.399963) * R * Math.sqrt((i + 1) / (nodes.length + 1));
      return {
        ...n,
        x, y,
        /* where it is DRAWN: x/y plus the idle float, see place() */
        px: x, py: y,
        vx: 0, vy: 0,
        r: 5.5 + Math.min(8, n.degree * 1.5),
        /* float phase and rate, from the index: a cloud that breathed in
           unison would read as the canvas itself wobbling */
        fp: i * 2.399963, fq: .7 + (i % 7) * .07,
        fs: FLOAT_SLACK(n.degree),
        /* how much of the current alpha this node feels; see heatAround() */
        heat: 1,
      };
    });
    this.byUid = Object.fromEntries(this.nodes.map(n => [n.uid, n]));
    this.edges = edges.filter(e => this.byUid[e.from_uid] && this.byUid[e.to_uid]);
    /* who is one relation away, for heatAround() */
    this.adj = Object.fromEntries(this.nodes.map(n => [n.uid, new Set()]));
    for (const e of this.edges) {
      this.adj[e.from_uid].add(e.to_uid);
      this.adj[e.to_uid].add(e.from_uid);
    }
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
       The pass count is a BUDGET, not a constant: each pass is O(n²), so
       the old fixed 900 was 40M distance checks at 300 nodes and froze
       the tab before it had drawn anything.

       The cooling rate has to come FROM that budget rather than being the
       live loop's constant. Decaying at COOL regardless meant the settle ran
       out of passes long before alpha ran out of heat -- at 138 nodes it
       stopped at alpha .43 and handed the live loop 13 seconds of visible
       drift, with the outliers still 76px from where they belonged and fit()
       framing a layout that was still moving. Deriving it lands alpha on
       SETTLE exactly as the last pass ends, at any size: the graph opens at
       rest instead of finishing its arrangement in front of the reader.

       A big graph still settles LOOSER -- fewer passes is less total work, and
       no rate fixes that -- but loose and still beats tight and crawling. */
    const pairs = Math.max(1, this.nodes.length * (this.nodes.length - 1) / 2);
    const passes = Math.max(60, Math.min(900, Math.floor(SETTLE_BUDGET / pairs)));
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
    const N = this.nodes;
    /* Every force below is scaled by the RECEIVING node's heat, not by a
       single global alpha: that is what keeps a drag local. It costs the
       symmetry of the pair -- a warm node pushed by a cold one gives nothing
       back -- which is fine here and is in fact the effect wanted: the cold
       part of the layout is not participating. */
    for (let i = 0; i < N.length; i++) {
      const a = N[i];
      for (let j = i + 1; j < N.length; j++) {
        const b = N[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = (Math.random() - .5); dy = (Math.random() - .5); d2 = 1; }
        if (d2 > 160000) continue;
        if (a.heat < SETTLE && b.heat < SETTLE) continue;
        const f = 900 / d2 * this.alpha;
        const d = Math.sqrt(d2);
        dx /= d; dy /= d;
        a.vx += dx * f * a.heat; a.vy += dy * f * a.heat;
        b.vx -= dx * f * b.heat; b.vy -= dy * f * b.heat;
      }
    }
    for (const e of this.edges) {
      const a = this.byUid[e.from_uid], b = this.byUid[e.to_uid];
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.max(1, Math.hypot(dx, dy));
      const f = (d - 85) * .012 * this.alpha;
      a.vx += dx / d * f * a.heat; a.vy += dy / d * f * a.heat;
      b.vx -= dx / d * f * b.heat; b.vy -= dy / d * f * b.heat;
    }
    for (const n of N) {
      n.vx -= n.x * .0016 * this.alpha * n.heat;
      n.vy -= n.y * .0016 * this.alpha * n.heat;
      if (n === this.drag) continue;
      n.vx *= .86; n.vy *= .86;
      /* escape velocity, capped -- see MAX_STEP */
      const v = Math.hypot(n.vx, n.vy);
      if (v > MAX_STEP) { n.vx *= MAX_STEP / v; n.vy *= MAX_STEP / v; }
      n.x += n.vx; n.y += n.vy;
    }
    this.alpha *= cool;
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
