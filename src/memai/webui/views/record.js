/* The memory record: a view with an address of its own.

   It was a centred dialog over whatever was behind it. A dialog is the
   right shape for a form you fill in and dismiss, and the wrong one for the
   thing this app exists to read and curate: it could not be linked to, it
   could not be reloaded, browser Back did not close it, and everything
   under it kept rendering behind a scrim.

   It is #/memory?uid=… now, and openRecord(uid) navigates there -- every
   caller in the app already went through that one function, so none of them
   changed.

   Editing is per FIELD. A memory of a sectioned type is a handful of named
   fields, and the editor used to be all of them at once in a stack of bare
   textareas with no sight of what the markup would become. A field opens on
   its own, full width, with the source on one side and the rendering on the
   other, and the save bar pinned under the pair. `Edit all` is still there
   for a rewrite that touches every field.

   There is no draft: Save writes the version immediately, with its note,
   and the previous text is kept inside that version -- which is what the
   API does anyway. */

import { esc, fmtDate, fmtInt, debounce } from '../core/dom.js';
import { api, seg } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, failed, openModal, closeModal, confirmModal, promptModal,
         openCtxMenu, copyCode, copyUid } from '../core/ui.js';
import { typeTag, uidChip, statusTag, wireCopyChips,
         CONF, REL_SUGGEST, typeItems, sectionLabel, sectionLabelHTML,
         cachedDomains, invalidateDomains, domainDatalist } from '../core/shared.js';
import { pickerFor, pickerValue, wirePicker, fixedItems } from '../core/pick.js';
import { pickMemories } from '../core/link-picker.js';
import { go, refreshBehind, backTo } from '../core/router.js';
import { renderRich, wireRich } from '../core/richtext.js';
import { highlightIn } from '../core/highlight.js';
import { DiagramEditor } from '../diagram-engine.js';
import { t } from '../i18n.js';

/* Where every other view sends a reader who clicked a memory. It is a
   navigation, so Back works, the URL is shareable, and nothing has to know
   this used to be a dialog. */
export const openRecord = uid => go('memory', { uid });

/* The read-only canvas a diagram record draws itself on. It listens on
   window and holds a ResizeObserver, so dropping the subtree that carries
   its <canvas> is not enough -- every repaint has to end it by hand. */
let recEngine = null;
const endRecordCanvas = () => {
  try { recEngine?.destroy(); } catch (err) { console.error(err); }
  recEngine = null;
};

/* The list this record can step through, in the order it was shown.
   Registered by whoever put the record on screen -- views/memories.js hands
   over the page it just rendered and clears it on the way out.

   Without it the record is a dead end: curating a page of memories meant
   going back, finding the next row, opening it again, fifty times over. It
   is a plain array of uids and not the rows themselves, so a record reached
   from anywhere else simply finds itself absent from it and shows no
   stepper. */
let seq = [];
export const setRecordSequence = uids => { seq = Array.isArray(uids) ? [...uids] : []; };

/* Where the memory on screen sat in that list the last time the list still
   held it. It is what keeps the stepper alive across a write that drops the
   row: archiving from a list filtered to active memories takes the record
   out of the list under it, and without an anchor that ended the walk --
   the one act you perform while curating a page cost you the page. */
let slot = null;
let anchored = null;

/* Which memory the record SHOWS, where the arrows go from it, and whether
   the list still holds it. Everything after a dropped row shifted up by one,
   so the record that took the slot IS the next one and the slot itself is
   where to resume; that also covers a row that fell off the end of a
   shrinking page. */
function stepPos(uid) {
  if (anchored !== uid) { anchored = uid; slot = null; }
  const at = seq.indexOf(uid);
  if (at >= 0) { slot = at; return { at, prev: at - 1, next: at + 1, gone: false }; }
  if (slot === null || !seq.length) return null;
  const anchor = Math.min(slot, seq.length - 1);
  return { at: anchor, prev: anchor - 1, next: anchor, gone: true };
}

/* One path per write, shared by the button that performs it and by the Undo
   that reverses it. An undo which reimplements the call it is undoing is an
   undo that drifts away from it on the next change. */
const setStatus = (uid, status, reason) =>
  api(`/api/memories/${seg(uid)}/status`,
      { body: reason === undefined ? { status } : { status, reason } });

/* A relation is recreatable from what its own row already knew, so deleting
   one is reversible without asking you to find the pair again. `direction`
   is which end this record is on. */
const relink = (uid, rel) => api('/api/relations', {
  body: {
    from_uid: rel.direction === 'out' ? uid : rel.peer.uid,
    to_uid: rel.direction === 'out' ? rel.peer.uid : uid,
    relation_type: rel.relation_type,
    note: rel.note || '',
  },
});

/* Which field is open for editing, by section key -- '' for the body of a
   type that has no fields, and null for none. `all` is the rewrite mode:
   every field open at once, sharing one save bar.

   Module-level and reset per uid, so a save (which re-renders the view)
   comes back with the same field open, and stepping to another memory
   does not land you in its editor. */
