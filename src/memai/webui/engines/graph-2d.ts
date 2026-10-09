/* The relations graph on a 2D canvas: camera, frame loop, pointer, selection and toggles, reported
   through callbacks. The arrangement settles a slice per frame and stops at SETTLE_MAX_MS. */

import { cssVar } from '../core/dom.ts';
import { clamp } from './graph-geom.ts';
import { deriveStore, arrangement, DEFAULT_MODE } from './graph-arrange.ts';
import type {
  Arrangement, Env, GraphEdge, GraphNode, GraphNodeData, Hit, Mark, Palette, Show, Store,
} from './graph-arrange.ts';
import type { Box, Pt, Rect } from './graph-draw.ts';

interface View { k: number; x: number; y: number }
interface Tween { from: View; to: View; at: number; ms: number }

type Obstacle = [number, number, number, number];

export interface GraphCallbacks {
  onSelect: (node: GraphNode | null) => void;
  onSelectDomain: (hit: { domain: string; count: number } | null) => void;
  onOpen: (node: GraphNode) => void;
  onHover: (hit: Hit | null, x?: number, y?: number) => void;
  onLink: (step: 'from' | 'pair', from: GraphNode, to?: GraphNode) => void;
  onSettle: (progress: number, settled: boolean) => void;
  obstacles: () => Obstacle[];
}

export interface GraphOptions extends Partial<GraphCallbacks> {
  nodes: GraphNodeData[];
  edges: GraphEdge[];
  colorOf: (type: string) => string;
  mode?: string;
  show?: Partial<Show>;
}

/* the travel to one memory, and the pull-back that frames the whole graph */
const FLY_MS = 620;
const FIT_MS = 900;
/* How fast the framing chases an arrangement that is still condensing: the
   time constant of the ease, in milliseconds. */
const FOLLOW_TAU = 260;
/* the padding the camera leaves around a framed box, in CSS px */
const FIT_PAD = 46;
/* The wall-clock ceiling on one settle, in milliseconds: a store big enough
   to reach it is readable long before the arrangement stops moving. */
const SETTLE_MAX_MS = 20000;

/* What a memory fades to: a spotlight miss all the way back, a selection's context less so. */
const DIM = 0.16;
const FOCUS_DIM = 0.3;

const ease = (t: number): number => 1 - Math.pow(1 - t, 3);

/* ---------------------------------------------------------------- camera */

/* screen = world * k + (x, y). */
class Cam {
  declare k: number;
  declare x: number;
  declare y: number;
  declare min: number;
  declare max: number;
  declare w: number;
  declare h: number;
  declare tween: Tween | null;
  declare touched: boolean;

  constructor() {
    this.k = 1; this.x = 0; this.y = 0;
    this.min = 0.02; this.max = 40;
    this.w = 1; this.h = 1;
    this.tween = null;
    /* whether the reader moved the camera since the last fit, so a resize may not reframe */
    this.touched = false;
  }

  /* Keeps the world point at the frame's centre: x and y are absolute, so a resize shifts them
     by half the difference. */
  resize(w: number, h: number): void {
    this.x += (w - this.w) / 2;
    this.y += (h - this.h) / 2;
    this.w = w; this.h = h;
  }

  toScreen(wx: number, wy: number): Pt { return { x: wx * this.k + this.x, y: wy * this.k + this.y }; }

  toWorld(sx: number, sy: number): Pt { return { x: (sx - this.x) / this.k, y: (sy - this.y) / this.k }; }

  clampK(k: number): number { return clamp(k, this.min, this.max); }

  /* The camera that frames `box` with `pad` pixels around it. */
  framing(box: Box, pad = FIT_PAD): View {
    const bw = Math.max(1e-6, box.x1 - box.x0), bh = Math.max(1e-6, box.y1 - box.y0);
    const k = this.clampK(Math.min((this.w - pad * 2) / bw, (this.h - pad * 2) / bh));
    return {
      k,
      x: this.w / 2 - ((box.x0 + box.x1) / 2) * k,
      y: this.h / 2 - ((box.y0 + box.y1) / 2) * k,
    };
  }

