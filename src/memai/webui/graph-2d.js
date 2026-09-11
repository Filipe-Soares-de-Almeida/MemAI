/* The relations graph, drawn on a 2D canvas.

   This file owns the camera, the frame loop, the pointer, the selection and
   the two show toggles; graph-arrange.js owns where things are and how each
   of the three arrangements is drawn. Nothing here touches the DOM outside
   its own canvas -- hovering, selecting, travelling and link mode are
   reported to the view through callbacks, and the view owns the card, the
   tip, the legend and the toolbar.

   The arrangement settles on the main thread, a slice per frame, so the graph
   answers the pointer while it is still condensing. A settle that reaches
   SETTLE_MAX_MS is stopped where it stands: an arrangement good enough to
   read arrives long before the polishing does. */

import { cssVar } from './core/dom.js';
import { clamp } from './graph-geom.js';
import { deriveStore, arrangement, DEFAULT_MODE } from './graph-arrange.js';

/* the travel to one memory, and the pull-back that frames the whole graph */
const FLY_MS = 620;
const FIT_MS = 900;
/* How fast the framing chases an arrangement that is still condensing: the
   time constant of the ease, in milliseconds. */
const FOLLOW_TAU = 260;
/* the padding the camera leaves around a framed box, in CSS px */
const FIT_PAD = 46;
/* The wall-clock ceiling on one settle. A store big enough to reach it reads
   long before the arrangement stops moving, and a bar sitting at 60% for a
   minute is worse than a graph that has stopped. */
const SETTLE_MAX_MS = 20000;

/* What a memory fades to. The spotlight pushes a miss all the way back,
   because a search is a question about every memory at once; a selection only
   pushes the rest into context, because the shape of the store is still the
   thing being read. */
const DIM = 0.16;
const FOCUS_DIM = 0.3;

const ease = t => 1 - Math.pow(1 - t, 3);

/* ---------------------------------------------------------------- camera */

/* screen = world * k + (x, y). */
class Cam {
  constructor() {
    this.k = 1; this.x = 0; this.y = 0;
    this.min = 0.02; this.max = 40;
    this.w = 1; this.h = 1;
    this.tween = null;
    /* whether the reader has moved the camera since the last fit: what
       decides if a resize may reframe the arrangement or has to keep where
       they are */
    this.touched = false;
  }

  /* The world point at the middle of the frame stays at the middle of it: x
     and y are an absolute translation, so a frame that changes size around
     them slides the whole drawing by half the difference. */
  resize(w, h) {
    this.x += (w - this.w) / 2;
    this.y += (h - this.h) / 2;
    this.w = w; this.h = h;
  }

  toScreen(wx, wy) { return { x: wx * this.k + this.x, y: wy * this.k + this.y }; }

  toWorld(sx, sy) { return { x: (sx - this.x) / this.k, y: (sy - this.y) / this.k }; }

  clampK(k) { return clamp(k, this.min, this.max); }

  /* The camera that frames `box` with `pad` pixels around it. */
  framing(box, pad = FIT_PAD) {
    const bw = Math.max(1e-6, box.x1 - box.x0), bh = Math.max(1e-6, box.y1 - box.y0);
    const k = this.clampK(Math.min((this.w - pad * 2) / bw, (this.h - pad * 2) / bh));
    return {
      k,
      x: this.w / 2 - ((box.x0 + box.x1) / 2) * k,
      y: this.h / 2 - ((box.y0 + box.y1) / 2) * k,
    };
  }

  set(to) { this.k = to.k; this.x = to.x; this.y = to.y; this.tween = null; }

  /* Frame `box` and count it as untouched, so a later resize may reframe. */
  frame(box, ms = 0) {
    this.glide(this.framing(box), ms);
    this.touched = false;
  }

  /* Move to `to` over `ms`, or straight away when `ms` is 0. */
  glide(to, ms) {
    if (!ms) { this.set(to); return; }
    this.tween = { from: { k: this.k, x: this.x, y: this.y }, to, at: 0, ms };
  }