let editing = { uid: null, key: null, all: false };
const resetEditing = uid => { editing = { uid, key: null, all: false }; };

export async function renderRecord(view, params, ctx) {
  const uid = params.get('uid') || '';
  if (!uid) { go('memories'); return; }
  if (editing.uid !== uid) resetEditing(uid);

  endRecordCanvas();
  const m = await api(`/api/memories/${seg(uid)}`);
  if (ctx.stale()) return;

  /* a diagram's content is generated from its graph, so the record shows it
     read-only and sends editing to the canvas; every other type gets the
     reverse view -- which flows have a step pointing at it */
  const isDiagram = m.type === 'diagram';
  const spec = isDiagram ? [] : (m.spec || []);
  const sectionText = Object.fromEntries((m.sections || []).map(s => [s.key, s.text]));
  /* One entry per editable field. A type with no spec has exactly one, whose
     key is '' -- so the card, the editor and the save path are written once
     and a sectioned body is not a second code path. */
  const fields = spec.length && !m.section_problem
    ? spec.map(s => ({ key: s.key, max: s.max_len,
                       labelHTML: sectionLabelHTML(m.type, s),
                       label: sectionLabel(m.type, s),
                       text: sectionText[s.key], present: s.key in sectionText }))
    : [{ key: '', max: 0, labelHTML: t('dr.content'), label: t('dr.content'),
         text: m.content, present: true }];

  view.innerHTML = `<div class="rec-shell">
    ${barHTML(m, uid)}
    <div class="rec-work">
      <div class="rec-main">
        <h2 class="rec-title"><input id="dTitle" value="${esc(m.title || '')}"
              placeholder="${esc(t('dr.title.placeholder'))}"
              aria-label="${esc(t('mm.name.label'))}" spellcheck="false"></h2>
        ${m.section_problem
          ? `<div class="sec-problem">${t('dr.sections.problem',
               { detail: esc(m.section_problem) })}</div>` : ''}
        ${isDiagram ? diagramHTML(m, uid) : `
        <div class="rec-fields${editing.key !== null || editing.all ? ' is-editing' : ''}"
             >${fields.map(f => fieldHTML(f, m)).join('')}</div>
        ${editing.all ? saveBarHTML(t('dr.saveAll'), 'dSaveAll') : ''}`}
        ${refsHTML(m)}
      </div>
      ${sideHTML(m, uid)}
    </div>
  </div>`;

  wire(view, m, uid, fields, isDiagram);
}

/* ─── the bar ─────────────────────────────────────────────────────────── */

function barHTML(m, uid) {
  const pos = stepPos(uid);
  const stepper = !pos || (pos.prev < 0 && pos.next >= seq.length) ? '' : `
    <span class="rec-step" role="group" aria-label="${esc(t('dr.step.aria'))}"
          ${pos.gone ? `title="${esc(t('dr.step.gone'))}"` : ''}>
      <button type="button" class="icon-btn" id="dPrev" ${pos.prev < 0 ? 'disabled' : ''}
              title="${esc(t('dr.step.prev'))}" aria-label="${esc(t('dr.step.prev'))}">${icon('chevron-left')}</button>
      <span class="rec-step-at">${pos.gone
        ? t('dr.step.atGone', { n: seq.length })
        : t('dr.step.at', { i: pos.at + 1, n: seq.length })}</span>
      <button type="button" class="icon-btn" id="dNext" ${pos.next >= seq.length ? 'disabled' : ''}
              title="${esc(t('dr.step.next'))}" aria-label="${esc(t('dr.step.next'))}">${icon('chevron-right')}</button>
    </span>`;
  return `<div class="rec-bar">
    <button type="button" class="rec-back" id="dBack">${icon('chevron-left')}${t('nav.memories')}</button>
    ${m.domain ? `<span class="rec-crumb-sep">/</span>
      <button type="button" class="rec-crumb" data-fdomain="${esc(m.domain)}"
        aria-label="${esc(t('a11y.filterDomain', { domain: m.domain }))}">${esc(m.domain)}</button>` : ''}
    <span class="rec-bar-end">
      ${stepper}
      ${m.type === 'diagram' ? '' : `<button type="button" class="btn btn-sm" id="dEditAll"
        aria-pressed="${editing.all}">${icon('pencil')}${t('dr.editAll')}</button>`}
      <button type="button" class="icon-btn" id="dMore" title="${t('dr.more')}"
              aria-label="${t('dr.more')}">${icon('maintenance')}</button>
    </span>
  </div>`;
}

/* ─── one field ───────────────────────────────────────────────────────── */

const countHTML = f => (f.max
  ? `<span class="rf-count" data-count>${t('dr.sections.count', { n: (f.text || '').length, max: f.max })}</span>`
  : `<span class="rf-count">${t('dr.chars', { n: fmtInt((f.text || '').length) })}</span>`);

