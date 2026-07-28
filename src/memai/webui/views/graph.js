/* The relations graph: a small canvas force layout.

   Repulsion is O(n²) per pass, which is why the server caps how many
   nodes it will hand over (see admin.graph) and why the initial settle
   below is a budget rather than a fixed pass count. The layout is
   computed here and never stored -- unlike the diagram editor, whose
   coordinates come from the store; see diagram.js for that contrast. */

import { $, esc, fmtInt, cssVar } from '../core/dom.js';
import { api, query } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, failed, tipShow, tipHide, openModal, closeModal, setPressed } from '../core/ui.js';
import { typeTag, typeColor, uidChip, statusTag, confPill, wireCopyChips,
         getDomains, TYPE_ORDER, TYPE_LABEL, relOptions } from '../core/shared.js';
import { go, refreshBehind } from '../core/router.js';
import { onTeardown } from '../core/lifecycle.js';
import { openRecord } from './record.js';
import { t } from '../i18n.js';

/* alpha below which the simulation is considered at rest */
const SETTLE = 0.02;
/* pair-distance checks to spend on the pre-paint settle, total */
const SETTLE_BUDGET = 2e6;

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
        <select id="gDomain" aria-label="${t('common.allDomains')}">
          <option value="">${t('common.allDomains')}</option>
          ${domains.map(d => `<option value="${esc(d.domain)}" ${d.domain === state.domain ? 'selected' : ''}>${esc(d.domain)}</option>`).join('')}
        </select>
        <select id="gType" aria-label="${t('common.allTypes')}">
          <option value="">${t('common.allTypes')}</option>
          ${TYPE_ORDER.map(tp => `<option value="${tp}" ${tp === state.type ? 'selected' : ''}>${TYPE_LABEL[tp]}</option>`).join('')}
        </select>
        <div class="seg" role="group" aria-label="${t('mem.status.aria')}">
          <button type="button" data-v="active" aria-pressed="${state.status === 'active'}">${t('common.active')}</button>
          <button type="button" data-v="" aria-pressed="${state.status === ''}">${t('common.all')}</button>
        </div>
        <button type="button" class="btn btn-sm" id="gLink" aria-pressed="false">${icon('pencil')}${t('g.linkMode')}</button>
        <button type="button" class="btn btn-sm" id="gFit">${t('g.center')}</button>
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
  $('#gDomain').addEventListener('change', e => nav({ domain: e.target.value }));
  $('#gType').addEventListener('change', e => nav({ type: e.target.value }));
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
}

