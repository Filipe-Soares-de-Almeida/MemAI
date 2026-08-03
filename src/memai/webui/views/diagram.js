/* The diagram editor view: toolbar, inspector, and everything the canvas
   engine (../diagram-engine.js) reaches back out through hooks for.

   The engine owns geometry and drawing and nothing else -- no dialogs, no
   API calls, no DOM outside its canvas. Every write below goes through
   act(), which reloads from the store afterwards, because the store is
   what decided the layout in the first place. */

import { $, esc, debounce } from '../core/dom.js';
import { api, seg } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, failed, openModal, closeModal, confirmModal, promptModal,
         openCtxMenu, tipShow, tipHide, setPressed } from '../core/ui.js';
import { typeClass, DG_REL_SUGGEST } from '../core/shared.js';
import { pickerFor, pickerValue, wirePicker, fixedItems } from '../core/pick.js';
import { pickMemories } from '../core/link-picker.js';
import { onTeardown } from '../core/lifecycle.js';
import { openRecord } from './record.js';
import { DiagramEditor, NODE_SHAPES, FONT_SCALES } from '../diagram-engine.js';
import { t } from '../i18n.js';

const shapeItems = () => NODE_SHAPES.map(s => ({ value: s, label: t(`dg.shape.${s}`) }));

/* One modal for "new step" and for retyping an existing one. Resolves to
   {key, label, shape} or null. */
function dgStepModal({ title, key = '', label = '', shape = 'step', lockKey = false }) {
  return new Promise(resolve => {
    const m = openModal({
      title,
      bodyHTML: `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
          <div class="field"><label for="dgsKey">${t('dg.key')}</label>
            <input type="text" id="dgsKey" value="${esc(key)}" placeholder="${t('dg.keyPh')}"
                   ${lockKey ? 'disabled' : ''} autocomplete="off"></div>
          <div class="field"><label for="dgsShape">${t('dg.shape')}</label>
            ${pickerFor({ id: 'dgsShape', value: shape, items: shapeItems(),
                          ariaLabel: t('dg.shape') })}</div>
        </div>
        <div class="field"><label for="dgsLabel">${t('dg.label')}</label>
          <input type="text" id="dgsLabel" value="${esc(label)}" placeholder="${t('dg.labelPh')}"></div>
        <div class="dg-empty">${t('dg.labelHint')}</div>`,
      footHTML: `<button class="btn" data-x>${t('common.cancel')}</button>
                 <button class="btn btn-solid" data-ok>${t('common.save')}</button>`,
    });
    const mq = s => m.querySelector(s);
    wirePicker(m, { id: 'dgsShape', items: fixedItems(shapeItems()), onPick: () => {} });
    mq('[data-x]').onclick = () => { closeModal(); resolve(null); };
    mq('[data-ok]').onclick = () => {
      const out = {
        key: (lockKey ? key : mq('#dgsKey').value).trim(),
        label: mq('#dgsLabel').value.trim(),
        shape: pickerValue(m, 'dgsShape'),
      };
      closeModal();
      resolve(out);
    };
  });
}

/* Where a jump goes, as an address. A real href and not a click handler:
   the flow you are leaving is worth keeping, so ctrl-click and the middle
   button have to open the destination in another tab -- which they do for
   free on an <a>, and cannot be made to do on a <span>. `node` is what the
   arriving view focuses; `from`/`fromNode` are the step being left, which
   is what lets the arriving canvas offer the way back (see renderDiagram). */
const enc = encodeURIComponent;
const jumpHref = (j, from) =>
  `#/diagram?uid=${enc(j.peer_uid)}`
  + (j.peer_node ? `&node=${enc(j.peer_node)}` : '')
  + `&from=${enc(from)}`
  + (j.node_key ? `&fromNode=${enc(j.node_key)}` : '');

/* The way back, with no `from` of its own: this is a RETURN, and a return
   that announced a way back to where it returned from would leave both
   flows offering to go to the other one forever. */
const backHref = (uid, node) =>
  `#/diagram?uid=${enc(uid)}` + (node ? `&node=${enc(node)}` : '');

/* How many chips the corner offers before folding the rest behind a +N, and
   how much of a flow's name one chip carries.

   Both are here because the corner is the one thing on the canvas that grows
   with the DATA rather than with the diagram. A chip used to carry a whole
   flow title: measured on a 614-wide stage, that came out 590 wide -- 96% of
   the canvas -- and was ellipsised anyway, so the width bought no text. And
   they stacked, one row each: at 27px a row, ten ties took 55% of the stage
   height, and insets() then handed fit() what was left, so a well-connected
   flow drew itself smaller the more it was connected.

   A short name fits several to a row, and the cap is what keeps it to one
   row no matter how many ties a step grows.

   Three and not four: a chip is capped at 10rem, so three of them plus the
   +N button fit one row down to a stage about 550 wide -- which is what the
   stage IS once the inspector has its share. Four measured 603 against 590
   of room and wrapped, which is the thing this exists to stop. */
const JUMP_CHIPS_SHOWN = 3;
const CHIP_LABEL_CHARS = 20;

/* A flow's name cut to what a chip can carry. The part before the first
   dash-like separator, which is where a title carries its identifier when it
   has one, and clamped so a title with no separator is short anyway. The
   whole title stays in the chip's tooltip -- that is where a reader asks
   which flow this is, rather than where it goes. */