  set(to: View): void { this.k = to.k; this.x = to.x; this.y = to.y; this.tween = null; }

  /* Frame `box` and count it as untouched, so a later resize may reframe. */
  frame(box: Box, ms = 0): void {
    this.glide(this.framing(box), ms);
    this.touched = false;
  }

  /* Move to `to` over `ms`, or straight away when `ms` is 0. */
  glide(to: View, ms: number): void {
    if (!ms) { this.set(to); return; }
    this.tween = { from: { k: this.k, x: this.x, y: this.y }, to, at: 0, ms };
  }

  goTo(wx: number, wy: number, k: number, ms = 0): void {
    const nk = this.clampK(k || this.k);
    this.glide({ k: nk, x: this.w / 2 - wx * nk, y: this.h / 2 - wy * nk }, ms);
    this.touched = true;
  }

  /* Ease toward the framing of `box` without ever arriving: what an
     arrangement that is still moving is followed with. */
  chase(box: Box, dt: number): void {
    const to = this.framing(box);
    const f = 1 - Math.exp(-dt / FOLLOW_TAU);
    this.k += (to.k - this.k) * f;
    this.x += (to.x - this.x) * f;
    this.y += (to.y - this.y) * f;
  }

  zoomAt(sx: number, sy: number, factor: number): void {
    this.tween = null;
    this.touched = true;
    const before = this.toWorld(sx, sy);
    this.k = this.clampK(this.k * factor);
    const after = this.toWorld(sx, sy);
    this.x += (after.x - before.x) * this.k;
    this.y += (after.y - before.y) * this.k;
  }

  panBy(dx: number, dy: number): void { this.tween = null; this.touched = true; this.x += dx; this.y += dy; }