class ForceGraph {
  constructor(canvas, nodes, edges) {
    this.cv = canvas;
    this.cx = canvas.getContext('2d');
    const R = Math.sqrt(nodes.length + 1) * 60;
    this.nodes = nodes.map((n, i) => ({
      ...n,
      x: Math.cos(i * 2.399963) * R * Math.sqrt((i + 1) / (nodes.length + 1)),
      y: Math.sin(i * 2.399963) * R * Math.sqrt((i + 1) / (nodes.length + 1)),
      vx: 0, vy: 0,
      r: 5.5 + Math.min(8, n.degree * 1.5),
    }));
    this.byUid = Object.fromEntries(this.nodes.map(n => [n.uid, n]));
    this.edges = edges.filter(e => this.byUid[e.from_uid] && this.byUid[e.to_uid]);
    this.tx = 0; this.ty = 0; this.scale = 1;
    this.fitScale = null;   /* set by fit(); it is the zoom-out floor, see zoomAt */
    this.alpha = 1;
    this.hover = null; this.selected = null;
    this.linkMode = false; this.linkFrom = null;
    this.drag = null; this.pan = null;
    this.running = true;
    this.raf = 0;
    /* theme colors resolved once from CSS custom properties */
    this.colAccent = cssVar('--accent') || '#bb86fc';
    this.colRing = cssVar('--bg') || '#121212';
    this.colEdge = cssVar('--canvas-edge') || 'rgba(255,255,255,.25)';
    this.colArrow = cssVar('--canvas-arrow') || 'rgba(255,255,255,.38)';
    this.colLabel = cssVar('--ink') || 'rgba(255,255,255,.87)';
    /* pointers currently down, by id: one drags or pans, two pinch */
    this.pointers = new Map();
    this.pinch = null;

    /* bound before resize(), which kicks the simulation and so needs a
       callable frame handler already in place */
    this.loop = this.loop.bind(this);
    this._resize = this.resize.bind(this);
    addEventListener('resize', this._resize);
    this.resize();

    /* Settle synchronously so the graph is born calm -- and painted at
       least once even where rAF is throttled (headless, hidden tabs).
       The pass count is a BUDGET, not a constant: each pass is O(n²), so
       the old fixed 900 was 40M distance checks at 300 nodes and froze
       the tab before it had drawn anything. Fewer passes on a big graph
       means a looser start, which the live loop then keeps improving. */
    const pairs = Math.max(1, this.nodes.length * (this.nodes.length - 1) / 2);
    const passes = Math.max(60, Math.min(900, Math.floor(SETTLE_BUDGET / pairs)));
    for (let k = 0; k < passes && this.alpha > .05; k++) this.physics();
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
  /* Resume the simulation, optionally stirring it back up first. */
  kick(alpha = 0) {
    if (alpha) this.alpha = Math.max(this.alpha, alpha);
    this.wake();
  }
  wake() {
    if (!this.running || this.raf) return;
    this.raf = requestAnimationFrame(this.loop);
  }
  /* One frame, for a change that is only visual (hover, selection, pan,
     zoom) and must not restart the physics. */
  requestDraw() {
    if (!this.running || this.raf) return;
    this.raf = requestAnimationFrame(() => { this.raf = 0; this.draw(); });
  }
  resize() {
    const r = this.cv.parentElement.getBoundingClientRect();
    const dpr = devicePixelRatio || 1;
    this.w = r.width; this.h = r.height;
    this.cv.width = r.width * dpr; this.cv.height = r.height * dpr;
    this.cx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.kick(.3);
  }
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
  nodeAt(p) {
    for (let i = this.nodes.length - 1; i >= 0; i--) {
      const n = this.nodes[i];
      const d2 = (n.x - p.x) ** 2 + (n.y - p.y) ** 2;
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
    if (n) { this.drag = n; this.kick(.35); }
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
      this.drag.x = p.x; this.drag.y = p.y;
      this.drag.vx = this.drag.vy = 0;
      this.moved = true;
      tipHide();
      this.kick(.25);
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
  promptLink(a, b) {
    const modal = openModal({
      title: t('g.modal.title'),
      bodyHTML: `
        <div style="display:grid;gap:6px;font-size:11.5px">
          <div><span class="dot" style="--c:${typeColor(a.type)};display:inline-block;margin-right:6px"></span>${esc(a.uid)} · ${esc(a.label)}</div>
          <div style="color:var(--accent);padding-left:2px;--ico:15px">${icon('arrow-down')}</div>
          <div><span class="dot" style="--c:${typeColor(b.type)};display:inline-block;margin-right:6px"></span>${esc(b.uid)} · ${esc(b.label)}</div>
        </div>
        <div class="field"><label for="glType">${t('g.modal.relType')}</label>
          <input type="text" id="glType" list="glTypesDL" value="relates_to">
          <datalist id="glTypesDL">${relOptions()}</datalist></div>
        <div class="field"><label for="glNote">${t('g.modal.note')}</label><input type="text" id="glNote"></div>`,
      footHTML: `<button class="btn" data-x>${t('common.cancel')}</button><button class="btn btn-solid" data-ok>${t('g.modal.create')}</button>`,
    });
    const mq = s => modal.querySelector(s);
    mq('[data-x]').onclick = () => { closeModal(); this.linkFrom = null; this.requestDraw(); };
    mq('[data-ok]').onclick = async () => {
      try {
        await api('/api/relations', { body: {
          from_uid: a.uid, to_uid: b.uid,
          relation_type: mq('#glType').value.trim() || 'relates_to',
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
        <span class="chip">${t('g.links', { n: n.degree })}</span>
      </div>`;
    wireCopyChips(card);
    card.querySelector('[data-openrec]').addEventListener('click', () => openRecord(n.uid));
  }
  physics() {
    if (this.alpha < SETTLE) return;
    const N = this.nodes;
    for (let i = 0; i < N.length; i++) {
      const a = N[i];
      for (let j = i + 1; j < N.length; j++) {
        const b = N[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = (Math.random() - .5); dy = (Math.random() - .5); d2 = 1; }
        if (d2 > 160000) continue;
        const f = 900 / d2 * this.alpha;
        const d = Math.sqrt(d2);
        dx /= d; dy /= d;
        a.vx += dx * f; a.vy += dy * f;
        b.vx -= dx * f; b.vy -= dy * f;
      }
    }
    for (const e of this.edges) {
      const a = this.byUid[e.from_uid], b = this.byUid[e.to_uid];
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.max(1, Math.hypot(dx, dy));
      const f = (d - 85) * .012 * this.alpha;
      a.vx += dx / d * f; a.vy += dy / d * f;
      b.vx -= dx / d * f; b.vy -= dy / d * f;
    }
    for (const n of N) {
      n.vx -= n.x * .0016 * this.alpha;
      n.vy -= n.y * .0016 * this.alpha;
      if (n === this.drag) continue;
      n.vx *= .86; n.vy *= .86;
      n.x += n.vx; n.y += n.vy;
    }
    this.alpha *= .996;
  }
  draw() {
    const { cx } = this;
    cx.clearRect(0, 0, this.w, this.h);
    cx.save();
    cx.translate(this.tx, this.ty);
    cx.scale(this.scale, this.scale);

    cx.strokeStyle = this.colEdge;
    cx.lineWidth = 1 / this.scale;
    for (const e of this.edges) {
      const a = this.byUid[e.from_uid], b = this.byUid[e.to_uid];
      cx.beginPath(); cx.moveTo(a.x, a.y); cx.lineTo(b.x, b.y); cx.stroke();
      const d = Math.hypot(b.x - a.x, b.y - a.y) || 1;
      const ux = (b.x - a.x) / d, uy = (b.y - a.y) / d;
      const px = b.x - ux * (b.r + 4), py = b.y - uy * (b.r + 4);
      const s = 4 / Math.sqrt(this.scale);
      cx.beginPath();
      cx.moveTo(px, py);
      cx.lineTo(px - ux * s - uy * s * .6, py - uy * s + ux * s * .6);
      cx.lineTo(px - ux * s + uy * s * .6, py - uy * s - ux * s * .6);
      cx.closePath();
      cx.fillStyle = this.colArrow;
      cx.fill();
    }

    for (const n of this.nodes) {
      const color = typeColor(n.type);
      cx.beginPath();
      if (n.type === 'anti_pattern') {          /* diamond: secondary encoding for the red↔green CVD pair */
        cx.moveTo(n.x, n.y - n.r); cx.lineTo(n.x + n.r, n.y);
        cx.lineTo(n.x, n.y + n.r); cx.lineTo(n.x - n.r, n.y);
        cx.closePath();
      } else {
        cx.arc(n.x, n.y, n.r, 0, 7);
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
        cx.arc(n.x, n.y, n.r + 5, 0, 7);
        cx.strokeStyle = this.colAccent;
        cx.lineWidth = 1.6 / this.scale;
        if (n === this.linkFrom) cx.setLineDash([4 / this.scale, 3 / this.scale]);
        cx.stroke();
        cx.setLineDash([]);
      }
    }

    if (this.hover && this.scale > .35) {
      const n = this.hover;
      cx.font = `${11 / this.scale}px 'Roboto Mono', monospace`;
      cx.fillStyle = this.colLabel;
      cx.fillText(n.label.slice(0, 46), n.x + n.r + 7 / this.scale, n.y + 4 / this.scale);
    }
    cx.restore();
  }
  loop() {
    this.raf = 0;
    if (!this.running) return;
    this.physics();
    this.draw();
    /* Stop once the layout is at rest. This used to run forever, redrawing
       an identical canvas at 60fps on a graph nobody was touching; every
       interaction path above calls kick() or requestDraw() to come back. */
    if (this.alpha > SETTLE || this.drag || this.pan) {
      this.raf = requestAnimationFrame(this.loop);
    }
  }
}