function fieldHTML(f, m) {
  const open = editing.all || editing.key === f.key;
  if (!open) {
    return `<section class="rf" data-field="${esc(f.key)}">
      <header class="rf-head">
        <span class="rf-label">${f.labelHTML}</span>
        ${countHTML(f)}
        <button type="button" class="rf-edit" data-edit="${esc(f.key)}">${icon('pencil')}${t('common.edit')}</button>
      </header>
      ${f.present
        ? `<div class="rf-body content-prose rt">${renderRich(f.text, m.body_links)}</div>`
        : `<div class="sec-absent">${t('dr.sections.missing')}</div>`}
    </section>`;
  }
  /* Open: the source and what it becomes, side by side, and the save bar
     attached to the pair. In `all` mode there is no preview -- every field
     is open at once and one save bar serves them all, so the column has no
     room to double each of them. */
  return `<section class="rf is-open" data-field="${esc(f.key)}">
    <header class="rf-head">
      <span class="rf-dot" aria-hidden="true"></span>
      <span class="rf-label">${f.labelHTML}</span>
      ${countHTML(f)}
      ${editing.all ? '' : `<span class="rf-keys">
        <kbd>${t('dr.key.save')}</kbd><kbd>${t('dr.key.close')}</kbd></span>`}
    </header>
    <div class="rf-split${editing.all ? ' rf-solo' : ''}">
      <div class="rf-pane">
        <span class="rf-sub">${t('dr.source')}</span>
        <textarea data-src="${esc(f.key)}" rows="10" spellcheck="false"
                  aria-label="${esc(f.label)}">${esc(f.text || '')}</textarea>
      </div>
      ${editing.all ? '' : `<div class="rf-pane">
        <span class="rf-sub">${t('dr.asRead')}</span>
        <div class="rf-preview content-prose rt" data-preview></div>
      </div>`}
    </div>
    ${editing.all ? '' : saveBarHTML(t('dr.saveVersion'), 'dSave')}
  </section>`;
}

const saveBarHTML = (label, id) => `<div class="rf-save">
  <span class="hint-sm">${t('dr.prevKept')}</span>
  <input type="text" data-note placeholder="${t('dr.editNote.placeholder')}"
         aria-label="${t('dr.editNote.placeholder')}">
  <button type="button" class="btn btn-solid btn-sm" id="${id}">${label}</button>
  <button type="button" class="btn btn-sm" data-cancel>${t('common.cancel')}</button>
</div>`;

function diagramHTML(m, uid) {
  return `<div class="rf">
    <header class="rf-head">
      <span class="rf-label">${t('dr.content')}</span>
      <button type="button" class="rf-edit" id="dOpenEditor">${icon('pencil')}${t('dr.openEditor')}</button>
    </header>
    <div class="dg-stage dg-stage-record" id="dRecordStage">
      <canvas id="dRecordCanvas" role="img"
              aria-label="${esc(t('dr.canvasAlt', { title: m.title || uid }))}"></canvas>
    </div>
    <!-- the projection stays in the DOM as the fallback: it is what shows if
         the graph cannot be fetched, and it is still the text the index was
         built from -->
    <pre class="content-pre" id="dContent" hidden>${esc(m.content)}</pre>
    <div class="dg-empty">${t('dr.generated')}</div>
  </div>`;
}

function refsHTML(m) {
  const refs = m.referenced_by_diagrams || [];
  if (!refs.length) return '';
  return `<div class="rf">
    <header class="rf-head"><span class="rf-label">${t('dr.inDiagrams')}</span>
      <span class="rf-count">${refs.length}</span></header>
    <div class="dg-links">
      ${refs.map(r => `<div class="dg-link">
        <span class="dg-key">${esc(r.node_key)}</span>
        <span class="snippet clickable" data-open="${esc(r.memory_uid)}">${esc(r.title)}${
          r.label ? ` · ${esc(r.label)}` : ''}</span>
      </div>`).join('')}
    </div>
  </div>`;
}

/* ─── the side ────────────────────────────────────────────────────────── */