  goTo(wx, wy, k, ms = 0) {
    const nk = this.clampK(k || this.k);
    this.glide({ k: nk, x: this.w / 2 - wx * nk, y: this.h / 2 - wy * nk }, ms);
    this.touched = true;
  }

  /* Ease toward the framing of `box` without ever arriving: what an
     arrangement that is still moving is followed with. */
  chase(box, dt) {
    const to = this.framing(box);
    const f = 1 - Math.exp(-dt / FOLLOW_TAU);
    this.k += (to.k - this.k) * f;
    this.x += (to.x - this.x) * f;
    this.y += (to.y - this.y) * f;
  }

  zoomAt(sx, sy, factor) {
    this.tween = null;
    this.touched = true;
    const before = this.toWorld(sx, sy);
    this.k = this.clampK(this.k * factor);
    const after = this.toWorld(sx, sy);
    this.x += (after.x - before.x) * this.k;
    this.y += (after.y - before.y) * this.k;
  }

  panBy(dx, dy) { this.tween = null; this.touched = true; this.x += dx; this.y += dy; }

  /* Advance an in-flight move. Returns whether the camera is still moving. */
  advance(ms) {
    const tw = this.tween;
    if (!tw) return false;
    tw.at += ms;
    const t = tw.ms ? Math.min(1, tw.at / tw.ms) : 1;
    const e = ease(t);
    this.k = tw.from.k + (tw.to.k - tw.from.k) * e;
    this.x = tw.from.x + (tw.to.x - tw.from.x) * e;
    this.y = tw.from.y + (tw.to.y - tw.from.y) * e;
    if (t >= 1) { this.tween = null; return false; }
    return true;
  }
}

/* ---------------------------------------------------------------- engine */

export class GraphCanvas {
  /* `nodes` and `edges` are the /api/graph payload and `colorOf(type)` hands
     back the CSS colour of a memory type.

     The callbacks are the whole outward surface: onSelect(node) when the
     selection changes, onSelectDomain({domain, count}) when a DOMAIN is the
     selection instead, onOpen(node) when the reader asks for the record,
     onHover(target, x, y) for the tip -- a memory carries `uid`, a domain body
     carries `domain` and no uid -- onLink(kind, a, b) for link mode ('from' or
     'pair'), onSettle(progress, done, error) for the arrangement, and
     obstacles() for the boxes the chrome occupies in canvas coordinates, so no
     name is drawn where a panel covers it.

     `mode` is the arrangement to open on and `show` the two toggles. */
  constructor(canvas, {
    nodes, edges, colorOf,
    onSelect = () => {}, onSelectDomain = () => {}, onOpen = () => {},
    onHover = () => {}, onLink = () => {}, onSettle = () => {},
    obstacles = () => [], mode = DEFAULT_MODE, show = {},
  }) {
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('getContext("2d") returned null');
    this.cv = canvas;
    this.ctx = ctx;
    this.cb = { onSelect, onSelectDomain, onOpen, onHover, onLink, onSettle, obstacles };
    this.colorOf = colorOf;

    this.nodes = nodes.map((n, i) => ({
      ...n,
      i,
      /* the name: the title a writer chose, falling back to the opening line
         of the body the way a memory row does */
      name: (n.title || '').trim() || n.label || n.uid,
      miss: false,
    }));
    this.byUid = new Map(this.nodes.map(n => [n.uid, n]));
    this.D = deriveStore(this.nodes, edges);
    this.edges = this.D.edges;

    this.show = { links: show.links !== false, titles: show.titles !== false };
    this.hover = null; this.selected = null; this.cameFrom = null;
    this.selectedDomain = null;
    /* The selection's set, which everything outside fades behind, and the
       hover's, which is brighter still. One is a decision and the other is a
       pointer, so they are two sets and not one. */
    this.focusSet = null;
    this.lit = null;
    this.linkMode = false; this.linkFrom = null;
    this.spotlit = false;

    this.palette = readPalette();
    this.cam = new Cam();
    this.drag = null; this.moved = false;
    this.pointers = new Map(); this.pinch = null;
    this.motion = !matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.running = true;
    this.raf = 0;
    this.dirty = true;
    this.lastFrame = 0;

    this._loop = this._loop.bind(this);

    /* The frame changes size without the window resizing -- the rail
       collapses, a scrollbar appears -- and a window-only listener misses it:
       the backing store then keeps its old size and the pointer arrives in a
       coordinate space the camera is not in. */
    this._resize = () => this.resize();
    addEventListener('resize', this._resize);
    if (typeof ResizeObserver === 'function') {
      this._ro = new ResizeObserver(this._resize);
      this._ro.observe(canvas.parentElement);
    }
    this.resize();

    /* Pointer events, not mouse events: one path serves a mouse, a pen and a
       finger. */
    canvas.addEventListener('pointerdown', e => this._down(e));
    canvas.addEventListener('pointermove', e => this._move(e));
    canvas.addEventListener('wheel', e => this._wheel(e), { passive: false });
    canvas.addEventListener('click', e => this._click(e));
    canvas.addEventListener('contextmenu', e => e.preventDefault());
    this._up = e => this._pointerUp(e);
    addEventListener('pointerup', this._up);
    addEventListener('pointercancel', this._up);
    this._keyDown = e => this._onKeyDown(e);
    addEventListener('keydown', this._keyDown);

    this.setMode(mode, { fit: true });
  }

