/* The memory record: a centred dialog over whatever view is showing, plus
   its metadata editor and the edit-history diff.

   It was a 760px drawer down the right-hand edge. Everything in it fitted
   in one column and nothing was meant to: the content of a memory is a
   wall of prose, and beside that wall the metadata, the curation controls,
   the relations and the whole edit history were queued underneath it,
   below the fold, on a screen with 500 unused pixels to the left. It is a
   wide two-column dialog now -- the memory on one side, everything ABOUT
   the memory on the other -- and it opens over the middle of the window
   because that is where a form belongs.

   That also makes it a member of the modal stack (core/ui.js), which is
   what lets it raise the link picker as a sub-form instead of having a
   lookup field wedged into a column.

   Every lookup below is scoped to the dialog (dq) rather than to the
   document. The record and a modal it raised are on screen at once, so a
   bare getElementById is a collision waiting for the day two of them pick
   the same id. */

import { esc, fmtDate, fmtInt } from '../core/dom.js';
import { api, seg } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, failed, openModal, closeModal, confirmModal, promptModal } from '../core/ui.js';
import { typeTag, typeClass, confPill, uidChip, statusTag, wireCopyChips,
         failedHTML, CONF, REL_SUGGEST, typeItems,
         cachedDomains, invalidateDomains, domainDatalist } from '../core/shared.js';
import { pickerFor, pickerValue, wirePicker, fixedItems } from '../core/pick.js';
import { pickMemories } from '../core/link-picker.js';
import { go, refreshBehind } from '../core/router.js';
import { t } from '../i18n.js';

/* The record's own scrim while it is open, so a re-render can write into
   the dialog that is already there rather than closing and reopening it --
   which would lose the reader's place and, once a sub-form is open above,
   the sub-form with it. */
let rec = null;
const dq = s => rec.querySelector(s);

/* Where you have been inside this dialog, oldest first.

   The dialog is ONE panel, so opening the memory on the other end of a
   relation replaces what is on screen. Without a trail that cost you the
   memory you were reading it from: checking a link meant losing the record
   the link was on, and the only way back was to go and find it again --
   which is the whole reason you were following the link.

   Entries are {uid, label}; the label is filled in once that record has
   rendered, so the button naming it can name it. */
let trail = [];
const trailTop = () => trail[trail.length - 1] || null;

/* The list this record can step through, in the order it was shown.
   Registered by whoever put the record on screen -- views/memories.js hands
   over the page it just rendered and clears it on the way out.

   Without it the dialog is a dead end: curating a page of memories meant
   closing it, finding the next row, opening it again, fifty times over. It
   is a plain array of uids and not the rows themselves, so a record that
   was opened from anywhere else simply finds itself absent from it and
   shows no stepper. */
let seq = [];
export const setRecordSequence = uids => { seq = Array.isArray(uids) ? [...uids] : []; };

/* Stepping is not the same move as following a link: the memory it lands on
   is the one the list itself would have opened, so it starts a trail of its
   own rather than stacking a Back button that walks the page a row at a time. */
function stepRecord(delta) {
  const at = seq.indexOf(trailTop()?.uid);
  const to = at + delta;
  if (at < 0 || to < 0 || to >= seq.length) return;
  trail.length = 0;
  openRecord(seq[to]);
}

/* One path per write, shared by the button that performs it and by the Undo
   that reverses it. An undo which reimplements the call it is undoing is an
   undo that drifts away from it on the next change. */
const setStatus = (uid, status, reason) =>
  api(`/api/memories/${seg(uid)}/status`,
      { body: reason === undefined ? { status } : { status, reason } });

/* A relation is recreatable from what its own row already knew, so deleting one
   is reversible without asking you to find the pair again. `direction` is which
   end this record is on. */
const relink = (uid, rel) => api('/api/relations', {
  body: {
    from_uid: rel.direction === 'out' ? uid : rel.peer.uid,
    to_uid: rel.direction === 'out' ? rel.peer.uid : uid,
    relation_type: rel.relation_type,
    note: rel.note || '',
  },
});

export const recordOpen = () => Boolean(rec && document.contains(rec));

/* Closes the record AND anything it raised: a confirmation opened from it
   sits above it in the stack, and leaving that behind over a dialog that
   no longer exists is not a state this app should be able to reach. */
export function closeRecord() {
  while (recordOpen()) closeModal();
  rec = null;
  trail = [];
}