const chipLabel = title => {
  const full = String(title ?? '').trim();
  const head = full.split(/\s+[\u2014\u2013-]\s+/)[0].trim() || full;
  if (head.length <= CHIP_LABEL_CHARS) return head;
  return `${head.slice(0, CHIP_LABEL_CHARS).replace(/[\s,;:.\-/]+$/, '')}\u2026`;
};

/* Which step of the target flow to arrive on, and why the flow continues
   there. Second half of adding a jump: the first half picked the diagram,
   and only that diagram knows what its steps are called. */
function dgJumpTargetModal(target) {
  /* the whole flow is a destination too, and the first row says so */
  const nodeItems = [
    { value: '', label: t('dg.jump.wholeDiagram'),
      html: `<span class="pick-any">${t('dg.jump.wholeDiagram')}</span>` },
    ...target.nodes.map(n => ({ value: n.key, label: `${n.key} · ${n.label}` })),
  ];
  return new Promise(resolve => {
    const m = openModal({
      title: t('dg.jump.step.title', { title: esc(target.title) }),
      bodyHTML: `
        <div class="field"><label for="dgjNode">${t('dg.jump.step.label')}</label>
          ${pickerFor({ id: 'dgjNode', items: nodeItems, ariaLabel: t('dg.jump.step.label') })}</div>
        <div class="field"><label for="dgjLabel">${t('dg.jump.label')}</label>
          <input type="text" id="dgjLabel" placeholder="${t('dg.jump.labelPh')}"
                 autocomplete="off"></div>
        <div class="dg-empty">${t('dg.jump.step.hint')}</div>`,
      footHTML: `<button class="btn" data-x>${t('common.cancel')}</button>
                 <button class="btn btn-solid" data-ok>${t('dg.jump.add')}</button>`,
    });
    wirePicker(m, { id: 'dgjNode', items: fixedItems(nodeItems), onPick: () => {} });
    m.querySelector('[data-x]').onclick = () => { closeModal(); resolve(null); };
    m.querySelector('[data-ok]').onclick = () => {
      const out = {
        node: pickerValue(m, 'dgjNode'),
        label: m.querySelector('#dgjLabel').value.trim(),
      };
      closeModal();
      resolve(out);
    };
  });
}

