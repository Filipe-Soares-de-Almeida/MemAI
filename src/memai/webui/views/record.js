/* The memory record: a right-hand drawer over whatever view is showing,
   plus its metadata editor and the edit-history diff.

   Every lookup below is scoped to the drawer (dq) rather than to the
   document. The drawer and a modal can be on screen at once, so a bare
   getElementById is a collision waiting for the day two of them pick the
   same id. */

import { esc, fmtDate, fmtInt } from '../core/dom.js';
import { api, seg } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, failed, openModal, closeModal, confirmModal, promptModal,
         inertBackground } from '../core/ui.js';
import { typeTag, typeClass, confPill, uidChip, statusTag, wireCopyChips, wireUidPicker,
         failedHTML, TYPE_ORDER, CONF, relOptions, cachedDomains,
         invalidateDomains } from '../core/shared.js';
import { go, refreshBehind } from '../core/router.js';
import { t } from '../i18n.js';

const drawer = document.getElementById('drawer');
const scrim = document.getElementById('scrim');
const dq = s => drawer.querySelector(s);

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

/* The close animation outlives the click that started it: the drawer is
   emptied 300ms later, once it has slid off. Reopening a record inside
   that window used to land in the middle of it -- the record rendered,
   then the pending timeout hid the drawer and threw the markup away, so
   clicking a row right after pressing Escape opened nothing at all. */
let closeTimer = 0;
/* whatever was focused when the drawer took over the screen, so closing it
   puts the caret back on the row that opened it rather than at the top of
   the document */
let opener = null;

export function closeDrawer() {
  drawer.classList.remove('open');
  scrim.classList.remove('show');
  /* released before the focus call below: an inert subtree cannot take focus,
     so restoring it first would silently do nothing */
  inertBackground(false);
  if (opener && document.contains(opener)) opener.focus();
  opener = null;
  clearTimeout(closeTimer);
  closeTimer = setTimeout(() => {
    closeTimer = 0;
    drawer.hidden = true;
    scrim.hidden = true;
    drawer.innerHTML = '';
  }, 300);
}

scrim.addEventListener('click', closeDrawer);

export const drawerOpen = () => !drawer.hidden;