  destroy() {
    this.running = false;
    cancelAnimationFrame(this.raf);
    this.raf = 0;
    removeEventListener('resize', this._resize);
    this._ro?.disconnect();
    removeEventListener('pointerup', this._up);
    removeEventListener('pointercancel', this._up);
    removeEventListener('keydown', this._keyDown);
  }

  /* ----------------------------------------------------------- the modes */

  /* Build an arrangement and hand the camera to it. The selection, the
     spotlight and the toggles survive the switch: they are the reader's
     state, not the drawing's. */
  setMode(id, { fit = true } = {}) {
    this.hover = null;
    this.lit = null;
    const spec = arrangement(id);
    this.mode = spec.id;
    this.note = spec.note;
    this.arr = spec.make(this.env());
    this.settleStart = performance.now();
    this.settled = !this.arr.step(this.env());
    if (fit) this.cam.frame(this.arr.box());
    this.cb.onSettle(this.arr.progress, this.settled);
    this.dirty = true;
    this._wake();
    return this.mode;
  }

  setShow(patch) {
    this.show = { ...this.show, ...patch };
    this.dirty = true;
    this._wake();
    return this.show;
  }

  /* ----------------------------------------------------------- the frame */

  resize() {
    const r = this.cv.parentElement.getBoundingClientRect();
    const dpr = Math.min(2, devicePixelRatio || 1);
    this.w = Math.max(1, r.width); this.h = Math.max(1, r.height);
    this.cv.width = Math.round(this.w * dpr);
    this.cv.height = Math.round(this.h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.cam.resize(this.w, this.h);
    /* A frame that changes size by a lot -- a phone turned, the rail
       collapsing -- leaves an untouched arrangement framed for the old one.
       Reframe it; a camera the reader has moved is theirs and stays put. */
    if (this.arr && !this.cam.touched) this.cam.frame(this.arr.box());
    this.dirty = true;
    this._wake();
  }

  /* The rectangles the floating chrome occupies, padded, so no name is drawn
     where a panel covers it. */
  _taken() {
    return this.cb.obstacles().map(([x, y, w, h]) =>
      ({ x: x - 6, y: y - 6, w: w + 12, h: h + 12 }));
  }

  fade(uid) {
    const node = this.byUid.get(uid);
    if (!node) return 1;
    let v = node.miss ? DIM : 1;
    if (this.focusSet && !this.focusSet.has(uid)) v = Math.min(v, FOCUS_DIM);
    return v;
  }

  /* What every arrangement is handed: the store, the frame, the reader's
     state and the palette. Rebuilt per frame -- it is a dozen fields, and a
     cached one is a state the drawing can read after it has changed. */
  env() {
    const marks = [];
    if (this.hover && this.hover !== this.selected)
      marks.push({ ...idOf(this.hover), color: this.palette.hot, width: 1.6 });
    if (this.selected)
      marks.push({ uid: this.selected.uid, color: this.palette.accent, width: 2.2 });
    if (this.selectedDomain)
      marks.push({ domain: this.selectedDomain, color: this.palette.accent, width: 2 });
    if (this.linkFrom)
      marks.push({ uid: this.linkFrom.uid, color: this.palette.accentHi, width: 2 });
    return {
      D: this.D,
      W: this.w, H: this.h,
      cam: this.cam,
      palette: this.palette,
      /* No name is drawn while the arrangement is still moving: the board
         would place each one against a frame that is already out of date,
         and a store's worth of text redrawn per frame reads as flicker. */
      show: { ...this.show, titles: this.show.titles && this.settled },
      hover: this.hover, selected: this.selected, linkFrom: this.linkFrom,
      /* what the pointer is standing on, as uids: the hovered memory and its
         neighbours, or every memory in the hovered domain */
      lit: this.lit,
      marks,
      taken: this._taken(),
      colorOf: this.colorOf,
      fade: uid => this.fade(uid),
      /* Whether a domain path is the one in scope, under it, or on the way
         down to it. In scope is the SELECTED domain, or the hovered one while
         there is no selection: a domain drawn at full strength while the
         memories around it fade reads as lit, so every domain staying full
         through a hover lit all of them at once. The ancestors stay legible,
         or the branch in scope is a bright patch with no path to read it by. */
      inScope: path => {
        const at = this.selectedDomain
          || (this.hover && !this.hover.uid ? this.hover.domain : null);
        if (!at || !path) return true;
        return path === at || path.startsWith(`${at}/`) || at.startsWith(`${path}/`);
      },
      font: (weight, size) => `${weight} ${size}px ${this.palette.font}`,
    };
  }

  fit() {
    this.cam.frame(this.arr.box(), this.motion ? FIT_MS : 0);
    this.dirty = true;
    this._wake();
  }

  _wake() {
    if (!this.running || this.raf) return;
    this.raf = requestAnimationFrame(this._loop);
  }

  _loop(now) {
    this.raf = 0;
    if (!this.running) return;
    const ms = Math.min(64, now - (this.lastFrame || now - 16));
    this.lastFrame = now;

    let busy = false;
    if (!this.settled) {
      busy = this.arr.step(this.env());
      if (busy && now - this.settleStart > SETTLE_MAX_MS) {
        this.arr.halt();
        this.arr.step(this.env());
        busy = false;
      }
      if (!busy) {
        this.settled = true;
        if (!this.cam.touched) this.cam.frame(this.arr.box(), this.motion ? FIT_MS : 0);
      } else if (this.motion && !this.cam.touched && !this.drag && !this.pinch) {
        /* the arrangement condensing and the frame pulling back are one
           movement: the only authored moment this view has */
        this.cam.chase(this.arr.box(), ms);
      }
      this.cb.onSettle(this.arr.progress, this.settled);
    }
    const moving = this.cam.advance(ms);

    /* Under reduced motion nothing is painted while the arrangement moves:
       there is nothing to watch, and the frame arrives finished. */
    const paint = this.motion || this.settled;
    if (paint && (this.dirty || moving || busy)) {
      this.dirty = false;
      this.draw();
    }
    if (moving || busy) this._wake();
  }

  draw() {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.w, this.h);
    this.arr.draw(ctx, this.cam, this.env());
  }