export async function renderDiagram(view, params, ctx) {
  const uid = params.get('uid') || '';
  /* Arrived from a jump on another flow: the step to land on, and the step
     it was left from. Read once -- they are where this render starts, not a
     filter the view keeps applying. */
  const landOn = params.get('node') || '';
  const cameFrom = params.get('from') || '';
  const cameFromNode = params.get('fromNode') || '';
  if (!uid) {
    view.innerHTML = `<div class="empty">${t('dg.noUid')}</div>`;
    return;
  }
  const mem = await api(`/api/memories/${seg(uid)}`);
  if (ctx.stale()) return;
  if (mem.type !== 'diagram' || !mem.diagram) {
    view.innerHTML = `<div class="empty">${t('dg.notDiagram')}</div>`;
    return;
  }
  let data = mem.diagram;
  let selected = null;
  /* Read-only until asked otherwise: reading a flow is the common case, and
     a stray drag while reading rewrites a position every other reader sees. */
  let editing = false;

  view.innerHTML = `<div class="anim">
    <div class="view-head">
      <h2 class="view-title" id="dgTitle">${esc(data.title)}</h2>
      <div class="view-sub"><a href="#/diagrams" class="dgl-back">${t('dgl.title')}</a> · <span id="dgSub"></span></div>
    </div>
    <div class="dg-shell">
      <div class="dg-main">
        <!-- above the canvas, not floating on it: a card dragged to the top
             of the flow used to slide under the buttons -->
        <div class="dg-tools" id="dgTools">
          <button class="btn btn-sm" id="dgMode"></button>
          <button class="btn btn-sm" id="dgAdd" data-editonly>${t('dg.add')}</button>
          <button class="btn btn-sm" id="dgConnect" data-editonly>${t('dg.connect')}</button>
          <button class="btn btn-sm" id="dgArrange" data-editonly>${t('dg.arrange')}</button>
          <button class="btn btn-sm" id="dgFont" data-editonly></button>
          <button class="btn btn-sm" id="dgFit">${t('dg.center')}</button>
          <button class="btn btn-sm" id="dgMermaid">${t('dg.mermaid')}</button>
          <button class="btn btn-sm" id="dgRecord">${t('dg.record')}</button>
        </div>
        <div class="dg-stage" id="dgStage">
          <!-- A drawing. The flow itself is readable as text two ways that do
               not need this canvas: the inspector beside it lists a step's
               connections and note, and the Mermaid button exports the whole
               graph. The label says so rather than claiming the picture is
               navigable. -->
          <canvas id="dgCanvas" role="img" aria-label="${esc(t('dg.canvasAlt', { title: data.title }))}"></canvas>
          <!-- Top-left corner: the way OUT of this flow, and the way back
               into the one that sent you here. It is on the canvas because
               that is where the card you clicked is -- the same tie listed
               in the inspector was four panels down a column you had to
               scroll to reach. -->
          <div class="dg-jumpnav" id="dgJumpNav" hidden></div>
          <div class="dg-overlay" id="dgOverlay">
            <div class="dg-hint" id="dgHint" hidden></div>
            <div class="dg-legend" id="dgLegend"></div>
          </div>
        </div>
      </div>
      <div class="dg-side" id="dgSide"></div>
    </div>
  </div>`;

  let engine = null;

  /* ── layout persistence: optimistic on the canvas, batched to the API ── */
  const pending = {};
  const flushLayout = debounce(async () => {
    const batch = { ...pending };
    Object.keys(pending).forEach(k => delete pending[k]);
    if (!Object.keys(batch).length) return;
    try {
      await api(`/api/diagrams/${seg(uid)}/layout`, { body: { positions: batch } });
    } catch (err) {
      /* the canvas is now lying about where things are; take the store's word */
      failed('err.diagram', err);
      reload();
    }
  }, 450);

  async function reload({ fit = false } = {}) {
    data = await api(`/api/diagrams/${seg(uid)}`);
    engine.setData(data, { fit });
    if (selected && !data.nodes.some(n => n.key === selected)) selected = null;
    if (selected) engine.select(selected);
    paintSide();
    paintHint();
    paintFont();
    $('#dgTitle').textContent = data.title;
  }

  const act = async (fn, okMsg) => {
    try { await fn(); if (okMsg) toast(okMsg, 'ok'); await reload(); }
    catch (err) { failed('err.diagram', err); }
  };

  /* What the strokes on the canvas mean. Static, so it is painted once --
     but it lives here rather than in index.html because the samples come
     from core/icons.js and the words from the catalogs. */
  function paintLegend() {
    const rows = [
      ['line-plain', 'var(--ink-2)', t('dg.legend.plain')],
      ['line-loop', 'var(--warn)', t('dg.legend.loop')],
      ['line-hot', 'var(--accent)', t('dg.legend.selected')],
    ];
    $('#dgLegend').innerHTML = rows.map(([sample, color, label]) =>
      `<span class="dg-legend-item"><span style="color:${color};display:inline-flex">${
        icon(sample, { cls: 'ico-line' })}</span>${label}</span>`).join('');
  }

  /* ── hint strip ─────────────────────────────────────────────────────
     Only speaks when it has something to say: which end of a connection
     is being picked, or which steps the flow cannot reach. It used to
     also carry a standing "drag to move" line, which just repeated what
     dragging a box already tells you. */
  function paintHint(connectFrom) {
    const el = $('#dgHint');
    const orphans = engine ? [...engine.orphans] : [];
    const edge = engine?.selectedEdge;
    if (engine?.connectMode) {
      el.hidden = false;
      el.className = 'dg-hint';
      el.textContent = connectFrom
        ? t('dg.hint.connectTarget', { key: connectFrom.key })
        : t('dg.hint.connectSource');
    } else if (edge) {
      /* the picture already highlights it; this says the same thing in
         words, which is what you read when the two ends are off-screen */
      el.hidden = false;
      el.className = 'dg-hint';
      el.textContent = t('dg.hint.edgeSelected', {
        from: edge.from, to: edge.to,
        label: edge.label ? ` · ${edge.label}` : '',
      });
    } else if (orphans.length) {
      el.hidden = false;
      el.className = 'dg-hint warn';
      el.textContent = t('dg.hint.orphans', { keys: orphans.join(', ') });
    } else {
      el.hidden = true;
      el.textContent = '';
    }
    $('#dgSub').textContent = t('dg.sub', {
      n: data.nodes.length, m: data.edges.length, l: data.links.length,
      j: data.jumps.length,
    });
  }

  /* The condition written on a line, edited from the line itself, from the
     right-click menu, or from the connection rows in the inspector. */
  const saveEdgeLabel = (edge, label) => act(() => api(`/api/diagrams/${seg(uid)}/edge`, {
    body: { from: edge.from, to: edge.to, label } }), t('dg.saved'));

  async function editEdgeLabel(edge) {
    const label = await promptModal({
      title: t('dg.edgeLabel.editTitle'),
      body: t('dg.edgeLabel.body', { from: esc(edge.from), to: esc(edge.to) }),
      label: t('dg.edgeLabel.label'), placeholder: t('dg.edgeLabel.ph'),
      value: edge.label || '', okLabel: t('common.save'),
    });
    if (label === null) return;
    await saveEdgeLabel(edge, label);
  }

  const deleteEdge = edge => act(() => api(`/api/diagrams/${seg(uid)}/edge`, {
    body: { from: edge.from, to: edge.to, delete: true } }), t('dg.disconnected'));

  async function editStep(node) {
    const out = await dgStepModal({
      title: t('dg.editStep.title'), key: node.key,
      label: node.label, shape: node.shape, lockKey: true,
    });
    if (!out) return;
    /* note deliberately not sent: the API patches only what it receives,
       and the note is edited in the inspector where there is room for it */
    await act(() => api(`/api/diagrams/${seg(uid)}/node`, {
      body: { key: node.key, label: out.label, shape: out.shape } }), t('dg.saved'));
  }

  /* The long half of a step. Reachable from the inspector, and from the card
     itself -- the marker that used to advertise a note is gone, and hovering
     only reads it. */
  function editNote(node) {
    const stored = data.nodes.find(n => n.key === node.key) || node;
    const m = openModal({
      title: t('dg.note.title', { key: esc(node.key) }),
      bodyHTML: `<div class="field"><label for="dgnNote">${t('dg.note')}</label>
          <textarea id="dgnNote" rows="9" style="min-height:210px"
                    placeholder="${t('dg.notePh')}">${esc(stored.note || '')}</textarea></div>
        <div class="dg-empty" style="margin-top:9px">${t('dg.note.hint')}</div>`,
      footHTML: `<button class="btn" data-x>${t('common.cancel')}</button>
                 <button class="btn btn-solid" data-ok>${t('common.save')}</button>`,
    });
    m.querySelector('[data-x]').onclick = closeModal;
    m.querySelector('[data-ok]').onclick = async () => {
      const note = m.querySelector('#dgnNote').value;
      closeModal();
      await act(() => api(`/api/diagrams/${seg(uid)}/node`, {
        body: { key: node.key, note } }), t('dg.saved'));
    };
  }

  async function deleteStep(key) {
    const ok = await confirmModal({
      title: t('dg.confirmDelete.title'),
      body: t('dg.confirmDelete.body', { key: esc(key) }),
      okLabel: t('dg.deleteStep'), danger: true });
    if (!ok) return;
    if (selected === key) selected = null;
    await act(() => api(`/api/diagrams/${seg(uid)}/node`, {
      body: { key, delete: true } }), t('dg.deleted'));
  }

  /* A step created where you right-clicked, rather than wherever a fresh
     layout would drop it: two calls, because the server places a new node
     from the graph and only then can it be moved. */
  async function addStepAt(world) {
    const step = await dgStepModal({ title: t('dg.newStep.title') });
    if (!step) return;
    try {
      await api(`/api/diagrams/${seg(uid)}/node`, { body: step });
      await api(`/api/diagrams/${seg(uid)}/layout`, { body: { positions: {
        [step.key]: { x: Math.round(world.x), y: Math.round(world.y) } } } });
      toast(t('dg.added'), 'ok');
      await reload();
      selected = step.key;
      engine.select(step.key);
      paintSide();
    } catch (err) { failed('err.diagram', err); }
  }

  async function arrange() {
    await act(() => api(`/api/diagrams/${seg(uid)}/relayout`, { body: {} }), t('dg.arranged'));
    engine.fit();
  }

  /* Right-click. What is under the pointer decides the menu -- and picking
     a line by the LINE is the only way to label one that has none yet, so
     there is nothing to click for. */
  function canvasMenu({ world, x, y, node, edge }) {
    /* Following a jump is READING, so it is on the menu in read-only too --
       and it is the first thing on it, because a step that continues
       somewhere else is a step whose next question is "where". */
    const jumpItems = (node ? data.jumps.filter(j => j.node_key === node.key) : [])
      .map(j => ({
        label: t('dg.ctx.openJump', { title: j.peer_title }),
        run: () => { location.hash = jumpHref(j, uid); },
      }));
    if (!editing) {
      openCtxMenu(x, y, [
        ...jumpItems,
        jumpItems.length && { sep: true },
        { label: t('dg.enableEditing'), run: () => { editing = true; applyMode(); } },
        { label: t('dg.center'), run: () => engine.fit() },
      ]);
      return;
    }
    if (edge) {
      openCtxMenu(x, y, [
        { label: edge.label ? t('dg.ctx.edgeLabelEdit') : t('dg.ctx.edgeLabelAdd'),
          run: () => editEdgeLabel(edge) },
        edge.label && { label: t('dg.ctx.edgeLabelClear'), run: () => saveEdgeLabel(edge, '') },
        { sep: true },
        { label: t('dg.ctx.edgeDelete'), danger: true, run: () => deleteEdge(edge) },
      ]);
      return;
    }
    if (node) {
      const stored = data.nodes.find(n => n.key === node.key);
      openCtxMenu(x, y, [
        ...jumpItems,
        jumpItems.length && { sep: true },
        { label: t('dg.ctx.nodeEdit'), run: () => editStep(node) },
        { label: stored?.note ? t('dg.ctx.nodeNoteEdit') : t('dg.ctx.nodeNoteAdd'),
          run: () => editNote(node) },
        { label: t('dg.ctx.nodeConnect'),
          /* the engine reports the pending end through onConnectProgress,
             so the hint is already right -- only the button needs telling */
          run: () => { engine.startConnectFrom(node.key);
                       setPressed($('#dgConnect'), true); } },
        (stored?.w != null || stored?.h != null)
          && { label: t('dg.ctx.resetSize'), run: () => resetCardSize(node.key) },
        { sep: true },
        { label: t('dg.deleteStep'), danger: true, run: () => deleteStep(node.key) },
      ]);
      return;
    }
    openCtxMenu(x, y, [
      { label: t('dg.ctx.addHere'), run: () => addStepAt(world) },
      { label: t('dg.arrange'), run: arrange },
      { label: t('dg.center'), run: () => engine.fit() },
    ]);
  }

  function openDiagramMeta() {
    const m = openModal({
      title: t('dg.meta'),
      bodyHTML: `
        <div class="field"><label for="dgmTitle">${t('dg.meta.name')}</label>
          <input type="text" id="dgmTitle" value="${esc(data.title)}"></div>
        <div class="field"><label for="dgmSummary">${t('dg.meta.summary')}</label>
          <textarea id="dgmSummary" rows="6" placeholder="${t('dg.meta.summaryPh')}">${esc(data.summary)}</textarea></div>`,
      footHTML: `<button class="btn" data-x>${t('common.cancel')}</button>
                 <button class="btn btn-solid" data-ok>${t('common.save')}</button>`,
    });
    m.querySelector('[data-x]').onclick = closeModal;
    m.querySelector('[data-ok]').onclick = async () => {
      const body = {
        title: m.querySelector('#dgmTitle').value,
        summary: m.querySelector('#dgmSummary').value,
      };
      closeModal();
      await act(() => api(`/api/diagrams/${seg(uid)}/meta`, { body }), t('dg.saved'));
    };
  }

  /* Text size is stored on the diagram, not in the browser: a card resized
     to fit its label at one size is the wrong size at another, and the card
     sizes ARE stored. So this is an edit, and it needs editing on. */
  function paintFont() {
    const b = $('#dgFont');
    b.textContent = `Aa ${Math.round((data.font_scale || 1) * 100)}%`;
    b.title = t('dg.font.switch');
  }

  function openFontMenu(ev) {
    const r = ev.currentTarget.getBoundingClientRect();
    openCtxMenu(r.left, r.bottom + 4, FONT_SCALES.map(s => ({
      label: `${Math.round(s * 100)}%${s === 1 ? ` · ${t('dg.font.default')}` : ''}`,
      run: () => act(() => api(`/api/diagrams/${seg(uid)}/meta`, {
        body: { font_scale: s } }), t('dg.saved')),
    })));
  }

  const resetCardSize = key => act(() => api(`/api/diagrams/${seg(uid)}/layout`, {
    body: { reset_boxes: [key] } }), t('dg.sizeReset'));

  /* ── read-only vs editing ───────────────────────────────────────────── */
  function applyMode() {
    engine?.setReadOnly(!editing);
    const mode = $('#dgMode');
    mode.textContent = editing ? t('dg.doneEditing') : t('dg.enableEditing');
    setPressed(mode, editing);
    view.querySelectorAll('[data-editonly]').forEach(b => { b.disabled = !editing; });
    $('#dgTitle').classList.toggle('dg-editable', editing);
    $('#dgTitle').title = editing ? t('dg.titleEdit') : '';
    setPressed($('#dgConnect'), !!engine?.connectMode);
    paintSide();
    paintHint();
  }

  /* ── inspector ─────────────────────────────────────────────────────── */
  /* One jump, from whichever end this diagram is on. The arrow is the
     direction, the link is the address, and the step at the far end is
     named rather than implied -- coming back means arriving on the step
     that left, not on a diagram and a hunt for it. */
  const jumpRow = (j, i) => `
    <div class="dg-jump">
      <span class="dg-arrow" title="${j.direction === 'out' ? t('dg.jump.out') : t('dg.jump.in')}"
            >${icon(j.direction === 'out' ? 'arrow-right' : 'arrow-left')}</span>
      <a class="dg-jump-to" href="${jumpHref(j, uid)}"
         title="${esc(t(j.direction === 'out' ? 'dg.jump.open' : 'dg.jump.openFrom',
                        { title: j.peer_title }))}">
        <span class="dg-jump-title">${esc(j.peer_title)}</span>
        ${j.peer_node
          ? `<span class="dg-key">${esc(j.peer_node)}</span>`
          : `<span class="dg-jump-whole">${t('dg.jump.wholeDiagram')}</span>`}
        ${j.peer_node_label ? `<span class="dg-label">${esc(j.peer_node_label)}</span>` : ''}
      </a>
      ${j.label ? `<span class="dg-label" title="${esc(j.label)}">${esc(j.label)}</span>` : ''}
      ${editing ? `<button class="icon-btn danger" data-deljump="${i}"
              title="${t('dg.jump.remove')}">${icon('close')}</button>` : ''}
    </div>`;

  /* Deleting works from either end, so the payload is always "my side, the
     other side" -- see db.delete_diagram_jump. */
  const deleteJump = j => act(() => api(`/api/diagrams/${seg(uid)}/jump`, {
    body: { node_key: j.node_key, peer_uid: j.peer_uid, peer_node: j.peer_node,
            delete: true } }), t('dg.jump.removed'));

  async function addJump(nodeKey) {
    const chosen = await pickMemories({
      title: t('dg.jump.pickTitle', { key: nodeKey }),
      exclude: uid, multi: false, type: 'diagram', okLabel: t('common.next'),
    });
    if (!chosen?.uids.length) return;
    const peer = chosen.uids[0];
    let target;
    try { target = await api(`/api/diagrams/${seg(peer)}`); }
    catch (err) { failed('err.load', err); return; }
    const step = await dgJumpTargetModal(target);
    if (!step) return;
    await act(() => api(`/api/diagrams/${seg(uid)}/jump`, { body: {
      node_key: nodeKey, peer_uid: peer, peer_node: step.node,
      label: step.label } }), t('dg.jump.added'));
  }

  /* Rendered after every paint of a panel that can hold jump rows: the rows
     are the same in both, and so is what clicking one has to do. */
  function wireJumps(host, list) {
    host.querySelectorAll('[data-deljump]').forEach(b =>
      b.onclick = () => deleteJump(list[Number(b.dataset.deljump)]));
  }

  /* ── the corridor on the canvas ──────────────────────────────────────
     A card whose step continues in another flow already says so -- it
     carries a mark. What it did not carry was a way to ACT on that: the
     tie was listed in the inspector, in the last panel of a column tall
     enough to scroll, so the shortest path from "this card leads
     somewhere" to going there was to scroll past everything else.

     So the offer follows the selection, beside the card it is about.
     Nothing is drawn for a card with no jumps -- most cards -- and
     nothing at all until one is clicked. The way BACK is the exception:
     it belongs to the arrival, not to a selection, so it stays put for as
     long as you are on the flow something sent you to. */
  function paintJumpNav() {
    const bar = $('#dgJumpNav');
    const node = data.nodes.find(n => n.key === selected);
    /* Which jump brought us here, so the chip can name the flow rather
       than say "back". Matched on the step as well when the address said
       one: two flows can hand off to the same step from different places. */
    const back = cameFrom && data.jumps.find(j => j.peer_uid === cameFrom
      && (!cameFromNode || j.peer_node === cameFromNode));
    /* and then NOT offered a second time as a way out: arriving on a step
       whose only tie is the one you just followed put two chips pointing at
       the same flow on the same corner */
    const mine = (node ? data.jumps.filter(j => j.node_key === node.key) : [])
      .filter(j => j !== back);
    /* A tie aimed at the diagram as a WHOLE belongs to no step, so no
       selection could ever surface it and the canvas said nothing about it
       at all -- it was reachable only from the inspector. It is about the
       flow you are looking at, so it stays for as long as you are on it,
       and it comes after the selected step's own: those are what the click
       that got you here was asking about. */
    const whole = data.jumps.filter(j => !j.node_key && j !== back);

    /* The arrow says what the CLICK does, not which way the tie points:
       right is "open that flow", left is "back to the one that sent you
       here", and nothing else. It used to carry the tie's direction --
       right for a jump out, left for one in -- so left meant two different
       things on the same corner, and the same act of opening a related
       flow pointed one way or the other depending on which end had
       recorded the jump. Which end recorded it is a fact about the data,
       not about where this button takes you; the inspector's panel is
       where it belongs and still shows it. */
    const chip = (over, href, kind, title, step, hint) => `
      <a class="dg-navchip${kind === 'back' ? ' back' : ''}${over ? ' over' : ''}"
         href="${href}" title="${esc(hint)}">
        <span class="dg-navchip-mark">${icon(kind === 'back' ? 'arrow-left' : 'arrow-right')}</span>
        <span class="dg-navchip-text">${esc(chipLabel(title))}</span>
        ${step ? `<span class="dg-key">${esc(step)}</span>` : ''}
      </a>`;

    /* Built as a list first, so the cap counts what is actually offered
       rather than each group guessing how much room the others left. */
    const offers = [
      ...(back ? [[backHref(back.peer_uid, back.peer_node), 'back', back.peer_title,
                   back.peer_node, t('dg.jump.backTitle', { title: back.peer_title })]] : []),
      /* the direction survives in the tooltip, which is where a reader asks
         "why is this flow on my screen" rather than "where does this go" */
      ...mine.map(j => [jumpHref(j, uid), 'go', j.peer_title, j.peer_node,
        t(j.direction === 'out' ? 'dg.jump.open' : 'dg.jump.openFrom',
          { title: j.peer_title })]),
      ...whole.map(j => [jumpHref(j, uid), 'go', j.peer_title, '',
        t('dg.jump.openHere', { title: j.peer_title })]),
    ];
    const folded = Math.max(0, offers.length - JUMP_CHIPS_SHOWN);

    bar.classList.remove('open');
    bar.innerHTML = offers
      .map((args, i) => chip(i >= JUMP_CHIPS_SHOWN, ...args))
      .concat(folded ? [`
        <button type="button" class="dg-navchip dg-navmore"
                title="${esc(t('dg.jump.more', { n: folded }))}">+${folded}</button>`] : [])
      .join('');
    bar.hidden = !offers.length;

    /* Toggled here rather than by a stylesheet-only :focus-within trick: the
       label has to flip too, and a corner that expanded on hover would open
       itself every time the pointer crossed it on the way to a card. */
    const more = bar.querySelector('.dg-navmore');
    if (more) {
      more.onclick = () => {
        const open = bar.classList.toggle('open');
        more.textContent = open ? '\u2212' : `+${folded}`;
        more.title = open ? t('dg.jump.less') : t('dg.jump.more', { n: folded });
      };
    }
  }

  function paintSide() {
    paintJumpNav();
    const side = $('#dgSide');
    const node = data.nodes.find(n => n.key === selected);
    const metaBtn = editing
      ? `<button class="btn btn-sm" id="dgMetaBtn">${t('common.edit')}</button>` : '';
    if (!node) {
      /* Jumps aimed at this diagram as a WHOLE belong to no step, so no step
         can show them. They are how another flow says "continues here", and
         without this panel the only sign of one was on the diagram that made
         it. Step-level jumps stay on their step, where the arrow means
         something. */
      const loose = data.jumps.filter(j => !j.node_key);
      side.innerHTML = `<div class="dg-panel">
        <h3>${t('dg.step')}</h3>
        <div class="dg-empty">${t('dg.selectPrompt')}</div>
      </div>
      <div class="dg-panel">
        <h3>${t('dg.summary')} ${metaBtn}</h3>
        <div class="dg-empty">${data.summary ? esc(data.summary) : t('dg.noSummary')}</div>
      </div>
      ${loose.length ? `<div class="dg-panel">
        <h3>${t('dg.jumps.whole')}</h3>
        <div class="dg-jumps">${loose.map(jumpRow).join('')}</div>
      </div>` : ''}`;
      side.querySelector('#dgMetaBtn')?.addEventListener('click', openDiagramMeta);
      wireJumps(side, loose);
      return;
    }
    const out = data.edges.filter(e => e.from === node.key);
    const inc = data.edges.filter(e => e.to === node.key);
    const edgeRow = (e, dir) => `
      <div class="dg-edge">
        <span class="dg-arrow">${icon(dir === 'out' ? 'arrow-right' : 'arrow-left')}</span>
        <span class="dg-key">${esc(dir === 'out' ? e.to : e.from)}</span>
        ${e.label ? `<span class="dg-label" title="${esc(e.label)}">${esc(e.label)}</span>` : ''}
        <span class="spacer"></span>
        ${editing ? `<button class="icon-btn" data-editedge="${esc(e.from)}|${esc(e.to)}"
                title="${t('dg.edge.editLabel')}">${icon('pencil')}</button>
          <button class="icon-btn danger" data-deledge="${esc(e.from)}|${esc(e.to)}"
                title="${t('dg.edge.remove')}">${icon('close')}</button>` : ''}
      </div>`;
    const links = data.links.filter(l => l.node_key === node.key);
    const jumps = data.jumps.filter(j => j.node_key === node.key);

    const ro = editing ? '' : ' disabled';
    side.innerHTML = `
      <div class="dg-panel">
        <h3>${t('dg.step')} <span class="dg-key">${esc(node.key)}</span></h3>
        ${editing ? '' : `<div class="dg-empty">${t('dg.readOnlyNote')}</div>`}
        <div class="field"><label for="dgLabel">${t('dg.label')}</label>
          <input type="text" id="dgLabel" value="${esc(node.label)}"${ro}></div>
        <div class="field"><label for="dgShape">${t('dg.shape')}</label>
          ${pickerFor({ id: 'dgShape', value: node.shape, items: shapeItems(),
                        ariaLabel: t('dg.shape'), disabled: !editing })}</div>
        <div class="field"><label for="dgNote">${t('dg.note')}</label>
          <textarea id="dgNote" rows="5" placeholder="${t('dg.notePh')}"${ro}>${esc(node.note)}</textarea></div>
        ${editing ? `<div class="act-row">
          <button class="btn btn-solid btn-sm" id="dgSave">${t('common.save')}</button>
          <button class="btn btn-danger btn-sm" id="dgDel">${t('dg.deleteStep')}</button>
        </div>` : ''}
      </div>

      <div class="dg-panel">
        <h3>${t('dg.edges')}</h3>
        <div class="dg-edges">
          ${[...out.map(e => edgeRow(e, 'out')), ...inc.map(e => edgeRow(e, 'in'))].join('')
            || `<div class="dg-empty">${t('dg.edges.empty')}</div>`}
        </div>
      </div>

      <div class="dg-panel">
        <h3>${t('dg.links')}</h3>
        <div class="dg-empty">${t('dg.linksHint')}</div>
        <div class="dg-links">
          ${links.map(l => `
            <div class="dg-link">
              <span class="type-tag ${typeClass(l.peer.type)}" style="flex:none"><span class="dot"></span>${esc(l.peer.type || '?')}</span>
              <span class="snippet clickable" data-open="${esc(l.target_uid)}"
                    title="${esc(l.peer.snippet || l.target_uid)}">${esc(l.peer.snippet || l.target_uid)}</span>
              ${editing ? `<button class="icon-btn danger" data-dellink="${esc(l.target_uid)}"
                      title="${t('dg.link.remove')}">${icon('close')}</button>` : ''}
            </div>`).join('') || `<div class="dg-empty">${t('dg.links.empty')}</div>`}
        </div>
        ${editing ? `<div class="act-row">
          <button class="btn btn-sm" id="dgAttach">${t('dg.link.attach')}</button>
        </div>` : ''}
      </div>

      <!-- Its own panel, and not a row in the one above: a linked memory is
           something to READ about this step, a jump is somewhere to GO from
           it. They were the same list, so the only way out of a flow looked
           like a footnote and behaved like one. -->
      <div class="dg-panel">
        <h3>${t('dg.jumps')}</h3>
        <div class="dg-empty">${t('dg.jumpsHint')}</div>
        <div class="dg-jumps">
          ${jumps.map(jumpRow).join('') || `<div class="dg-empty">${t('dg.jumps.empty')}</div>`}
        </div>
        ${editing ? `<div class="act-row">
          <button class="btn btn-sm" id="dgAddJump">${t('dg.jump.add')}</button>
        </div>` : ''}
      </div>`;

    side.querySelectorAll('[data-open]').forEach(el =>
      el.addEventListener('click', () => openRecord(el.dataset.open)));
    if (!editing) return;   /* nothing below this point exists in read-only */

    /* node fields */
    wirePicker(side, { id: 'dgShape', items: fixedItems(shapeItems()), onPick: () => {} });
    side.querySelector('#dgSave').onclick = () => act(() => api(`/api/diagrams/${seg(uid)}/node`, { body: {
      key: node.key, label: side.querySelector('#dgLabel').value,
      shape: pickerValue(side, 'dgShape'),
      note: side.querySelector('#dgNote').value } }), t('dg.saved'));

    side.querySelector('#dgDel').onclick = () => deleteStep(node.key);

    side.querySelectorAll('[data-deledge]').forEach(b => b.onclick = () => {
      const [from, to] = b.dataset.deledge.split('|');
      deleteEdge({ from, to });
    });
    side.querySelectorAll('[data-editedge]').forEach(b => b.onclick = () => {
      const [from, to] = b.dataset.editedge.split('|');
      editEdgeLabel(data.edges.find(e => e.from === from && e.to === to) || { from, to });
    });

    /* links */
    side.querySelectorAll('[data-dellink]').forEach(b => b.onclick = () =>
      act(() => api(`/api/diagrams/${seg(uid)}/link`, { body: {
        node_key: node.key, target_uid: b.dataset.dellink, delete: true } }), t('dg.unlinked')));

    /* Attaching is a form of its own now (core/link-picker.js), so several
       memories can go onto one step in one pass and each candidate can be
       read in full before it is chosen. What used to be here was a lookup
       field, a type select and an error line, wedged into a 320px column. */
    side.querySelector('#dgAttach').onclick = async () => {
      const chosen = await pickMemories({
        title: t('dg.link.pickTitle', { key: node.key }),
        exclude: uid,
        linked: links.map(l => l.target_uid),
        relOptions: DG_REL_SUGGEST,
        relValue: 'explains',
        okLabel: t('dg.link.attach'),
      });
      if (!chosen?.uids.length) return;
      await act(async () => {
        /* one call per memory: the API links one at a time, and a partial
           failure leaves the ones that landed rather than rolling back a
           batch the operator chose deliberately */
        for (const target of chosen.uids) {
          await api(`/api/diagrams/${seg(uid)}/link`, { body: {
            node_key: node.key, target_uid: target,
            relation_type: chosen.relation || 'explains' } });
        }
      }, t('dg.linkedN', { n: chosen.uids.length }));
    };

    /* jumps */
    wireJumps(side, jumps);
    side.querySelector('#dgAddJump').onclick = () => addJump(node.key);
  }

  /* ── engine ──────────────────────────────────────────────────────────
     The legend is painted BEFORE the engine exists: the constructor fits
     the diagram, and fit() asks insets() how much of the corner is taken.
     An empty legend at that moment measures zero and the first row of
     cards lands behind it. */
  paintLegend();
  engine = new DiagramEditor($('#dgCanvas'), data, {
    tipShow, tipHide,
    readOnly: true,
    onEditEdgeLabel: editEdgeLabel,
    onContextMenu: canvasMenu,
    /* the toolbar has its own row now; the hint strip, the legend and the
       jump nav still float on the canvas, so the fit stays clear of both
       corners. A hidden bar measures zero, which is the common case. */
    insets: () => ({
      top: ($('#dgJumpNav')?.offsetHeight || 0) + 18,
      bottom: ($('#dgOverlay')?.offsetHeight || 0) + 18,
    }),
    onSelect: node => { selected = node ? node.key : null; paintSide(); },
    onSelectEdge: () => paintHint(),
    onMove: positions => { Object.assign(pending, positions); flushLayout(); },
    onConnectProgress: from => paintHint(from),
    onConnect: async (from, to) => {
      const label = await promptModal({
        title: t('dg.edgeLabel.title'),
        body: t('dg.edgeLabel.body', { from: esc(from), to: esc(to) }),
        label: t('dg.edgeLabel.label'), placeholder: t('dg.edgeLabel.ph'),
        okLabel: t('dg.connectOk'),
      });
      if (label === null) { paintHint(); return; }
      await act(() => api(`/api/diagrams/${seg(uid)}/edge`, {
        body: { from, to, label } }), t('dg.connected'));
    },
  });
  /* the canvas dies with the view's innerHTML; the window listeners, the
     ResizeObserver and the pending frame do not */
  onTeardown(() => engine.destroy());

  /* ── toolbar ───────────────────────────────────────────────────────── */
  $('#dgAdd').onclick = async () => {
    const step = await dgStepModal({ title: t('dg.newStep.title') });
    if (!step) return;
    await act(() => api(`/api/diagrams/${seg(uid)}/node`, { body: step }), t('dg.added'));
    selected = step.key;
    engine.select(step.key);
    paintSide();
  };
  $('#dgConnect').onclick = () => {
    engine.toggleConnectMode();
    setPressed($('#dgConnect'), engine.connectMode);
    paintHint();
  };
  $('#dgArrange').onclick = arrange;
  $('#dgFit').onclick = () => engine.fit();
  $('#dgRecord').onclick = () => openRecord(uid);
  $('#dgFont').onclick = openFontMenu;
  $('#dgMode').onclick = () => { editing = !editing; applyMode(); };
  /* the heading and the summary panel are where you reach for these, so both
     open the same editor -- the toolbar is no longer the only way in */
  $('#dgTitle').onclick = () => { if (editing) openDiagramMeta(); };
  /* Arrived from a jump on another flow: land on the step it named rather
     than on the whole-diagram fit the constructor just did. A key that no
     longer exists is ignored on purpose -- the flow that pointed here is
     stale, and a red toast about a step nobody asked for is noise.

     The nav is painted BEFORE the centring, not after: focusNode() asks
     insets() how much of the corner is taken, and a bar that appears a
     moment later is a bar the centring did not know about. */
  if (landOn && data.nodes.some(n => n.key === landOn)) {
    selected = landOn;
    paintJumpNav();
    engine.focusNode(landOn);
  }

  paintFont();
  applyMode();
  $('#dgMermaid').onclick = async () => {
    let src = '';
    try { src = (await api(`/api/diagrams/${seg(uid)}/mermaid`)).mermaid; }
    catch (err) { failed('err.load', err); return; }
    const m = openModal({
      title: t('dg.mermaid.title'),
      bodyHTML: `<div class="dg-empty" style="margin-bottom:9px">${t('dg.mermaid.hint')}</div>
        <pre class="content-pre" style="max-height:340px">${esc(src)}</pre>`,
      footHTML: `<button class="btn" data-x>${t('common.close')}</button>
                 <button class="btn btn-solid" data-ok>${t('dg.mermaid.copy')}</button>`,
    });
    m.querySelector('[data-x]').onclick = closeModal;
    m.querySelector('[data-ok]').onclick = () => {
      navigator.clipboard?.writeText(src).then(() => toast(t('dg.mermaid.copied'), 'ok'));
      closeModal();
    };
  };
}