function sideHTML(m, uid) {
  const chip = (value, kind, label) => `<span class="rs-chip">${esc(value)}
    <button type="button" class="rs-chip-x" data-drop="${kind}" data-value="${esc(value)}"
            title="${esc(label)}" aria-label="${esc(label)}">${icon('close')}</button></span>`;
  const tags = (m.tags || '').split(',').map(x => x.trim()).filter(Boolean);

  /* The note says WHY these two belong together, so it is text on the row
     rather than a title on a span -- unreachable by keyboard, invisible on
     touch, announced by nothing. It takes a line of its own: the column is
     368px and the peer's own name already fills it. */
  const rels = m.relations.map(r => `
    <div class="rs-rel">
      <span class="rel-dir" title="${r.direction === 'out' ? t('dr.rel.out.title') : t('dr.rel.in.title')}">${icon(r.direction === 'out' ? 'arrow-right' : 'arrow-left')}</span>
      <span class="rel-type-chip">${esc(r.relation_type)}</span>
      ${r.peer.missing
        ? `<span class="snippet rel-gone">${t('dr.rel.missing', { uid: esc(r.peer.uid) })}</span>`
        : `<button type="button" class="snippet" data-open="${esc(r.peer.uid)}"
             title="${esc(r.peer.snippet)}">${esc(r.peer.snippet)}</button>`}
      <button type="button" class="icon-btn danger" data-delrel="${r.id}"
              title="${t('dr.rel.remove.title')}"
              aria-label="${t('dr.rel.remove.title')}">${icon('close')}</button>
      ${r.note ? `<span class="rs-rel-note">${esc(r.note)}</span>` : ''}
    </div>`).join('') || `<div class="hint-sm">${t('dr.rel.empty')}</div>`;

  const hist = m.edit_history.slice().reverse().map((e, i) => `
    <div class="rs-hist">
      <span class="rs-hist-when" title="${esc(e.edited_at)}">${fmtDate(e.edited_at)}</span>
      <span class="rs-hist-note">${esc(e.note || '')
        || (e.prev_content !== e.new_content ? t('dr.hist.contentEdited') : t('dr.hist.entry'))}</span>
      ${e.prev_content !== e.new_content
        ? `<button type="button" class="rs-hist-diff" data-diff="${i}" aria-expanded="false"
                   aria-controls="histDiff${i}">${t('dr.hist.viewBtn')}</button>
           <div class="hist-diff" id="histDiff${i}" data-diffbody="${i}" hidden></div>` : ''}
    </div>`).join('') || `<div class="hint-sm">${t('dr.hist.empty')}</div>`;

  return `<aside class="rec-side">
    <div class="rs-head">
      <div class="rs-head-row">
        ${typeTag(m.type)}${uidChip(m.uid)}
        <span class="rs-status">${m.status === 'archived'
          ? statusTag('archived') : t('common.active')}</span>
      </div>
      <div class="seg rs-conf" id="dConf" role="group" aria-label="${t('dr.curation')}">
        ${Object.keys(CONF).map(c => `<button type="button" data-c="${c}"
            aria-pressed="${m.confidence === c}"><span class="conf-pill c-${c}">${
            icon(CONF[c].icon)}</span>${CONF[c].label}</button>`).join('')}
      </div>
    </div>

    <div class="rs-body">
      <div class="rs-field">
        <div class="rs-field-head"><span class="mg-label">${t('dr.meta.domain')}</span>
          <button type="button" class="rs-more" id="dMeta">${t('dr.meta.change')}</button></div>
        <button type="button" class="rs-value" data-fdomain="${esc(m.domain)}"
          ${m.domain ? '' : 'disabled'}>${esc(m.domain || t('mem.mi.noDomain'))}</button>
      </div>

      <div class="rs-field">
        <div class="rs-field-head"><span class="mg-label">${t('dr.meta.also')}</span>
          <span class="rs-n">${(m.also || []).length}</span>
          <button type="button" class="rs-more" data-add="also">${t('dr.meta.addDomain')}</button></div>
        <div class="rs-chips">${(m.also || []).map(p => chip(p, 'also', t('dr.meta.dropAlso'))).join('')
          || `<span class="hint-sm">${t('dr.meta.noAlso')}</span>`}</div>
      </div>

      <div class="rs-field">
        <div class="rs-field-head"><span class="mg-label">${t('dr.meta.tags')}</span>
          <span class="rs-n">${tags.length}</span>
          <button type="button" class="rs-more" data-add="tag">${t('dr.meta.addTag')}</button></div>
        <div class="rs-chips">${tags.map(x => chip(x, 'tag', t('dr.meta.dropTag'))).join('')
          || `<span class="hint-sm">${t('mem.noTags')}</span>`}</div>
      </div>

      <div class="rs-rows">
        <div><span>${t('dr.meta.session')}</span>${m.session
          ? `<button type="button" class="rs-mono" data-fsession="${esc(m.session)}"
               title="${esc(m.session)}"
               aria-label="${esc(t('a11y.filterSession', { session: m.session }))}"
               >${esc(m.session)}</button>`
          : `<b class="rs-mono">—</b>`}</div>
        <div><span>${t('dr.meta.created')}</span><b class="rs-mono"
          title="${esc(m.created_at)}">${fmtDate(m.created_at)}</b></div>
        <div><span>${t('dr.meta.updated')}</span><b class="rs-mono"
          title="${esc(m.updated_at)}">${fmtDate(m.updated_at)}</b></div>
        <div><span>${t('dr.meta.size')}</span><b class="rs-mono">${
          t('dr.chars', { n: fmtInt(m.content.length) })}</b></div>
        ${m.superseded_by ? `<div><span>${t('dr.meta.supersededBy')}</span>
          <button type="button" class="rs-mono" data-open="${esc(m.superseded_by)}"
          >${esc(m.superseded_by)}</button></div>` : ''}
      </div>

      <div class="rs-field">
        <div class="rs-field-head"><span class="mg-label">${t('dr.relations')}</span>
          <span class="rs-n">${m.relations.length}</span>
          <button type="button" class="rs-more" id="relAdd">${t('dr.rel.link')}</button></div>
        ${rels}
      </div>

      <div class="rs-field">
        <div class="rs-field-head"><span class="mg-label">${t('dr.history')}</span>
          <span class="rs-n">${m.edit_history.length}</span></div>
        ${hist}
      </div>
    </div>

    <div class="rs-foot">
      ${m.status === 'active'
        ? `<button class="btn btn-sm" id="dArchive">${t('dr.archiveSoft')}</button>`
        : `<button class="btn btn-sm" id="dRestore">${t('common.restore')}</button>`}
      <button class="btn btn-sm btn-danger" id="dDelete">${icon('trash')}${t('dz.button')}</button>
    </div>
  </aside>`;
}