  /* ------------------------------------------------------- interaction */

  _local(e) {
    const r = this.cv.getBoundingClientRect();
    /* the backstop for a resize that never arrived: the rect is being read
       anyway, and a stale camera answers a click with the wrong memory */
    if (Math.abs(r.width - this.w) > 1 || Math.abs(r.height - this.h) > 1) this.resize();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  /* What is under a canvas point: a memory, a domain body, or nothing. */
  at(sx, sy) {
    const p = this.cam.toWorld(sx, sy);
    return this.arr.hit(p.x, p.y, this.cam);
  }

  _down(e) {
    try { this.cv.setPointerCapture(e.pointerId); } catch { /* not capturable */ }
    this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (this.pointers.size === 2) {
      const [a, b] = [...this.pointers.values()];
      this.drag = null;
      this.pinch = { gap: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)),
                     k: this.cam.k, mid: [(a.x + b.x) / 2, (a.y + b.y) / 2] };
      return;
    }
    this.drag = { x: e.clientX, y: e.clientY };
    this.moved = false;
    this.cv.classList.add('grabbing');
  }

  _move(e) {
    if (this.pointers.has(e.pointerId)) {
      this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    }
    if (this.pinch && this.pointers.size >= 2) {
      const [a, b] = [...this.pointers.values()];
      const gap = Math.max(1, Math.hypot(a.x - b.x, a.y - b.y));
      const r = this.cv.getBoundingClientRect();
      const mid = [(a.x + b.x) / 2, (a.y + b.y) / 2];
      this.cam.zoomAt(mid[0] - r.left, mid[1] - r.top, gap / this.pinch.gap);
      this.cam.panBy(mid[0] - this.pinch.mid[0], mid[1] - this.pinch.mid[1]);
      this.pinch.gap = gap;
      this.pinch.mid = mid;
      this.moved = true;
      this.cb.onHover(null);
      this.dirty = true;
      this._wake();
      return;
    }
    if (this.drag) {
      const dx = e.clientX - this.drag.x, dy = e.clientY - this.drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) this.moved = true;
      this.cam.panBy(dx, dy);
      this.drag = { x: e.clientX, y: e.clientY };
      this.cb.onHover(null);
      this.dirty = true;
      this._wake();
      return;
    }
    const found = this.at(...this._local(e));
    const same = found === this.hover
      || (found && this.hover && found.uid === this.hover.uid
          && found.domain === this.hover.domain);
    if (!same) {
      this.hover = found;
      /* computed on the change and not per frame: a domain's set is a pass
         over every memory, and a pointer crossing a hub would put the store's
         size on the cost of moving the mouse */
      this.lit = this._around(found);
      this.dirty = true;
      this._wake();
    }
    this.cv.style.cursor = this.linkMode ? 'crosshair' : found ? 'pointer' : 'grab';
    /* a finger has no hover: a tip left behind after a tap is a label stuck on
       the canvas with nothing to dismiss it */
    if (e.pointerType === 'touch') { this.cb.onHover(null); return; }
    this.cb.onHover(found, e.clientX, e.clientY);
  }

  _pointerUp(e) {
    if (e) this.pointers.delete(e.pointerId);
    if (this.pointers.size < 2) this.pinch = null;
    if (this.pointers.size) return;
    this.drag = null;
    this.cv.classList.remove('grabbing');
  }

  _wheel(e) {
    e.preventDefault();
    const [x, y] = this._local(e);
    this.cam.zoomAt(x, y, Math.exp(-e.deltaY * 0.0016));
    this.dirty = true;
    this._wake();
  }

  _click(e) {
    if (this.moved) { this.moved = false; return; }
    const found = this.at(...this._local(e));
    if (this.linkMode && found && found.uid) {
      const node = this.byUid.get(found.uid);
      if (!this.linkFrom) {
        this.linkFrom = node;
        this.dirty = true;
        this._wake();
        this.cb.onLink('from', node);
      } else if (node !== this.linkFrom) {
        this.cb.onLink('pair', this.linkFrom, node);
      }
      return;
    }
    if (found && found.domain && !found.uid) {
      /* a domain body is a place, not a record: the arrangement decides what
         going there means, and the focus holds what is filed in it */
      this.selectDomain(found.domain);
      this.arr.click?.(found, this.env());
      this.dirty = true;
      this._wake();
      return;
    }
    if (!found && this.arr.click) {
      this.arr.click(null, this.env());
      this.fit();
    }
    this.select(found ? found.uid : null);
  }

  _onKeyDown(e) {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
    const el = document.activeElement;
    if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) return;
    if (!this.selected) return;
    if (e.key === 'ArrowRight') { e.preventDefault(); this.hop(1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); this.hop(-1); }
    else if (e.key === 'Enter') { e.preventDefault(); this.cb.onOpen(this.selected); }
  }

  /* --------------------------------------------------------- the reader */

  /* Select a memory and travel to it. Passing null clears the selection and
     leaves the camera where it is -- dismissing a card is not a journey. */
  select(uid, { fly = true } = {}) {
    const node = uid ? this.byUid.get(uid) : null;
    this.selected = node || null;
    this.cameFrom = null;
    if (this.selectedDomain) {
      this.selectedDomain = null;
      this.cb.onSelectDomain(null);
    }
    this._focus(this.selected);
    if (this.selected && fly) this.travel(this.selected.uid);
    this.cb.onSelect(this.selected);
  }

  /* Select a DOMAIN: everything filed in it or under it holds its strength and
     the rest of the store falls back, the same way a memory's neighbourhood
     does. Passing null clears it. */
  selectDomain(path) {
    if (this.selected) {
      this.selected = null;
      this.cameFrom = null;
      this.cb.onSelect(null);
    }
    this.selectedDomain = path || null;
    this.focusSet = path ? this.inDomain(path) : null;
    this.dirty = true;
    this._wake();
    this.cb.onSelectDomain(
      path ? { domain: path, count: this.focusSet.size } : null);
  }

  /* Every memory filed AT `path` or under it. The filed domain only: a
     memory's `also` paths cut across the tree, and a scope that followed them
     would light half the store from a leaf. */
  inDomain(path) {
    const under = `${path}/`;
    const out = new Set();
    for (const n of this.nodes) {
      const d = n.domain || '';
      if (d === path || d.startsWith(under)) out.add(n.uid);
    }
    return out;
  }

  _focus(node) {
    this.focusSet = node
      ? new Set([node.uid, ...this.neighbours(node.uid).map(p => p.uid)])
      : null;
    this.dirty = true;
    this._wake();
  }

  /* The set the pointer lights: a memory and its neighbours, or a domain and
     everything filed under it. */
  _around(at) {
    if (!at) return null;
    if (!at.uid) return at.domain ? this.inDomain(at.domain) : null;
    const out = new Set([at.uid]);
    for (const e of this.D.adj.get(at.uid) || [])
      out.add(e.from_uid === at.uid ? e.to_uid : e.from_uid);
    return out;
  }

  /* Bring a memory to the middle, at a zoom close enough to read its name. */
  travel(uid) {
    const at = this.arr.locate?.(uid);
    if (!at) return;
    this.cam.goTo(at.x, at.y, Math.max(this.cam.k, 1), this.motion ? FLY_MS : 0);
    this.dirty = true;
    this._wake();
  }

  /* Step to the next memory along the selection's relations, and select it.
     `cameFrom` is where the last step arrived from, so the same key again
     carries on outward instead of bouncing between two memories. */
  hop(step) {
    const from = this.selected;
    if (!from) return null;
    const peers = this.neighbours(from.uid);
    if (!peers.length) return null;
    let at = peers.findIndex(p => p.uid === this.cameFrom);
    if (at < 0) at = step > 0 ? -1 : 0;
    const node = peers[((at + step) % peers.length + peers.length) % peers.length];
    this.selected = node;
    this.cameFrom = from.uid;
    this._focus(node);
    this.travel(node.uid);
    this.cb.onSelect(node);
    return node;
  }

  /* The memories one relation away, most-connected first. */
  neighbours(uid) {
    const seen = new Set();
    const out = [];
    for (const e of this.D.adj.get(uid) || []) {
      const other = e.from_uid === uid ? e.to_uid : e.from_uid;
      if (other === uid || seen.has(other)) continue;
      seen.add(other);
      const node = this.byUid.get(other);
      if (node) out.push(node);
    }
    return out.sort((a, b) => (b.degree || 0) - (a.degree || 0));
  }

  /* Every term has to match. Nothing is removed and the arrangement never
     moves: what a search does here is push everything else back. */
  spotlight(raw) {
    const terms = String(raw || '').toLowerCase().split(/\s+/).filter(Boolean);
    let count = 0, first = null;
    for (const node of this.nodes) {
      if (!terms.length) { node.miss = false; count++; continue; }
      const hay = `${node.name} ${node.label || ''} ${node.domain || ''} `
        + `${(node.also || []).join(' ')} ${node.tags || ''}`.toLowerCase();
      node.miss = !terms.every(w => hay.includes(w));
      if (!node.miss) {
        count++;
        if (!first || (node.degree || 0) > (first.degree || 0)) first = node;
      }
    }
    this.spotlit = terms.length > 0;
    this.dirty = true;
    this._wake();
    return { count, first };
  }

  toggleLinkMode() {
    this.linkMode = !this.linkMode;
    this.linkFrom = null;
    this.cv.classList.toggle('linkmode', this.linkMode);
    this.dirty = true;
    this._wake();
    return this.linkMode;
  }

  clearLinkFrom() {
    this.linkFrom = null;
    this.dirty = true;
    this._wake();
  }
}