  /* Advance an in-flight move. Returns whether the camera is still moving. */
  advance(ms: number): boolean {
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
  declare cv: HTMLCanvasElement;
  declare ctx: CanvasRenderingContext2D;
  declare cb: GraphCallbacks;
  declare colorOf: (type: string) => string;
  declare nodes: GraphNode[];
  declare byUid: Map<string, GraphNode>;
  declare D: Store;
  declare edges: GraphEdge[];
  declare show: Show;
  declare hover: Hit | null;
  declare selected: GraphNode | null;
  declare cameFrom: string | null;
  declare selectedDomain: string | null;
  declare focusSet: Set<string> | null;
  declare lit: Set<string> | null;
  declare linkMode: boolean;
  declare linkFrom: GraphNode | null;
  declare spotlit: boolean;
  declare palette: Palette;
  declare cam: Cam;
  declare drag: Pt | null;
  declare moved: boolean;
  declare pointers: Map<number, Pt>;
  declare pinch: { gap: number; k: number; mid: [number, number] } | null;
  declare running: boolean;
  declare raf: number;
  declare dirty: boolean;
  declare lastFrame: number;
  declare w: number;
  declare h: number;
  declare mode: string;
  declare note: string;
  declare arr: Arrangement;
  declare settleStart: number;
  declare settled: boolean;
  declare _resize: () => void;
  declare _ro: ResizeObserver | undefined;
  declare _up: (e: PointerEvent) => void;
  declare _keyDown: (e: KeyboardEvent) => void;

  /* `nodes` and `edges` are the /api/graph payload; `colorOf(type)` gives a type's CSS colour.
     Callbacks: onSelect, onSelectDomain, onOpen, onHover, onLink, onSettle, obstacles(). */
  constructor(canvas: HTMLCanvasElement, {
    nodes, edges, colorOf,
    onSelect = () => {}, onSelectDomain = () => {}, onOpen = () => {},
    onHover = () => {}, onLink = () => {}, onSettle = () => {},
    obstacles = () => [], mode = DEFAULT_MODE, show = {},
  }: GraphOptions) {
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('getContext("2d") returned null');
    this.cv = canvas;
    this.ctx = ctx;
    this.cb = { onSelect, onSelectDomain, onOpen, onHover, onLink, onSettle, obstacles };
    this.colorOf = colorOf;

    this.nodes = nodes.map((n, i): GraphNode => ({
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

    /* Relations are off unless asked for: at a store's scale they draw a
       mesh over the arrangement, and the hover draws one memory's own. */
    this.show = {
      links: show.links === true,
      domains: show.domains !== false,
      names: show.names !== false,
    };
    this.hover = null; this.selected = null; this.cameFrom = null;
    this.selectedDomain = null;
    /* The selection's set and the brighter hover set, kept apart. */
    this.focusSet = null;
    this.lit = null;
    this.linkMode = false; this.linkFrom = null;
    this.spotlit = false;

    this.palette = readPalette();
    this.cam = new Cam();
    this.drag = null; this.moved = false;
    this.pointers = new Map(); this.pinch = null;
    this.running = true;
    this.raf = 0;
    this.dirty = true;
    this.lastFrame = 0;

    this._loop = this._loop.bind(this);

    /* The frame can change size without a window resize (rail collapse, scrollbar), so the
       backing store and pointer space follow the element too. */
    this._resize = () => this.resize();
    addEventListener('resize', this._resize);
    if (typeof ResizeObserver === 'function') {
      this._ro = new ResizeObserver(this._resize);
      if (canvas.parentElement) this._ro.observe(canvas.parentElement);
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

  destroy(): void {
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

  /* Build an arrangement and frame it; selection, spotlight and toggles are the reader's state
     and survive. */
  setMode(id: string, { fit = true }: { fit?: boolean } = {}): string {
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

  setShow(patch: Partial<Show>): Show {
    this.show = { ...this.show, ...patch };
    this.dirty = true;
    this._wake();
    return this.show;
  }

  /* ----------------------------------------------------------- the frame */

  resize(): void {
    const host = this.cv.parentElement;
    if (!host) return;
    const r = host.getBoundingClientRect();
    const dpr = Math.min(2, devicePixelRatio || 1);
    this.w = Math.max(1, r.width); this.h = Math.max(1, r.height);
    this.cv.width = Math.round(this.w * dpr);
    this.cv.height = Math.round(this.h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.cam.resize(this.w, this.h);
    /* A big size change reframes an untouched arrangement; a camera the reader moved stays put. */
    if (this.arr && !this.cam.touched) this.cam.frame(this.arr.box());
    this.dirty = true;
    this._wake();
  }

  /* The rectangles the floating chrome occupies, padded, so no name is drawn
     where a panel covers it. */
  _taken(): Rect[] {
    return this.cb.obstacles().map(([x, y, w, h]) =>
      ({ x: x - 6, y: y - 6, w: w + 12, h: h + 12 }));
  }

  fade(uid: string): number {
    const node = this.byUid.get(uid);
    if (!node) return 1;
    let v = node.miss ? DIM : 1;
    if (this.focusSet && !this.focusSet.has(uid)) v = Math.min(v, FOCUS_DIM);
    return v;
  }

  /* What every arrangement is handed, rebuilt per frame so it is never stale. */
  env(): Env {
    const marks: Mark[] = [];
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
      /* No names while the arrangement moves: they would be placed against a stale frame. */
      show: {
        ...this.show,
        domains: this.show.domains && this.settled,
        names: this.show.names && this.settled,
      },
      hover: this.hover, selected: this.selected, linkFrom: this.linkFrom,
      /* what the pointer is standing on, as uids: the hovered memory and its
         neighbours, or every memory in the hovered domain */
      lit: this.lit,
      marks,
      taken: this._taken(),
      colorOf: this.colorOf,
      fade: uid => this.fade(uid),
      /* Whether a path is in scope (selected domain, else hovered), under it, or an ancestor;
         ancestors stay legible so the lit branch can be read. */
      inScope: (path: string) => {
        const at = this.selectedDomain
          || (this.hover && !this.hover.uid ? this.hover.domain : null);
        if (!at || !path) return true;
        return path === at || path.startsWith(`${at}/`) || at.startsWith(`${path}/`);
      },
      font: (weight: number, size: number) => `${weight} ${size}px ${this.palette.font}`,
    };
  }

  fit(): void {
    this.cam.frame(this.arr.box(), FIT_MS);
    this.dirty = true;
    this._wake();
  }

  _wake(): void {
    if (!this.running || this.raf) return;
    this.raf = requestAnimationFrame(this._loop);
  }

  _loop(now: number): void {
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
        if (!this.cam.touched) this.cam.frame(this.arr.box(), FIT_MS);
      } else if (!this.cam.touched && !this.drag && !this.pinch) {
        /* the arrangement condensing and the frame pulling back are one
           movement: the only authored moment this view has */
        this.cam.chase(this.arr.box(), ms);
      }
      this.cb.onSettle(this.arr.progress, this.settled);
    }
    const moving = this.cam.advance(ms);

    if (this.dirty || moving || busy) {
      this.dirty = false;
      this.draw();
    }
    if (moving || busy) this._wake();
  }

  draw(): void {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.w, this.h);
    this.arr.draw(ctx, this.cam, this.env());
  }

  /* ------------------------------------------------------- interaction */

  _local(e: MouseEvent): [number, number] {
    const r = this.cv.getBoundingClientRect();
    /* the backstop for a resize that never arrived: the rect is being read
       anyway, and a stale camera answers a click with the wrong memory */
    if (Math.abs(r.width - this.w) > 1 || Math.abs(r.height - this.h) > 1) this.resize();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  /* What is under a canvas point: a memory, a domain body, or nothing. */
  at(sx: number, sy: number): Hit | null {
    const p = this.cam.toWorld(sx, sy);
    return this.arr.hit(p.x, p.y, this.cam);
  }

  _down(e: PointerEvent): void {
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

  _move(e: PointerEvent): void {
    if (this.pointers.has(e.pointerId)) {
      this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    }
    if (this.pinch && this.pointers.size >= 2) {
      const [a, b] = [...this.pointers.values()];
      const gap = Math.max(1, Math.hypot(a.x - b.x, a.y - b.y));
      const r = this.cv.getBoundingClientRect();
      const mid: [number, number] = [(a.x + b.x) / 2, (a.y + b.y) / 2];
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
      /* computed on change, not per frame: a domain's set is a pass over every memory */
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

  _pointerUp(e?: PointerEvent): void {
    if (e) this.pointers.delete(e.pointerId);
    if (this.pointers.size < 2) this.pinch = null;
    if (this.pointers.size) return;
    this.drag = null;
    this.cv.classList.remove('grabbing');
  }

  _wheel(e: WheelEvent): void {
    e.preventDefault();
    const [x, y] = this._local(e);
    this.cam.zoomAt(x, y, Math.exp(-e.deltaY * 0.0016));
    this.dirty = true;
    this._wake();
  }

  _click(e: MouseEvent): void {
    if (this.moved) { this.moved = false; return; }
    const found = this.at(...this._local(e));
    if (this.linkMode && found && found.uid) {
      const node = this.byUid.get(found.uid);
      if (!node) return;
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
    this.select(found?.uid ?? null);
  }

  _onKeyDown(e: KeyboardEvent): void {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
    const el = document.activeElement;
    if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA'
               || (el instanceof HTMLElement && el.isContentEditable))) return;
    if (!this.selected) return;
    if (e.key === 'ArrowRight') { e.preventDefault(); this.hop(1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); this.hop(-1); }
    else if (e.key === 'Enter') { e.preventDefault(); this.cb.onOpen(this.selected); }
  }

  /* --------------------------------------------------------- the reader */

  /* Select a memory and travel to it. Passing null clears the selection and
     leaves the camera where it is -- dismissing a card is not a journey. */
  select(uid: string | null, { fly = true }: { fly?: boolean } = {}): void {
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

/* Select a domain: what is filed in or under it keeps its strength; null clears it. */
  selectDomain(path: string | null): void {
    if (this.selected) {
      this.selected = null;
      this.cameFrom = null;
      this.cb.onSelect(null);
    }
    this.selectedDomain = path || null;
    const held = path ? this.inDomain(path) : null;
    this.focusSet = held;
    this.dirty = true;
    this._wake();
    this.cb.onSelectDomain(
      path && held ? { domain: path, count: held.size } : null);
  }

  /* Every memory filed at `path` or under it; `also` paths are ignored, or a leaf would light
     half the store. */
  inDomain(path: string): Set<string> {
    const under = `${path}/`;
    const out = new Set<string>();
    for (const n of this.nodes) {
      const d = n.domain || '';
      if (d === path || d.startsWith(under)) out.add(n.uid);
    }
    return out;
  }

  _focus(node: GraphNode | null): void {
    this.focusSet = node
      ? new Set([node.uid, ...this.neighbours(node.uid).map(p => p.uid)])
      : null;
    this.dirty = true;
    this._wake();
  }

  /* The set the pointer lights: a memory and its neighbours, or a domain and
     everything filed under it. */
  _around(at: Hit | null): Set<string> | null {
    if (!at) return null;
    if (!at.uid) return at.domain ? this.inDomain(at.domain) : null;
    const out = new Set<string>([at.uid]);
    for (const e of this.D.adj.get(at.uid) || [])
      out.add(e.from_uid === at.uid ? e.to_uid : e.from_uid);
    return out;
  }

  /* Bring a memory to the middle, at a zoom close enough to read its name. */
  travel(uid: string): void {
    const at = this.arr.locate?.(uid);
    if (!at) return;
    this.cam.goTo(at.x, at.y, Math.max(this.cam.k, 1), FLY_MS);
    this.dirty = true;
    this._wake();
  }

  /* Step along the selection's relations; `cameFrom` keeps a repeated key moving outward. */
  hop(step: number): GraphNode | null {
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
  neighbours(uid: string): GraphNode[] {
    const seen = new Set<string>();
    const out: GraphNode[] = [];
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
  spotlight(raw: unknown): { count: number; first: GraphNode | null } {
    const terms = String(raw || '').toLowerCase().split(/\s+/).filter(Boolean);
    let count = 0, first: GraphNode | null = null;
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

  toggleLinkMode(): boolean {
    this.linkMode = !this.linkMode;
    this.linkFrom = null;
    this.cv.classList.toggle('linkmode', this.linkMode);
    this.dirty = true;
    this._wake();
    return this.linkMode;
  }

  clearLinkFrom(): void {
    this.linkFrom = null;
    this.dirty = true;
    this._wake();
  }
}

/* A hit's identity, whichever kind it is. */
const idOf = (hit: Hit): { uid: string } | { domain: string | undefined } =>
  (hit.uid ? { uid: hit.uid } : { domain: hit.domain });

/* The theme's own colours, read once: the graph follows the stylesheet like
   the rest of the dashboard. */
function readPalette(): Palette {
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
    /* names get a halo of the page ground, readable over a dense store */
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

const haloFrom = (bg: string): string => {
  const h = bg.replace('#', '');
  const n = h.length === 3 ? h.split('').map(c => c + c).join('') : h;
  const v = parseInt(n, 16);
  if (!Number.isFinite(v)) return 'rgba(10, 10, 10, .82)';
  return `rgba(${(v >> 16) & 255}, ${(v >> 8) & 255}, ${v & 255}, .82)`;
};