/* ─── wiring ──────────────────────────────────────────────────────────── */

function wire(view, m, uid, fields, isDiagram) {
  const q = s => view.querySelector(s);
  const save = () => refreshBehind();

  wireCopyChips(view);
  wireRich(view, { open: openRecord, copy: copyCode });
  /* the text is on screen already; colour arrives when the grammar does */
  highlightIn(view).catch(() => {});

  q('#dBack').addEventListener('click', () => backTo('memories'));
  q('#dPrev')?.addEventListener('click', () => step(uid, -1));
  q('#dNext')?.addEventListener('click', () => step(uid, 1));
  view.querySelectorAll('[data-open]').forEach(el =>
    el.addEventListener('click', () => openRecord(el.dataset.open)));
  view.querySelectorAll('[data-fdomain]').forEach(el =>
    el.addEventListener('click', () => go('memories', { domain: el.dataset.fdomain })));
  view.querySelectorAll('[data-fsession]').forEach(el =>
    el.addEventListener('click', () => go('memories', { session: el.dataset.fsession, status: '' })));

  /* ── the title, edited where it is read ── */
  const title = q('#dTitle');
  const saveTitle = async () => {
    const value = title.value.trim();
    if (value === (m.title || '')) return;
    if (!value) { title.value = m.title || ''; return; }   /* the API refuses an empty one */
    try {
      await api(`/api/memories/${seg(uid)}/meta`, { body: { title: value } });
      toast(t('dr.titleUpdated'), 'ok');
      save();
    } catch (err) { title.value = m.title || ''; failed('err.save', err); }
  };
  title.addEventListener('blur', saveTitle);
  title.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); title.blur(); }
    if (e.key === 'Escape') { title.value = m.title || ''; title.blur(); }
  });

  /* ── the fields ── */
  view.querySelectorAll('[data-edit]').forEach(b => b.addEventListener('click', () => {
    editing = { uid, key: b.dataset.edit, all: false };
    refreshBehind();
  }));
  q('#dEditAll')?.addEventListener('click', () => {
    editing = { uid, key: null, all: !editing.all };
    refreshBehind();
  });
  view.querySelectorAll('[data-cancel]').forEach(b => b.addEventListener('click', () => {
    resetEditing(uid);
    refreshBehind();
  }));

  /* the preview follows the source, and the counter follows both */
  view.querySelectorAll('[data-src]').forEach(box => {
    const card = box.closest('.rf');
    const preview = card.querySelector('[data-preview]');
    const count = card.querySelector('[data-count]');
    const field = fields.find(f => f.key === box.dataset.src);
    const draw = () => {
      if (preview) {
        preview.innerHTML = renderRich(box.value, m.body_links);
        wireRich(preview, { open: openRecord, copy: copyCode });
        highlightIn(preview).catch(() => {});
      }
      if (count && field?.max) {
        count.textContent = t('dr.sections.count', { n: box.value.length, max: field.max });
        count.classList.toggle('over', box.value.length > field.max);
      }
    };
    draw();
    box.addEventListener('input', debounce(draw, 180));
    box.addEventListener('keydown', e => {
      if (e.key === 'Escape') { resetEditing(uid); refreshBehind(); return; }
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        saveFields(view, m, uid, fields);
      }
    });
  });
  const first = view.querySelector('[data-src]');
  if (first) { first.focus(); first.setSelectionRange(first.value.length, first.value.length); }
  q('#dSave')?.addEventListener('click', () => saveFields(view, m, uid, fields));
  q('#dSaveAll')?.addEventListener('click', () => saveFields(view, m, uid, fields));

  /* ── the diagram canvas ── */
  if (isDiagram) {
    q('#dOpenEditor').addEventListener('click', () => go('diagram', { uid }));
    /* The graph is a second request, so the canvas fills in after the rest
       of the record. DiagramEditor is read-only unless told otherwise, and
       the layout is whatever was arranged in the editor -- this draws it,
       it does not re-derive it. */
    const stage = q('#dRecordStage');
    api(`/api/diagrams/${seg(uid)}`).then(data => {
      if (!document.contains(stage)) return;   /* navigated away mid-flight */
      recEngine = new DiagramEditor(stage.querySelector('canvas'), data, {});
    }).catch(err => {
      console.error(err);
      if (!document.contains(stage)) return;
      stage.hidden = true;
      view.querySelector('#dContent').hidden = false;
    });
  }

  /* ── metadata ── */
  q('#dMeta').addEventListener('click', () => openMetaModal(m));
  view.querySelectorAll('[data-add]').forEach(b => b.addEventListener('click',
    () => addMeta(m, b.dataset.add)));
  view.querySelectorAll('[data-drop]').forEach(b => b.addEventListener('click',
    () => dropMeta(m, b.dataset.drop, b.dataset.value)));

  /* ── confidence ── */
  q('#dConf').querySelectorAll('button').forEach(b => b.addEventListener('click', async () => {
    if (b.dataset.c === m.confidence) return;
    try {
      await api(`/api/memories/${seg(uid)}/confidence`, { body: { confidence: b.dataset.c } });
      toast(t('dr.confSet', { label: CONF[b.dataset.c].label }), 'ok');
      save();
    } catch (err) { failed('err.save', err); }
  }));

  /* ── archive / restore / delete ── */
  q('#dArchive')?.addEventListener('click', async () => {
    const reason = await promptModal({
      title: t('dr.archiveModal.title'),
      body: t('dr.archiveModal.body'),
      label: t('dr.archiveModal.label'), okLabel: t('common.archive'), danger: true });
    if (reason === null) return;
    try {
      await setStatus(uid, 'archived', reason);
      /* Archiving is reversible in the data model and was not reversible in
         the UI: the toast said "archived" and left. Restoring is the exact
         inverse and needs nothing this screen has thrown away. */
      toast(t('dr.archived'), 'ok', {
        action: {
          label: t('common.undo'),
          run: () => setStatus(uid, 'active')
            .then(() => { toast(t('dr.restored'), 'ok'); save(); })
            .catch(err => failed('err.status', err)),
        },
      });
      save();
    } catch (err) { failed('err.status', err); }
  });
  q('#dRestore')?.addEventListener('click', async () => {
    try {
      await setStatus(uid, 'active');
      /* No Undo on this one, deliberately: putting a record back to archived
         needs the reason it was archived with, and that is not something this
         screen still knows. An "undo" that silently rewrites the reason would
         be worse than no undo at all. */
      toast(t('dr.restored'), 'ok');
      save();
    } catch (err) { failed('err.status', err); }
  });
  q('#dDelete').addEventListener('click', () => openPurgeModal(uid));

  q('#dMore').addEventListener('click', e => {
    const r = e.currentTarget.getBoundingClientRect();
    openCtxMenu(r.right - 220, r.bottom + 4, [
      { label: t('mm.title'), run: () => openMetaModal(m) },
      { label: t('a11y.copyUid', { uid }), run: () => copyUid(uid) },
    ]);
  });

  /* ── relations ── */
  view.querySelectorAll('[data-delrel]').forEach(b => b.addEventListener('click', async () => {
    const ok = await confirmModal({
      title: t('dr.rel.removeModal.title'),
      body: t('dr.rel.removeModal.body'),
      okLabel: t('dr.rel.removeModal.ok'), danger: true });
    if (!ok) return;
    const rel = m.relations.find(r => String(r.id) === b.dataset.delrel);
    try {
      await api(`/api/relations/${seg(b.dataset.delrel)}`, { method: 'DELETE' });
      toast(t('dr.rel.removed'), 'ok', rel ? {
        action: {
          label: t('common.undo'),
          run: () => relink(uid, rel)
            .then(() => { toast(t('dr.rel.created'), 'ok'); save(); })
            .catch(err => failed('err.relation', err)),
        },
      } : {});
      save();
    } catch (err) { failed('err.relation', err); }
  }));

  /* Several relations in one pass, all of the same type and note: the type
     and the note describe WHY these memories belong together, and a batch
     the operator chose in one go is one such statement. Anything with its
     own reason is its own trip through the picker.

     Peers already related are deliberately NOT excluded: two memories can
     be tied twice under different types ('relates_to' and 'supersedes'),
     and only an identical triple is refused by the API. */
  q('#relAdd').addEventListener('click', async () => {
    const chosen = await pickMemories({
      title: t('dr.rel.pickTitle'),
      exclude: uid,
      relOptions: REL_SUGGEST,
      relValue: 'relates_to',
      withNote: true,
      okLabel: t('dr.rel.link'),
    });
    if (!chosen?.uids.length) return;
    const relType = chosen.relation || 'relates_to';
    let made = 0;
    try {
      for (const target of chosen.uids) {
        await api('/api/relations', { body: {
          from_uid: uid, to_uid: target, relation_type: relType, note: chosen.note } });
        made++;
      }
      toast(t('dr.rel.createdN', { n: made }), 'ok');
    } catch (err) {
      /* the ones before the failure are real relations and stay; the count
         says how far it got rather than implying all or nothing */
      failed('err.relation', err, made ? { detail: t('dr.rel.createdN', { n: made }) } : {});
    }
    save();
  });

  /* ── history diffs (lazy) ── */
  const histRev = m.edit_history.slice().reverse();
  view.querySelectorAll('[data-diff]').forEach(b => b.addEventListener('click', () => {
    const i = b.dataset.diff;
    const body = view.querySelector(`[data-diffbody="${i}"]`);
    if (body.hidden && !body.innerHTML)
      body.innerHTML = renderDiff(histRev[i].prev_content, histRev[i].new_content);
    body.hidden = !body.hidden;
    b.setAttribute('aria-expanded', body.hidden ? 'false' : 'true');
    b.textContent = body.hidden ? t('dr.hist.show') : t('dr.hist.hide');
  }));
}