/* Where a render writes. The dialog exists once; opening the same record
   again, or saving from inside it, repaints these two rather than tearing
   the dialog down -- see the note on `rec`. */
const paint = (head, body) => {
  rec.querySelector('.modal-head').innerHTML = head;
  rec.querySelector('.modal-body').innerHTML = body;
};

export async function openRecord(uid) {
  /* Opening and re-rendering are the same call here -- every save ends with
     openRecord(uid) -- so the two have to be told apart. A re-render must
     not yank the caret back to the top of the dialog, and must not lose the
     place the reader had scrolled to. */
  const reopening = recordOpen();
  const keepScroll = reopening ? (rec.querySelector('.modal-body')?.scrollTop || 0) : 0;
  /* A save re-renders the SAME record -- every write here ends with
     openRecord(uid) -- and that is not a step in the trail. Only a move to
     a different memory is. */
  if (!reopening) trail = [{ uid }];
  /* `?.` because stepping through the sequence empties the trail first: the
     memory it lands on is a starting point, not somewhere you followed a
     link to */
  else if (trailTop()?.uid !== uid) trail.push({ uid });
  if (!reopening) {
    rec = openModal({
      ariaLabel: t('dr.dialogAria'),
      title: '',
      bodyHTML: '<div class="loading"><span class="spin"></span></div>',
      wide: true, tall: true,
    });
    /* Left and right step through the list the record was opened from. Bound
       to the dialog rather than the document, so a sub-form raised over it
       keeps its own arrows; skipped wherever a caret or a listbox already
       owns them. */
    rec.addEventListener('keydown', e => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      if (e.target.closest('input, textarea, select, [contenteditable], [role=listbox]')) return;
      e.preventDefault();
      stepRecord(e.key === 'ArrowRight' ? 1 : -1);
    });
  }
  let m;
  try { m = await api(`/api/memories/${seg(uid)}`); }
  catch (err) {
    /* the dialog may have been dismissed while this was in flight */
    if (!recordOpen()) return;
    paint('', failedHTML(err));
    dq('[data-retry]').addEventListener('click', () => openRecord(uid));
    return;
  }
  if (!recordOpen()) return;

  /* a diagram's content is generated from its graph, so the drawer shows
     it read-only and sends editing to the canvas; every other type gets
     the reverse view -- which flows have a step pointing at it */
  const isDiagram = m.type === 'diagram';
  const refs = m.referenced_by_diagrams || [];

  const rels = m.relations.map(r => `
    <div class="rel-row">
      <span class="rel-dir" title="${r.direction === 'out' ? t('dr.rel.out.title') : t('dr.rel.in.title')}">${icon(r.direction === 'out' ? 'arrow-right' : 'arrow-left')}</span>
      <span class="rel-type-chip">${esc(r.relation_type)}</span>
      ${r.peer.missing
        ? `<span class="rel-peer"><span class="snippet rel-gone">${t('dr.rel.missing', { uid: esc(r.peer.uid) })}</span></span>`
        : `<span class="rel-peer" data-open="${esc(r.peer.uid)}">
             <span class="type-tag ${typeClass(r.peer.type)}"><span class="dot"></span>${esc(r.peer.type)}</span>
             ${/* The peer is nowrap + ellipsis and truncates in most rows; the
                  note beside it already carried a title and this did not, so
                  the identity of the memory on the other end of a relation was
                  the one thing on the row you could not recover. */''}
             <span class="snippet" title="${esc(r.peer.snippet)}">${esc(r.peer.snippet)}</span>
             ${r.peer.status === 'archived' ? statusTag('archived') : ''}
           </span>`}
      ${r.note ? `<span class="rel-note" title="${esc(r.note)}">${esc(r.note)}</span>` : ''}
      <button type="button" class="icon-btn danger" data-delrel="${r.id}"
              title="${t('dr.rel.remove.title')}"
              aria-label="${t('dr.rel.remove.title')}">${icon('close')}</button>
    </div>`).join('') || `<div class="empty" style="padding:18px">${t('dr.rel.empty')}</div>`;

  const hist = m.edit_history.slice().reverse().map((e, i) => `
    <div class="hist-item">
      <div class="hist-when">${fmtDate(e.edited_at)} <span style="opacity:.6">· ${esc(e.edited_at)}</span></div>
      <div class="hist-note">${esc(e.note || '') || (e.prev_content !== e.new_content ? t('dr.hist.contentEdited') : t('dr.hist.entry'))}</div>
      ${e.prev_content !== e.new_content
        ? `<button type="button" class="btn btn-sm" data-diff="${i}" aria-expanded="false"
                   aria-controls="histDiff${i}" style="margin-top:6px">${t('dr.hist.viewBtn')}</button>
           <div class="hist-diff" id="histDiff${i}" data-diffbody="${i}" hidden></div>` : ''}
    </div>`).join('') || `<div class="empty" style="padding:18px">${t('dr.hist.empty')}</div>`;

  /* what a back button one step further in would call this record */
  const here = trailTop();
  if (here?.uid === uid) here.label = m.content.split('\n', 1)[0].slice(0, 70);
  const behind = trail.length > 1 ? trail[trail.length - 2] : null;
  const at = seq.indexOf(uid);

  paint(`
    <div class="record-head">
      ${behind ? `<button type="button" class="btn btn-sm record-back" id="dBack"
              title="${esc(t('dr.back.title', { label: behind.label || behind.uid }))}"
              aria-label="${esc(t('dr.back.title', { label: behind.label || behind.uid }))}"
              >${icon('arrow-left')}<span class="record-back-text"
              >${esc(behind.label || behind.uid)}</span></button>` : ''}
      ${typeTag(m.type)}
      ${uidChip(m.uid)}
      ${statusTag(m.status)}
      ${confPill(m.confidence)}
      <span class="spacer"></span>
      <!-- Where this memory sits in the list that opened it, and the way to
           the ones either side of it. Absent when the record was reached from
           somewhere with no list behind it (a relation, the audit trail, the
           canvas) -- a stepper with nowhere to step is a lie about context. -->
      ${at >= 0 && seq.length > 1 ? `<div class="record-step" role="group" aria-label="${esc(t('dr.step.aria'))}">
        <button type="button" class="icon-btn" id="dPrev" ${at === 0 ? 'disabled' : ''}
                title="${esc(t('dr.step.prev'))}" aria-label="${esc(t('dr.step.prev'))}">${icon('chevron-left')}</button>
        <span class="record-step-at">${t('dr.step.at', { i: at + 1, n: seq.length })}</span>
        <button type="button" class="icon-btn" id="dNext" ${at === seq.length - 1 ? 'disabled' : ''}
                title="${esc(t('dr.step.next'))}" aria-label="${esc(t('dr.step.next'))}">${icon('chevron-right')}</button>
      </div>` : ''}
      <button type="button" class="icon-btn" id="dClose" title="${t('dr.close.title')}"
              aria-label="${t('dr.close.title')}" style="--ico:17px">${icon('close')}</button>
    </div>`, `
    <!-- The memory on the left, everything ABOUT it on the right. One
         column put five panels of metadata under a wall of prose, so
         curating a record meant scrolling past the record to reach the
         controls for it and back up to check what they applied to. -->
    <div class="record-grid">
      <div class="record-col">

      <div class="section">
        <div class="section-label">${t('dr.content')}
          ${isDiagram
            ? `<button class="btn btn-sm btn-solid" id="dOpenEditor">${t('dr.openEditor')}</button>`
            : `<button type="button" class="btn btn-sm" id="dEdit" aria-expanded="false" aria-controls="dEditBox">${t('common.edit')}</button>`}
        </div>
        <pre class="content-pre${isDiagram ? '' : ' content-prose'}" id="dContent">${esc(m.content)}</pre>
        ${isDiagram ? `<div class="dg-empty" style="margin-top:8px">${t('dr.generated')}</div>` : `
        <div id="dEditBox" hidden style="display:grid;gap:9px;margin-top:10px">
          <!-- a placeholder is a hint, not a name: it is gone the moment
               anything is typed, and it is never announced as a label -->
          <textarea id="dEditText" rows="10" aria-label="${t('dr.content')}"></textarea>
          <input type="text" id="dEditNote" placeholder="${t('dr.editNote.placeholder')}"
                 aria-label="${t('dr.editNote.placeholder')}">
          <div class="act-row">
            <button class="btn btn-solid" id="dEditSave">${t('dr.saveVersion')}</button>
            <button class="btn" id="dEditCancel">${t('common.cancel')}</button>
            <span class="hint-sm">${t('dr.prevKept')}</span>
          </div>
        </div>`}
      </div>
      ${refs.length ? `
      <div class="section">
        <div class="section-label">${t('dr.inDiagrams')} <span class="panel-aside">(${refs.length})</span></div>
        <div class="dg-links">
          ${refs.map(r => `
            <div class="dg-link">
              <span class="dg-key">${esc(r.node_key)}</span>
              <span class="snippet clickable" data-open="${esc(r.memory_uid)}">${esc(r.title)}${r.label ? ` · ${esc(r.label)}` : ''}</span>
            </div>`).join('')}
        </div>
      </div>` : ''}

      </div>
      <div class="record-col">

      <div class="section">
        <div class="section-label">${t('dr.metadata')}
          <button class="btn btn-sm" id="dMeta">${t('common.edit')}</button>
        </div>
        <div class="meta-grid">
          <div><div class="mg-label">${t('dr.meta.domain')}</div><div class="mg-val">${m.domain ? `<button type="button" class="chip clickable" data-fdomain="${esc(m.domain)}" aria-label="${esc(t('a11y.filterDomain', { domain: m.domain }))}">${esc(m.domain)}</button>` : '—'}</div></div>
          <!-- the domains this belongs to beside the one it is filed at: one
               chip each, filtering the same way, because a cross-listing is
               a scope you can go and read, not a label -->
          ${(m.also || []).length ? `<div><div class="mg-label">${t('dr.meta.also')}</div><div class="mg-val">${
            m.also.map(p => `<button type="button" class="chip clickable" data-fdomain="${esc(p)}" aria-label="${esc(t('a11y.filterDomain', { domain: p }))}">${esc(p)}</button>`).join(' ')
          }</div></div>` : ''}
          <div><div class="mg-label">${t('dr.meta.session')}</div><div class="mg-val">${m.session ? `<button type="button" class="chip clickable" data-fsession="${esc(m.session)}" title="${esc(m.session)}" aria-label="${esc(t('a11y.filterSession', { session: m.session }))}">${esc(m.session.length > 24 ? m.session.slice(0, 24) + '…' : m.session)}</button>` : '—'}</div></div>
          <div><div class="mg-label">${t('dr.meta.tags')}</div><div class="mg-val">${esc(m.tags || '—')}</div></div>
          <div><div class="mg-label">${t('dr.meta.created')}</div><div class="mg-val" title="${esc(m.created_at)}">${fmtDate(m.created_at)}</div></div>
          <div><div class="mg-label">${t('dr.meta.updated')}</div><div class="mg-val" title="${esc(m.updated_at)}">${fmtDate(m.updated_at)}</div></div>
          <div><div class="mg-label">${t('dr.meta.size')}</div><div class="mg-val">${fmtInt(m.content.length)} ${t('common.chars')}</div></div>
          ${m.superseded_by ? `<div><div class="mg-label">${t('dr.meta.supersededBy')}</div><div class="mg-val"><button type="button" class="uid-chip" data-open="${esc(m.superseded_by)}" style="cursor:pointer" aria-label="${esc(t('a11y.openRecord', { uid: m.superseded_by }))}">${esc(m.superseded_by)}</button></div></div>` : ''}
        </div>
      </div>

      <!-- Archive rides in the heading with every other section's action,
           not in the body beside the confidence scale: down there it ended
           wherever the scale happened to end, so the one button in this
           column that is an action sat short of the two that are. -->
      <div class="section">
        <div class="section-label">${t('dr.curation')}
          ${m.status === 'active'
            ? `<button class="btn btn-sm" id="dArchive">${t('dr.archiveSoft')}</button>`
            : `<button class="btn btn-sm" id="dRestore">${t('common.restore')}</button>`}
        </div>
        <div class="seg" id="dConf" role="group" aria-label="${t('dr.curation')}">
          ${Object.keys(CONF).map(c => `<button type="button" data-c="${c}" aria-pressed="${m.confidence === c}"><span class="conf-pill c-${c}">${icon(CONF[c].icon)}</span>${CONF[c].label}</button>`).join('')}
        </div>
      </div>

      <!-- Linking opens a form of its own (core/link-picker.js). What was
           here -- a lookup field, a type select, a note field and an error
           line -- asked for the type and the note of a relation before the
           memory it describes had been chosen, offered one pick per pass,
           and showed 280 characters of the candidate to decide on. -->
      <div class="section">
        <div class="section-label">${t('dr.relations')} <span class="panel-aside">(${m.relations.length})</span>
          <button class="btn btn-sm" id="relAdd">${t('dr.rel.link')}</button>
        </div>
        ${rels}
      </div>

      <div class="section">
        <div class="section-label">${t('dr.history')} <span class="panel-aside">(${m.edit_history.length})</span></div>
        ${hist}
      </div>

      <div class="section section-bare">
        <details class="danger-zone">
          <summary>${t('dz.summary')}</summary>
          <div class="dz-body">
            <div class="dz-hint">${t('dz.hint')}</div>
            <!-- The phrase is printed HERE and nowhere else. It used to be
                 the field's placeholder as well, so an empty field showed
                 the exact text you were being asked for: nothing on screen
                 told a typed phrase from an untyped one, and the button
                 beside it read as broken rather than as waiting. -->
            <div class="dz-type">${t('dz.typeThis', { phrase: `<code>DELETE ${esc(m.uid)}</code>` })}</div>
            <div class="dz-row">
              <input type="text" id="dzPhrase" aria-label="${t('dz.phrase.aria')}" autocomplete="off">
              <button class="btn btn-danger" id="dzGo" disabled>${t('dz.button')}</button>
            </div>
            <div class="dz-state" id="dzState" role="status"></div>
          </div>
        </details>
      </div>

      </div>
    </div>`);

  /* the dialog takes the caret, so a screen reader announces it and its
     label instead of staying on the covered list behind it. openModal has
     already done that for a fresh one; a re-render must not repeat it. */
  if (reopening && keepScroll) rec.querySelector('.modal-body').scrollTop = keepScroll;

  wireCopyChips(rec);
  dq('#dClose').addEventListener('click', closeRecord);
  /* Drops the step being left rather than pushing another one, so walking
     three links deep and back leaves the trail where it started instead of
     six entries long. openRecord() below sees the previous uid already on
     top and does not re-push it. */
  dq('#dBack')?.addEventListener('click', () => {
    trail.pop();
    const prev = trailTop();
    if (prev) openRecord(prev.uid);
  });
  dq('#dPrev')?.addEventListener('click', () => stepRecord(-1));
  dq('#dNext')?.addEventListener('click', () => stepRecord(1));
  rec.querySelectorAll('[data-open]').forEach(el =>
    el.addEventListener('click', () => openRecord(el.dataset.open)));
  rec.querySelectorAll('[data-fdomain]').forEach(el =>
    el.addEventListener('click', () => { closeRecord(); go('memories', { domain: el.dataset.fdomain }); }));
  rec.querySelectorAll('[data-fsession]').forEach(el =>
    el.addEventListener('click', () => { closeRecord(); go('memories', { session: el.dataset.fsession, status: '' }); }));

  /* edit content — a diagram is edited on its canvas instead */
  if (isDiagram) {
    dq('#dOpenEditor').addEventListener('click', () => { closeRecord(); go('diagram', { uid }); });
  } else {
    const editBtn = dq('#dEdit');
    const syncEdit = open => {
      dq('#dEditBox').hidden = !open;
      editBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    };
    editBtn.addEventListener('click', () => {
      const opening = dq('#dEditBox').hidden;
      syncEdit(opening);
      if (opening) { dq('#dEditText').value = m.content; dq('#dEditText').focus(); }
    });
    dq('#dEditCancel').addEventListener('click', () => { syncEdit(false); editBtn.focus(); });
    dq('#dEditSave').addEventListener('click', async () => {
      try {
        await api(`/api/memories/${seg(uid)}/content`, {
          body: { content: dq('#dEditText').value, note: dq('#dEditNote').value } });
        toast(t('dr.contentUpdated'), 'ok');
        openRecord(uid); refreshBehind();
      } catch (err) { failed('err.save', err); }
    });
  }

  /* edit metadata */
  dq('#dMeta').addEventListener('click', () => openMetaModal(m));

  /* confidence */
  dq('#dConf').querySelectorAll('button').forEach(b => b.addEventListener('click', async () => {
    if (b.dataset.c === m.confidence) return;
    try {
      await api(`/api/memories/${seg(uid)}/confidence`, { body: { confidence: b.dataset.c } });
      toast(t('dr.confSet', { label: CONF[b.dataset.c].label }), 'ok');
      openRecord(uid); refreshBehind();
    } catch (err) { failed('err.save', err); }
  }));

  /* archive / restore */
  const dArch = dq('#dArchive');
  if (dArch) dArch.addEventListener('click', async () => {
    const reason = await promptModal({
      title: t('dr.archiveModal.title'),
      body: t('dr.archiveModal.body'),
      label: t('dr.archiveModal.label'), okLabel: t('common.archive'), danger: true });
    if (reason === null) return;
    try {
      await setStatus(uid, 'archived', reason);
      /* Archiving is reversible in the data model and was not reversible in the
         UI: the toast said "archived" and left. Restoring is the exact inverse
         and needs nothing this screen has thrown away, so it is offered here. */
      toast(t('dr.archived'), 'ok', {
        action: {
          label: t('common.undo'),
          run: () => setStatus(uid, 'active')
            .then(() => { toast(t('dr.restored'), 'ok'); openRecord(uid); refreshBehind(); })
            .catch(err => failed('err.status', err)),
        },
      });
      openRecord(uid); refreshBehind();
    } catch (err) { failed('err.status', err); }
  });
  const dRest = dq('#dRestore');
  if (dRest) dRest.addEventListener('click', async () => {
    try {
      await setStatus(uid, 'active');
      /* No Undo on this one, deliberately: putting a record back to archived
         needs the reason it was archived with, and that is not something this
         screen still knows. An "undo" that silently rewrites the reason would
         be worse than no undo at all. */
      toast(t('dr.restored'), 'ok');
      openRecord(uid); refreshBehind();
    } catch (err) { failed('err.status', err); }
  });

  /* relations */
  rec.querySelectorAll('[data-delrel]').forEach(b => b.addEventListener('click', async () => {
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
            .then(() => { toast(t('dr.rel.created'), 'ok'); openRecord(uid); })
            .catch(err => failed('err.relation', err)),
        },
      } : {});
      openRecord(uid);
    } catch (err) { failed('err.relation', err); }
  }));

  /* Several relations in one pass, all of the same type and note: the type
     and the note describe WHY these memories belong together, and a batch
     the operator chose in one go is one such statement. Anything with its
     own reason is its own trip through the picker.

     Peers already related are deliberately NOT excluded: two memories can
     be tied twice under different types ('relates_to' and 'supersedes'),
     and only an identical triple is refused by the API. */
  dq('#relAdd').addEventListener('click', async () => {
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
    openRecord(uid);
  });

  /* history diffs (lazy) */
  const histRev = m.edit_history.slice().reverse();
  rec.querySelectorAll('[data-diff]').forEach(b => b.addEventListener('click', () => {
    const i = b.dataset.diff;
    const body = rec.querySelector(`[data-diffbody="${i}"]`);
    if (body.hidden && !body.innerHTML)
      body.innerHTML = renderDiff(histRev[i].prev_content, histRev[i].new_content);
    body.hidden = !body.hidden;
    b.setAttribute('aria-expanded', body.hidden ? 'false' : 'true');
    b.textContent = body.hidden ? t('dr.hist.show') : t('dr.hist.hide');
  }));

  /* purge */
  const dzPhrase = dq('#dzPhrase'), dzGo = dq('#dzGo'), dzState = dq('#dzState');
  const dzWant = `DELETE ${uid}`;
  dzPhrase.addEventListener('input', () => {
    const typed = dzPhrase.value;
    const ok = typed === dzWant;
    dzGo.disabled = !ok;
    /* A greyed-out button cannot say why it is grey, and this one guards the
       only irreversible act in the app. So the field answers for it: silent
       until something is typed, then either what is still wrong or that the
       button is now live. */
    dzState.className = `dz-state${ok ? ' armed' : ''}`;
    dzState.textContent = ok ? t('dz.armed') : typed ? t('dz.mismatch') : '';
  });
  dzGo.addEventListener('click', async () => {
    try {
      await api(`/api/memories/${seg(uid)}/purge`, { body: { confirm: dzPhrase.value } });
      toast(t('dz.purged'), 'ok');
      closeRecord(); refreshBehind();
    } catch (err) { failed('err.purge', err); }
  });
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
        type: pickerValue(modal, 'mmType'), domain: mq('#mmDomain').value,
        also: mq('#mmAlso').value,
        tags: mq('#mmTags').value, session: mq('#mmSession').value } });
      closeModal();
      toast(r.changed.length ? t('mm.updated', { list: r.changed.join(', ') }) : t('mm.nothing'), 'ok');
      invalidateDomains();
      openRecord(m.uid); refreshBehind();
    } catch (err) { failed('err.save', err); }
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
