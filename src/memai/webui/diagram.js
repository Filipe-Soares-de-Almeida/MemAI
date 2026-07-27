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
   (LAYOUT_COL_W 220 / LAYOUT_ROW_H 140), so a stored arrangement has
   room to breathe without any scaling here. */
const NODE_W = 170;
const NODE_H = 48;
const DECISION_H = 66;
const LABEL_LINES = 2;

/* canvas `font` takes a literal font stack -- it does not resolve the
   CSS custom properties the rest of the UI styles with */
const FONT_UI = "'Roboto', 'Segoe UI', system-ui, sans-serif";
const FONT_MONO = "'Roboto Mono', ui-monospace, Consolas, monospace";

const nodeSize = shape => ({
  w: NODE_W,
  h: shape === 'decision' ? DECISION_H : NODE_H,
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
    this.pan = null;
    this.moved = false;
    this.hover = null;
    this.selected = null;
    this.connectMode = false;
    this.connectFrom = null;
    this.destroyed = false;
    this._frame = 0;

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
    this._resize = () => { this.resize(); this.requestDraw(); };

    canvas.addEventListener('mousedown', this._down);
    canvas.addEventListener('mousemove', this._move);
    canvas.addEventListener('wheel', this._wheel, { passive: false });
    canvas.addEventListener('click', this._click);
    addEventListener('mouseup', this._up);
    addEventListener('resize', this._resize);

    this.requestDraw();
  }

  destroy() {
    this.destroyed = true;
    cancelAnimationFrame(this._frame);
    this.cv.removeEventListener('mousedown', this._down);
    this.cv.removeEventListener('mousemove', this._move);
    this.cv.removeEventListener('wheel', this._wheel);
    this.cv.removeEventListener('click', this._click);
    removeEventListener('mouseup', this._up);
    removeEventListener('resize', this._resize);
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
    this.nodes = (data.nodes || []).map(n => {
      const shape = NODE_SHAPES.includes(n.shape) ? n.shape : 'step';
      return {
        key: n.key,
        label: n.label || '',
        note: n.note || '',
        shape,
        x: n.x,
        y: n.y,
        ...nodeSize(shape),
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
    /* structural, so compute once here rather than per frame -- dragging
       redraws on every mousemove and cannot change reachability */
    this.orphans = new Set(this.orphanKeys());
    if (fit) this.fit();
    this.requestDraw();
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
    const r = this.cv.parentElement.getBoundingClientRect();
    const dpr = devicePixelRatio || 1;
    this.w = r.width;
    this.h = r.height;
    this.cv.width = r.width * dpr;
    this.cv.height = r.height * dpr;
    this.cx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  fit() {
    if (!this.nodes.length || !this.w) return;
    const xs = this.nodes.flatMap(n => [n.x - n.w / 2, n.x + n.w / 2]);
    const ys = this.nodes.flatMap(n => [n.y - n.h / 2, n.y + n.h / 2]);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const pad = 48;
    this.scale = Math.min(1.4, Math.min(
      this.w / (maxX - minX + pad * 2),
      this.h / (maxY - minY + pad * 2)));
    this.tx = this.w / 2 - (minX + maxX) / 2 * this.scale;
    this.ty = this.h / 2 - (minY + maxY) / 2 * this.scale;
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

  onDown(e) {
    if (this.connectMode) return;
    const n = this.nodeAt(this.toWorld(e));
    if (n) {
      const p = this.toWorld(e);
      this.drag = { node: n, dx: n.x - p.x, dy: n.y - p.y };
    } else {
      this.pan = { x: e.clientX - this.tx, y: e.clientY - this.ty };
      this.cv.classList.add('dragging');
    }
  }

  onMove(e) {
    if (this.drag) {
      const p = this.toWorld(e);
      this.drag.node.x = p.x + this.drag.dx;
      this.drag.node.y = p.y + this.drag.dy;
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
    const n = this.nodeAt(this.toWorld(e));
    if (n !== this.hover) { this.hover = n; this.requestDraw(); }
    this.cv.style.cursor = this.connectMode ? 'crosshair' : n ? 'pointer' : 'grab';
    if (n && n.note) {
      this.hooks.tipShow?.(
        `<b>${esc(n.key)}</b><br>${esc(n.note.slice(0, 220))}`, e.clientX, e.clientY);
    } else {
      this.hooks.tipHide?.();
    }
  }

  onUp() {
    if (this.drag) {
      const { node } = this.drag;
      this.drag = null;
      /* optimistic: the box already moved, persistence catches up */
      this.hooks.onMove?.({ [node.key]: { x: Math.round(node.x), y: Math.round(node.y) } });
    }
    this.pan = null;
    this.cv.classList.remove('dragging');
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
    const n = this.nodeAt(this.toWorld(e));
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

  toggleConnectMode(on = !this.connectMode) {
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
      if (!this.destroyed) this.draw();
    });
  }

  /* Where a straight line from the node's centre leaves its box. */
  static exit(n, dx, dy) {
    const sx = dx === 0 ? Infinity : (n.w / 2 + 4) / Math.abs(dx);
    const sy = dy === 0 ? Infinity : (n.h / 2 + 4) / Math.abs(dy);
    const s = Math.min(sx, sy);
    return { x: n.x + dx * s, y: n.y + dy * s };
  }

  /* `grow` inflates the outline, which is how the selection ring is drawn
     without transforming the canvas mid-path. */
  shapePath(n, grow = 0) {
    const { cx } = this;
    const hw = n.w / 2 + grow, hh = n.h / 2 + grow;
    const box = [n.x - hw, n.y - hh, hw * 2, hh * 2];
    cx.beginPath();
    if (n.shape === 'decision') {
      cx.moveTo(n.x, n.y - hh);
      cx.lineTo(n.x + hw, n.y);
      cx.lineTo(n.x, n.y + hh);
      cx.lineTo(n.x - hw, n.y);
      cx.closePath();
    } else if (n.shape === 'io') {
      const skew = 14;
      cx.moveTo(n.x - hw + skew, n.y - hh);
      cx.lineTo(n.x + hw, n.y - hh);
      cx.lineTo(n.x + hw - skew, n.y + hh);
      cx.lineTo(n.x - hw, n.y + hh);
      cx.closePath();
    } else if (cx.roundRect) {
      cx.roundRect(...box, n.shape === 'start' || n.shape === 'end' ? hh : 10);
    } else {
      cx.rect(...box);
    }
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
      /* trim the tail until the ellipsis fits, so nothing spills out */
      let last = lines[maxLines - 1];
      const consumed = lines.join(' ').length;
      if (consumed < String(text).trim().length) {
        while (last && cx.measureText(`${last}…`).width > maxWidth) {
          last = last.slice(0, -1);
        }
        lines[maxLines - 1] = `${last}…`;
      }
    }
    return lines;
  }

  drawEdge(e) {
    const { cx } = this;
    const a = this.byKey[e.from], b = this.byKey[e.to];
    if (!a || !b) return;
    const back = b.y <= a.y;   /* points up the canvas: a loop closer */
    const from = DiagramEditor.exit(a, b.x - a.x, b.y - a.y);
    const to = DiagramEditor.exit(b, a.x - b.x, a.y - b.y);

    /* Bow a back-edge sideways so a retry loop does not sit on top of
       the forward path it returns to. */
    const mx = (from.x + to.x) / 2, my = (from.y + to.y) / 2;
    const bow = back ? Math.max(70, Math.abs(from.y - to.y) * 0.45) : 0;
    const ctrl = { x: mx + bow, y: my };

    cx.strokeStyle = back ? this.colWarn : this.colLine;
    cx.lineWidth = (back ? 1.3 : 1.6) / this.scale;
    if (back) cx.setLineDash([5 / this.scale, 4 / this.scale]);
    cx.beginPath();
    cx.moveTo(from.x, from.y);
    if (bow) cx.quadraticCurveTo(ctrl.x, ctrl.y, to.x, to.y);
    else cx.lineTo(to.x, to.y);
    cx.stroke();
    cx.setLineDash([]);

    /* arrowhead along the incoming tangent */
    const tanX = bow ? to.x - ctrl.x : to.x - from.x;
    const tanY = bow ? to.y - ctrl.y : to.y - from.y;
    const d = Math.hypot(tanX, tanY) || 1;
    const ux = tanX / d, uy = tanY / d;
    const s = 8 / Math.sqrt(this.scale);
    cx.beginPath();
    cx.moveTo(to.x, to.y);
    cx.lineTo(to.x - ux * s - uy * s * 0.5, to.y - uy * s + ux * s * 0.5);
    cx.lineTo(to.x - ux * s + uy * s * 0.5, to.y - uy * s - ux * s * 0.5);
    cx.closePath();
    cx.fillStyle = back ? this.colWarn : this.colInk2;
    cx.fill();

    if (e.label && this.scale > 0.4) {
      const lx = bow ? (from.x + 2 * ctrl.x + to.x) / 4 : mx;
      cx.font = `${11 / this.scale}px ${FONT_MONO}`;
      const wide = cx.measureText(e.label).width;
      cx.fillStyle = this.colSurface;
      cx.fillRect(lx - wide / 2 - 4 / this.scale, my - 8 / this.scale,
                  wide + 8 / this.scale, 15 / this.scale);
      cx.fillStyle = back ? this.colWarn : this.colInk2;
      cx.textAlign = 'center';
      cx.textBaseline = 'middle';
      cx.fillText(e.label, lx, my);
    }
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
    cx.font = `${11.5 / this.scale}px ${FONT_UI}`;
    cx.fillStyle = terminal ? '#000' : this.colInk;
    cx.textAlign = 'center';
    cx.textBaseline = 'middle';
    const lines = this.wrap(n.label, n.w - (n.shape === 'decision' ? 54 : 20), LABEL_LINES);
    const lh = 13 / this.scale;
    const top = n.y - (lines.length - 1) * lh / 2;
    lines.forEach((line, i) => cx.fillText(line, n.x, top + i * lh));

    /* a step that explains itself carries a marker, so the notes and
       links attached to it are discoverable without clicking every box */
    const badges = (n.note ? '𝒊' : '') + (this.linkCount[n.key] ? ` ⇱${this.linkCount[n.key]}` : '');
    if (badges.trim() && this.scale > 0.45) {
      cx.font = `${10 / this.scale}px ${FONT_MONO}`;
      cx.fillStyle = terminal ? 'rgba(0,0,0,.6)' : this.colInk2;
      cx.textAlign = 'right';
      cx.fillText(badges.trim(), n.x + n.w / 2 - 6 / this.scale, n.y - n.h / 2 + 9 / this.scale);
    }
  }

  draw() {
    const { cx } = this;
    cx.clearRect(0, 0, this.w, this.h);
    cx.save();
    cx.translate(this.tx, this.ty);
    cx.scale(this.scale, this.scale);

    for (const e of this.edges) this.drawEdge(e);
    for (const n of this.nodes) this.drawNode(n, this.orphans.has(n.key));

    cx.restore();
  }
}