function step(uid, delta) {
  const pos = stepPos(uid);
  if (!pos) return;
  const to = delta < 0 ? pos.prev : pos.next;
  if (to < 0 || to >= seq.length) return;
  openRecord(seq[to]);
}

/* ─── writing a version ───────────────────────────────────────────────── */

async function saveFields(view, m, uid, fields) {
  const boxes = [...view.querySelectorAll('[data-src]')];
  if (!boxes.length) return;
  const note = view.querySelector('[data-note]')?.value || '';
  const sectioned = fields.length > 1 || (fields[0] && fields[0].key !== '');
  /* A sectioned body is built from its fields rather than typed, so the
     server can hold it to the shape its type is supposed to have. Editing
     ONE field still sends the whole set -- the others come back from what
     was read, unchanged. */
  const [path, body] = sectioned
    ? [`/api/memories/${seg(uid)}/sections`, {
        sections: Object.fromEntries(fields.map(f => {
          const box = boxes.find(b => b.dataset.src === f.key);
          return [f.key, box ? box.value : (f.text || '')];
        })),
        note,
      }]
    : [`/api/memories/${seg(uid)}/content`, { content: boxes[0].value, note }];
  try {
    await api(path, { body });
    toast(t('dr.contentUpdated'), 'ok');
    resetEditing(uid);
    refreshBehind();
  } catch (err) { failed('err.save', err); }
}

