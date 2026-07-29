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
import { typeClass, wireUidPicker, relTypeField, wireRelTypeField,
         DG_REL_SUGGEST } from '../core/shared.js';
import { onTeardown } from '../core/lifecycle.js';
import { openRecord } from './record.js';
import { DiagramEditor, NODE_SHAPES, ROUTINGS, FONT_SCALES } from '../diagram-engine.js';
import { t } from '../i18n.js';

const ROUTING_KEY = 'memai.diagram.routing';

const shapeOptions = sel => NODE_SHAPES.map(s =>
  `<option value="${s}"${s === sel ? ' selected' : ''}>${t(`dg.shape.${s}`)}</option>`).join('');

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
            <select id="dgsShape">${shapeOptions(shape)}</select></div>
        </div>
        <div class="field"><label for="dgsLabel">${t('dg.label')}</label>
          <input type="text" id="dgsLabel" value="${esc(label)}" placeholder="${t('dg.labelPh')}"></div>
        <div class="dg-empty">${t('dg.labelHint')}</div>`,
      footHTML: `<button class="btn" data-x>${t('common.cancel')}</button>
                 <button class="btn btn-solid" data-ok>${t('common.save')}</button>`,
    });
    const mq = s => m.querySelector(s);
    mq('[data-x]').onclick = () => { closeModal(); resolve(null); };
    mq('[data-ok]').onclick = () => {
      const out = {
        key: (lockKey ? key : mq('#dgsKey').value).trim(),
        label: mq('#dgsLabel').value.trim(),
        shape: mq('#dgsShape').value,
      };
      closeModal();
      resolve(out);
    };
  });
}

export async function renderDiagram(view, params, ctx) {
  const uid = params.get('uid') || '';
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
  /* how edges are drawn is a reading preference, not diagram data, so it
     lives in the browser rather than in the store */
  let routing = localStorage.getItem(ROUTING_KEY);
  if (!ROUTINGS.includes(routing)) routing = 'orthogonal';

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
          <button class="btn btn-sm" id="dgRouting"></button>
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
    if (!editing) {
      openCtxMenu(x, y, [
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

  function paintRouting() {
    const b = $('#dgRouting');
    b.textContent = t(`dg.routing.${routing}`);
    b.title = t('dg.routing.switch');
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
  function paintSide() {
    const side = $('#dgSide');
    const node = data.nodes.find(n => n.key === selected);
    const metaBtn = editing
      ? `<button class="btn btn-sm" id="dgMetaBtn">${t('common.edit')}</button>` : '';
    if (!node) {
      side.innerHTML = `<div class="dg-panel">
        <h3>${t('dg.step')}</h3>
        <div class="dg-empty">${t('dg.selectPrompt')}</div>
      </div>
      <div class="dg-panel">
        <h3>${t('dg.summary')} ${metaBtn}</h3>
        <div class="dg-empty">${data.summary ? esc(data.summary) : t('dg.noSummary')}</div>
      </div>`;
      side.querySelector('#dgMetaBtn')?.addEventListener('click', openDiagramMeta);
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

    const ro = editing ? '' : ' disabled';
    side.innerHTML = `
      <div class="dg-panel">
        <h3>${t('dg.step')} <span class="dg-key">${esc(node.key)}</span></h3>
        ${editing ? '' : `<div class="dg-empty">${t('dg.readOnlyNote')}</div>`}
        <div class="field"><label for="dgLabel">${t('dg.label')}</label>
          <input type="text" id="dgLabel" value="${esc(node.label)}"${ro}></div>
        <div class="field"><label for="dgShape">${t('dg.shape')}</label>
          <select id="dgShape"${ro}>${shapeOptions(node.shape)}</select></div>
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
        ${editing ? `<div class="rel-add">
          <div class="picker">
            <input type="text" id="dgTarget" placeholder="${t('dg.link.target')}"
                   aria-label="${t('dg.link.target')}" autocomplete="off">
            <div class="picker-results" id="dgResults" hidden></div>
          </div>
          <div class="act-row">
            ${relTypeField({
              selId: 'dgRelType', customId: 'dgRelTypeCustom', options: DG_REL_SUGGEST,
              value: 'explains', ariaLabel: t('dg.link.relType') })}
            <button class="btn btn-sm" id="dgAttach">${t('dg.link.attach')}</button>
          </div>
          <div class="field-error" id="dgLinkError" role="alert" hidden></div>
        </div>` : ''}
      </div>`;

    side.querySelectorAll('[data-open]').forEach(el =>
      el.addEventListener('click', () => openRecord(el.dataset.open)));
    if (!editing) return;   /* nothing below this point exists in read-only */

    /* node fields */
    side.querySelector('#dgSave').onclick = () => act(() => api(`/api/diagrams/${seg(uid)}/node`, { body: {
      key: node.key, label: side.querySelector('#dgLabel').value,
      shape: side.querySelector('#dgShape').value,
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

    /* the same picker the relations editor in a record uses -- it was a second
       copy of it here, with the same keyboard hole in both */
    const resolveTarget = wireUidPicker({
      input: side.querySelector('#dgTarget'),
      results: side.querySelector('#dgResults'),
      exclude: uid,
      label: t('dg.link.target'),
    });
    const dgRelValue = wireRelTypeField(
      side.querySelector('#dgRelType'), side.querySelector('#dgRelTypeCustom'));

    /* Beside the field, not in the corner: the two things that can be wrong
       here are both fields the caret is already in or next to. */
    const linkErr = side.querySelector('#dgLinkError');
    const targetEl = side.querySelector('#dgTarget');
    const linkFail = msg => {
      targetEl.setAttribute('aria-invalid', 'true');
      linkErr.textContent = msg;
      linkErr.hidden = false;
      targetEl.focus();
    };
    targetEl.addEventListener('input', () => {
      targetEl.setAttribute('aria-invalid', 'false');
      linkErr.hidden = true;
    });
    const LINK_MSG = {
      empty: 'dg.link.pickTarget',
      self: 'dr.rel.selfTarget',
      unknown: 'dr.rel.unknownTarget',
      failed: 'dr.rel.lookupFailed',
    };

    const attach = side.querySelector('#dgAttach');
    attach.onclick = async () => {
      targetEl.setAttribute('aria-invalid', 'false');
      linkErr.hidden = true;
      if (!targetEl.value.trim()) return linkFail(t('dg.link.pickTarget'));
      attach.disabled = true;
      try {
        const { uid: target, reason } = await resolveTarget();
        if (!target) return linkFail(t(LINK_MSG[reason] || 'dr.rel.unknownTarget'));
        act(() => api(`/api/diagrams/${seg(uid)}/link`, { body: {
          node_key: node.key, target_uid: target,
          relation_type: dgRelValue() } }), t('dg.linked'));
      } finally { attach.disabled = false; }
    };
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
    routing,
    onEditEdgeLabel: editEdgeLabel,
    onContextMenu: canvasMenu,
    /* the toolbar has its own row now; the hint strip and the legend still
       float on the canvas, so the fit stays clear of that corner */
    insets: () => ({
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
  $('#dgRouting').onclick = () => {
    routing = routing === 'orthogonal' ? 'curved' : 'orthogonal';
    localStorage.setItem(ROUTING_KEY, routing);
    engine.setRouting(routing);
    paintRouting();
  };
  $('#dgFont').onclick = openFontMenu;
  $('#dgMode').onclick = () => { editing = !editing; applyMode(); };
  /* the heading and the summary panel are where you reach for these, so both
     open the same editor -- the toolbar is no longer the only way in */
  $('#dgTitle').onclick = () => { if (editing) openDiagramMeta(); };
  paintRouting();
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