export async function openRecord(uid) {
  clearTimeout(closeTimer);         /* see closeDrawer */
  closeTimer = 0;
  /* Opening and re-rendering are the same call here -- every save ends with
     openRecord(uid) -- so the two have to be told apart. A re-render must
     not yank the caret back to the top of the panel, must not lose the place
     the reader had scrolled to, and must not overwrite the element that
     closing will return focus to. */
  const reopening = !drawer.hidden;
  const keepScroll = reopening ? (dq('.drawer-body')?.scrollTop || 0) : 0;
  if (!reopening) {
    opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    inertBackground(true);
  }
  drawer.hidden = false; scrim.hidden = false;
  requestAnimationFrame(() => { drawer.classList.add('open'); scrim.classList.add('show'); });
  drawer.innerHTML = '<div class="loading"><span class="spin"></span></div>';
  let m;
  try { m = await api(`/api/memories/${seg(uid)}`); }
  catch (err) {
    drawer.innerHTML = failedHTML(err);
    drawer.querySelector('[data-retry]').addEventListener('click', () => openRecord(uid));
    return;
  }

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
        ? `<span class="rel-peer"><span class="snippet" style="color:var(--bad)">${t('dr.rel.missing', { uid: esc(r.peer.uid) })}</span></span>`
        : `<span class="rel-peer" data-open="${esc(r.peer.uid)}">
             <span class="type-tag ${typeClass(r.peer.type)}" style="flex:none"><span class="dot"></span>${esc(r.peer.type)}</span>
             <span class="snippet">${esc(r.peer.snippet)}</span>
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

  drawer.innerHTML = `
    <div class="drawer-head">
      ${typeTag(m.type)}
      ${uidChip(m.uid)}
      ${statusTag(m.status)}
      ${confPill(m.confidence)}
      <span class="spacer"></span>
      <button type="button" class="icon-btn" id="dClose" title="${t('dr.close.title')}"
              aria-label="${t('dr.close.title')}" style="--ico:17px">${icon('close')}</button>
    </div>
    <div class="drawer-body">

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
            <span style="font-size:11px;color:var(--ink-3)">${t('dr.prevKept')}</span>
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

      <div class="section">
        <div class="section-label">${t('dr.metadata')}
          <button class="btn btn-sm" id="dMeta">${t('common.edit')}</button>
        </div>
        <div class="meta-grid">
          <div><div class="mg-label">${t('dr.meta.domain')}</div><div class="mg-val">${m.domain ? `<button type="button" class="chip clickable" data-fdomain="${esc(m.domain)}" aria-label="${esc(t('a11y.filterDomain', { domain: m.domain }))}">${esc(m.domain)}</button>` : '—'}</div></div>
          <div><div class="mg-label">${t('dr.meta.session')}</div><div class="mg-val">${m.session ? `<button type="button" class="chip clickable" data-fsession="${esc(m.session)}" title="${esc(m.session)}" aria-label="${esc(t('a11y.filterSession', { session: m.session }))}">${esc(m.session.length > 24 ? m.session.slice(0, 24) + '…' : m.session)}</button>` : '—'}</div></div>
          <div><div class="mg-label">${t('dr.meta.tags')}</div><div class="mg-val">${esc(m.tags || '—')}</div></div>
          <div><div class="mg-label">${t('dr.meta.created')}</div><div class="mg-val" title="${esc(m.created_at)}">${fmtDate(m.created_at)}</div></div>
          <div><div class="mg-label">${t('dr.meta.updated')}</div><div class="mg-val" title="${esc(m.updated_at)}">${fmtDate(m.updated_at)}</div></div>
          <div><div class="mg-label">${t('dr.meta.size')}</div><div class="mg-val">${fmtInt(m.content.length)} ${t('common.chars')}</div></div>
          ${m.superseded_by ? `<div><div class="mg-label">${t('dr.meta.supersededBy')}</div><div class="mg-val"><button type="button" class="uid-chip" data-open="${esc(m.superseded_by)}" style="cursor:pointer" aria-label="${esc(t('a11y.openRecord', { uid: m.superseded_by }))}">${esc(m.superseded_by)}</button></div></div>` : ''}
        </div>
      </div>

      <div class="section">
        <div class="section-label">${t('dr.curation')}</div>
        <div class="act-row">
          <div class="seg" id="dConf" role="group" aria-label="${t('dr.curation')}">
            ${Object.keys(CONF).map(c => `<button type="button" data-c="${c}" aria-pressed="${m.confidence === c}"><span class="conf-pill c-${c}">${icon(CONF[c].icon)}</span>${CONF[c].label}</button>`).join('')}
          </div>
          ${m.status === 'active'
            ? `<button class="btn" id="dArchive">${t('dr.archiveSoft')}</button>`
            : `<button class="btn" id="dRestore">${t('common.restore')}</button>`}
        </div>
      </div>

      <div class="section">
        <div class="section-label">${t('dr.relations')} <span class="panel-aside">(${m.relations.length})</span></div>
        ${rels}
        <div class="rel-add">
          <div class="act-row" style="align-items:stretch">
            <div class="picker" style="flex:2;min-width:200px">
              <input type="text" id="relTarget" placeholder="${t('dr.rel.target.placeholder')}"
                     aria-label="${t('dr.rel.target.placeholder')}" autocomplete="off">
              <div class="picker-results" id="relResults" hidden></div>
            </div>
            <input type="text" id="relType" list="relTypesDL" placeholder="${t('dr.rel.type.placeholder')}"
                   aria-label="${t('dr.rel.type.placeholder')}" style="flex:1;min-width:130px">
            <button class="btn" id="relCreate">${t('dr.rel.link')}</button>
          </div>
          <input type="text" id="relNote" placeholder="${t('dr.rel.note.placeholder')}"
                 aria-label="${t('dr.rel.note.placeholder')}">
          <!-- said as a corner toast until this round, about two fields the
               caret was already sitting in -->
          <div class="field-error" id="relError" role="alert" hidden></div>
          <datalist id="relTypesDL">${relOptions()}</datalist>
        </div>
      </div>

      <div class="section">
        <div class="section-label">${t('dr.history')} <span class="panel-aside">(${m.edit_history.length})</span></div>
        ${hist}
      </div>

      <div class="section">
        <details class="danger-zone">
          <summary>${t('dz.summary')}</summary>
          <div class="dz-body">
            <div class="dz-hint">${t('dz.hint', { uid: esc(m.uid) })}</div>
            <div class="act-row">
              <input type="text" id="dzPhrase" placeholder="DELETE ${esc(m.uid)}"
                     aria-label="${t('dz.phrase.aria')}" style="flex:1" autocomplete="off">
              <button class="btn btn-danger" id="dzGo" disabled>${t('dz.button')}</button>
            </div>
          </div>
        </details>
      </div>
    </div>`;

  /* the panel itself takes the caret, so a screen reader announces the
     dialog and its label instead of staying on the covered list behind it.
     tabindex="-1" in index.html is what makes it focusable. */
  if (!reopening) drawer.focus({ preventScroll: true });
  else if (keepScroll) dq('.drawer-body').scrollTop = keepScroll;

  wireCopyChips(drawer);
  dq('#dClose').addEventListener('click', closeDrawer);
  drawer.querySelectorAll('[data-open]').forEach(el =>
    el.addEventListener('click', () => openRecord(el.dataset.open)));
  drawer.querySelectorAll('[data-fdomain]').forEach(el =>
    el.addEventListener('click', () => { closeDrawer(); go('memories', { domain: el.dataset.fdomain }); }));
  drawer.querySelectorAll('[data-fsession]').forEach(el =>
    el.addEventListener('click', () => { closeDrawer(); go('memories', { session: el.dataset.fsession, status: '' }); }));

  /* edit content — a diagram is edited on its canvas instead */
  if (isDiagram) {
    dq('#dOpenEditor').addEventListener('click', () => { closeDrawer(); go('diagram', { uid }); });
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
  drawer.querySelectorAll('[data-delrel]').forEach(b => b.addEventListener('click', async () => {
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

  const relPicked = wireUidPicker({
    input: dq('#relTarget'), results: dq('#relResults'), exclude: uid,
    label: t('dr.rel.target.placeholder'),
  });

  /* Marks the field that is actually empty, next to itself, and clears the
     moment you start fixing it. Returns true when there is nothing to report. */
  const relFields = [dq('#relTarget'), dq('#relType')];
  function checkRel(target, relType) {
    const wrong = [!target, !relType];
    relFields.forEach((el, i) => el.setAttribute('aria-invalid', wrong[i] ? 'true' : 'false'));
    const box = dq('#relError');
    const bad = wrong.some(Boolean);
    box.textContent = bad ? t('dr.rel.pickBoth') : '';
    box.hidden = !bad;
    if (bad) relFields[wrong[0] ? 0 : 1].focus();
    return !bad;
  }
  relFields.forEach(el => el.addEventListener('input', () => {
    el.setAttribute('aria-invalid', 'false');
    dq('#relError').hidden = true;
  }));

  dq('#relCreate').addEventListener('click', async () => {
    const target = relPicked() || dq('#relTarget').value.trim();
    const relType = dq('#relType').value.trim();
    if (!checkRel(target, relType)) return;
    try {
      await api('/api/relations', { body: { from_uid: uid, to_uid: target, relation_type: relType, note: dq('#relNote').value } });
      toast(t('dr.rel.created'), 'ok'); openRecord(uid);
    } catch (err) { failed('err.relation', err); }
  });

  /* history diffs (lazy) */
  const histRev = m.edit_history.slice().reverse();
  drawer.querySelectorAll('[data-diff]').forEach(b => b.addEventListener('click', () => {
    const i = b.dataset.diff;
    const body = drawer.querySelector(`[data-diffbody="${i}"]`);
    if (body.hidden && !body.innerHTML)
      body.innerHTML = renderDiff(histRev[i].prev_content, histRev[i].new_content);
    body.hidden = !body.hidden;
    b.setAttribute('aria-expanded', body.hidden ? 'false' : 'true');
    b.textContent = body.hidden ? t('dr.hist.show') : t('dr.hist.hide');
  }));

  /* purge */
  const dzPhrase = dq('#dzPhrase'), dzGo = dq('#dzGo');
  dzPhrase.addEventListener('input', () => { dzGo.disabled = dzPhrase.value !== `DELETE ${uid}`; });
  dzGo.addEventListener('click', async () => {
    try {
      await api(`/api/memories/${seg(uid)}/purge`, { body: { confirm: dzPhrase.value } });
      toast(t('dz.purged'), 'ok');
      closeDrawer(); refreshBehind();
    } catch (err) { failed('err.purge', err); }
  });
}

function openMetaModal(m) {
  const dl = cachedDomains().map(d => `<option value="${esc(d.domain)}">`).join('');
  const modal = openModal({
    title: t('mm.title'),
    bodyHTML: `
      <div class="field"><label for="mmType">${t('mm.type')}</label>
        <select id="mmType">${TYPE_ORDER.map(tp => `<option ${tp === m.type ? 'selected' : ''}>${tp}</option>`).join('')}
        ${TYPE_ORDER.includes(m.type) ? '' : `<option selected>${esc(m.type)}</option>`}</select></div>
      <div class="field"><label for="mmDomain">${t('dr.meta.domain')}</label>
        <input type="text" id="mmDomain" value="${esc(m.domain)}" list="mmDomainsDL"><datalist id="mmDomainsDL">${dl}</datalist></div>
      <div class="field"><label for="mmTags">${t('mm.tags.label')}</label>
        <input type="text" id="mmTags" value="${esc(m.tags)}"></div>
      <div class="field"><label for="mmSession">${t('dr.meta.session')}</label>
        <input type="text" id="mmSession" value="${esc(m.session)}"></div>
      <div style="font-size:11px;color:var(--ink-3)">${t('mm.hint')}</div>`,
    footHTML: `<button class="btn" data-x>${t('common.cancel')}</button><button class="btn btn-solid" data-ok>${t('common.save')}</button>`,
  });
  const mq = s => modal.querySelector(s);
  mq('[data-x]').onclick = closeModal;
  mq('[data-ok]').onclick = async () => {
    try {
      const r = await api(`/api/memories/${seg(m.uid)}/meta`, { body: {
        type: mq('#mmType').value, domain: mq('#mmDomain').value,
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