/* ─── metadata, edited in place ───────────────────────────────────────── */

async function addMeta(m, kind) {
  const value = await promptModal({
    title: kind === 'also' ? t('dr.meta.addDomain') : t('dr.meta.addTag'),
    label: kind === 'also' ? t('dr.meta.domain') : t('dr.meta.tags'),
    okLabel: t('common.add'),
  });
  if (value === null || !value.trim()) return;
  const body = kind === 'also'
    ? { also: [...(m.also || []), value.trim()].join(', ') }
    : { tags: [...(m.tags || '').split(',').map(x => x.trim()).filter(Boolean),
               value.trim()].join(', ') };
  await writeMeta(m.uid, body);
}

async function dropMeta(m, kind, value) {
  const body = kind === 'also'
    ? { also: (m.also || []).filter(p => p !== value).join(', ') }
    : { tags: (m.tags || '').split(',').map(x => x.trim()).filter(x => x && x !== value).join(', ') };
  await writeMeta(m.uid, body);
}

async function writeMeta(uid, body) {
  try {
    await api(`/api/memories/${seg(uid)}/meta`, { body });
    invalidateDomains();
    refreshBehind();
  } catch (err) { failed('err.save', err); }
}

function openMetaModal(m) {
  const dl = domainDatalist(cachedDomains());
  /* A type the vocabulary does not know is still what this memory IS, so it
     joins the list rather than being silently replaced by the first row. */
  const types = typeItems();
  if (!types.some(it => it.value === m.type)) types.push({ value: m.type, label: m.type });
  const modal = openModal({
    title: t('mm.title'),
    bodyHTML: `
      <div class="field"><label for="mmType">${t('mm.type')}</label>
        ${pickerFor({ id: 'mmType', value: m.type, items: types, ariaLabel: t('mm.type') })}</div>
      <div class="field"><label for="mmName">${t('mm.name.label')}</label>
        <input type="text" id="mmName" value="${esc(m.title || '')}"></div>
      <div class="field"><label for="mmDomain">${t('dr.meta.domain')}</label>
        <input type="text" id="mmDomain" value="${esc(m.domain)}" list="mmDomainsDL"><datalist id="mmDomainsDL">${dl}</datalist></div>
      <div class="field"><label for="mmAlso">${t('dr.meta.also')}</label>
        <input type="text" id="mmAlso" value="${esc((m.also || []).join(', '))}" placeholder="${t('mm.also.placeholder')}" list="mmDomainsDL">
        <div class="hint-sm">${t('mm.also.hint')}</div></div>
      <div class="field"><label for="mmTags">${t('mm.tags.label')}</label>
        <input type="text" id="mmTags" value="${esc(m.tags)}"></div>
      <div class="field"><label for="mmSession">${t('dr.meta.session')}</label>
        <input type="text" id="mmSession" value="${esc(m.session)}"></div>
      <div class="hint-sm">${t('mm.hint')}</div>`,
    footHTML: `<button class="btn" data-x>${t('common.cancel')}</button><button class="btn btn-solid" data-ok>${t('common.save')}</button>`,
  });
  const mq = s => modal.querySelector(s);
  wirePicker(modal, { id: 'mmType', items: fixedItems(types), onPick: () => {} });
  mq('[data-x]').onclick = closeModal;
  mq('[data-ok]').onclick = async () => {
    try {
      const r = await api(`/api/memories/${seg(m.uid)}/meta`, { body: {
        type: pickerValue(modal, 'mmType'), title: mq('#mmName').value,
        domain: mq('#mmDomain').value,
        also: mq('#mmAlso').value,
        tags: mq('#mmTags').value, session: mq('#mmSession').value } });
      closeModal();
      toast(r.changed.length ? t('mm.updated', { list: r.changed.join(', ') }) : t('mm.nothing'), 'ok');
      invalidateDomains();
      refreshBehind();
    } catch (err) { failed('err.save', err); }
  };
}