/* A hit's identity, whichever kind it is. */
const idOf = hit => (hit.uid ? { uid: hit.uid } : { domain: hit.domain });

/* The theme's own colours, read once: the graph follows the stylesheet like
   the rest of the dashboard. */
function readPalette() {
  const ink = cssVar('--ink') || 'rgba(255,255,255,.87)';
  return {
    ink,
    ink2: cssVar('--ink-2') || 'rgba(255,255,255,.6)',
    ink3: cssVar('--ink-3') || 'rgba(255,255,255,.5)',
    accent: cssVar('--accent') || '#bb86fc',
    accentHi: cssVar('--accent-hi') || '#d3b1ff',
    hot: '#ffffff',
    /* the scaffolding an arrangement stands on, the branch above it, and the
       part of it the pointer is lighting */
    tree: 'rgba(255, 255, 255, .055)',
    treeHi: 'rgba(255, 255, 255, .16)',
    treeHot: 'rgba(255, 255, 255, .38)',
    /* a name sits on its own colour with the page's ground stroked behind it:
       over the middle of a large store there is no ink dark enough to read
       against without one */
    halo: haloFrom(cssVar('--bg') || '#121212'),
    font: cssVar('--font-ui') || 'Roboto, sans-serif',
    mono: cssVar('--font-m') || 'Roboto Mono, monospace',
    rel: {
      relates_to: cssVar('--canvas-edge') || 'rgba(255,255,255,.25)',
      supersedes: cssVar('--warn') || '#ffd54f',
      contradicts: cssVar('--bad-ink') || '#e57373',
      links_to: cssVar('--zip') || '#7fb3d5',
    },
  };
}

const haloFrom = bg => {
  const h = bg.replace('#', '');
  const n = h.length === 3 ? h.split('').map(c => c + c).join('') : h;
  const v = parseInt(n, 16);
  if (!Number.isFinite(v)) return 'rgba(10, 10, 10, .82)';
  return `rgba(${(v >> 16) & 255}, ${(v >> 8) & 255}, ${v & 255}, .82)`;
};