/* ─── the one irreversible act ────────────────────────────────────────────
   It was a <details> at the bottom of a column. As a button in the footer it
   asks for the same thing it always did: the phrase typed out, printed once
   and never pre-filled, with the reversible option named beside it. */

function openPurgeModal(uid) {
  const want = `DELETE ${uid}`;
  const modal = openModal({
    title: t('dz.summary'),
    bodyHTML: `
      <div class="dz-hint">${t('dz.hint')}</div>
      <!-- The phrase is printed HERE and nowhere else. It used to be the
           field's placeholder as well, so an empty field showed the exact
           text you were being asked for: nothing on screen told a typed
           phrase from an untyped one, and the button beside it read as
           broken rather than as waiting. -->
      <div class="dz-type">${t('dz.typeThis', { phrase: `<code>DELETE ${esc(uid)}</code>` })}</div>
      <div class="dz-row">
        <input type="text" id="dzPhrase" aria-label="${t('dz.phrase.aria')}" autocomplete="off">
      </div>
      <div class="dz-state" id="dzState" role="status"></div>`,
    footHTML: `<button class="btn" data-x>${t('common.cancel')}</button>
               <button class="btn btn-danger" data-ok disabled>${t('dz.button')}</button>`,
  });
  const phrase = modal.querySelector('#dzPhrase');
  const state = modal.querySelector('#dzState');
  const okBtn = modal.querySelector('[data-ok]');
  /* A greyed-out button cannot say why it is grey, and this one guards the
     only irreversible act in the app. So the field answers for it: silent
     until something is typed, then either what is still wrong or that the
     button is now live. */
  phrase.addEventListener('input', () => {
    const ok = phrase.value === want;
    okBtn.disabled = !ok;
    state.className = `dz-state${ok ? ' armed' : ''}`;
    state.textContent = ok ? t('dz.armed') : phrase.value ? t('dz.mismatch') : '';
  });
  modal.querySelector('[data-x]').onclick = closeModal;
  okBtn.onclick = async () => {
    try {
      await api(`/api/memories/${seg(uid)}/purge`, { body: { confirm: phrase.value } });
      closeModal();
      toast(t('dz.purged'), 'ok');
      backTo('memories');
    } catch (err) { failed('err.purge', err); }
  };
}

/* line diff — plain LCS, plenty for memory-sized content */
export function renderDiff(a, b) {
  const A = a.split('\n'), B = b.split('\n');
  if (A.length * B.length > 250000)
    return `<span class="diff-del">− ${esc(a.slice(0, 800))}…</span><span class="diff-add">+ ${esc(b.slice(0, 800))}…</span>`;
  const dp = Array.from({ length: A.length + 1 }, () => new Uint16Array(B.length + 1));
  for (let i = A.length - 1; i >= 0; i--)
    for (let j = B.length - 1; j >= 0; j--)
      dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < A.length && j < B.length) {
    if (A[i] === B[j]) { out.push(`<span class="diff-ctx">  ${esc(A[i])}</span>`); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push(`<span class="diff-del">− ${esc(A[i])}</span>`); i++; }
    else { out.push(`<span class="diff-add">+ ${esc(B[j])}</span>`); j++; }
  }
  while (i < A.length) out.push(`<span class="diff-del">− ${esc(A[i++])}</span>`);
  while (j < B.length) out.push(`<span class="diff-add">+ ${esc(B[j++])}</span>`);
  return out.join('');
}
