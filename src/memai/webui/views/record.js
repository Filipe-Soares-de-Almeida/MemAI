/* The memory record: a right-hand drawer over whatever view is showing,
   plus its metadata editor and the edit-history diff.

   Every lookup below is scoped to the drawer (dq) rather than to the
   document. The drawer and a modal can be on screen at once, so a bare
   getElementById is a collision waiting for the day two of them pick the
   same id. */

import { esc, fmtDate, fmtInt, debounce } from '../core/dom.js';
import { api, seg } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, openModal, closeModal, confirmModal, promptModal } from '../core/ui.js';
import { typeTag, typeClass, typeColor, confPill, uidChip, statusTag, wireCopyChips,
         TYPE_ORDER, CONF, relOptions, cachedDomains, invalidateDomains } from '../core/shared.js';
import { go, refreshBehind } from '../core/router.js';
import { t } from '../i18n.js';

const drawer = document.getElementById('drawer');
const scrim = document.getElementById('scrim');
const dq = s => drawer.querySelector(s);

/* The close animation outlives the click that started it: the drawer is
   emptied 300ms later, once it has slid off. Reopening a record inside
   that window used to land in the middle of it -- the record rendered,
   then the pending timeout hid the drawer and threw the markup away, so
   clicking a row right after pressing Escape opened nothing at all. */
let closeTimer = 0;

export function closeDrawer() {
  drawer.classList.remove('open');
  scrim.classList.remove('show');
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
  drawer.hidden = false; scrim.hidden = false;
  requestAnimationFrame(() => { drawer.classList.add('open'); scrim.classList.add('show'); });
  drawer.innerHTML = '<div class="loading"><span class="spin"></span></div>';
  let m;
  try { m = await api(`/api/memories/${seg(uid)}`); }
  catch (err) { drawer.innerHTML = `<div class="empty">${esc(err.message)}</div>`; return; }

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
      ${r.note ? `<span class="icon-btn" title="${esc(r.note)}" style="cursor:help">${icon('info')}</span>` : ''}
      <button class="icon-btn danger" data-delrel="${r.id}" title="${t('dr.rel.remove.title')}">${icon('close')}</button>
    </div>`).join('') || `<div class="empty" style="padding:18px">${t('dr.rel.empty')}</div>`;

  const hist = m.edit_history.slice().reverse().map((e, i) => `
    <div class="hist-item">
      <div class="hist-when">${fmtDate(e.edited_at)} <span style="opacity:.6">· ${esc(e.edited_at)}</span></div>
      <div class="hist-note">${esc(e.note || '') || (e.prev_content !== e.new_content ? t('dr.hist.contentEdited') : t('dr.hist.entry'))}</div>
      ${e.prev_content !== e.new_content
        ? `<button class="btn btn-sm" data-diff="${i}" style="margin-top:6px">${t('dr.hist.viewBtn')}</button>
           <div class="hist-diff" data-diffbody="${i}" hidden></div>` : ''}
    </div>`).join('') || `<div class="empty" style="padding:18px">${t('dr.hist.empty')}</div>`;

  drawer.innerHTML = `
    <div class="drawer-head">
      ${typeTag(m.type)}
      ${uidChip(m.uid)}
      ${statusTag(m.status)}
      ${confPill(m.confidence)}
      <span class="spacer"></span>
      <button class="icon-btn" id="dClose" title="${t('dr.close.title')}" style="--ico:17px">${icon('close')}</button>
    </div>
    <div class="drawer-body">

      <div class="section">
        <div class="section-label">${t('dr.content')}
          ${isDiagram
            ? `<button class="btn btn-sm btn-solid" id="dOpenEditor">${t('dr.openEditor')}</button>`
            : `<button class="btn btn-sm" id="dEdit">${t('common.edit')}</button>`}
        </div>
        <pre class="content-pre" id="dContent">${esc(m.content)}</pre>
        ${isDiagram ? `<div class="dg-empty" style="margin-top:8px">${t('dr.generated')}</div>` : `
        <div id="dEditBox" hidden style="display:grid;gap:9px;margin-top:10px">
          <textarea id="dEditText" rows="10"></textarea>
          <input type="text" id="dEditNote" placeholder="${t('dr.editNote.placeholder')}">
          <div class="act-row">
            <button class="btn btn-solid" id="dEditSave">${t('dr.saveVersion')}</button>
            <button class="btn" id="dEditCancel">${t('common.cancel')}</button>
            <span style="font-size:11px;color:var(--ink-4)">${t('dr.prevKept')}</span>
          </div>
        </div>`}
      </div>
      ${refs.length ? `
      <div class="section">
        <div class="section-label">${t('dr.inDiagrams')} <span style="letter-spacing:0;text-transform:none">(${refs.length})</span></div>
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
          <div><div class="mg-label">${t('dr.meta.domain')}</div><div class="mg-val">${m.domain ? `<span class="chip clickable" data-fdomain="${esc(m.domain)}">${esc(m.domain)}</span>` : '—'}</div></div>
          <div><div class="mg-label">${t('dr.meta.session')}</div><div class="mg-val">${m.session ? `<span class="chip clickable" data-fsession="${esc(m.session)}" title="${esc(m.session)}">${esc(m.session.length > 24 ? m.session.slice(0, 24) + '…' : m.session)}</span>` : '—'}</div></div>
          <div><div class="mg-label">${t('dr.meta.tags')}</div><div class="mg-val">${esc(m.tags || '—')}</div></div>
          <div><div class="mg-label">${t('dr.meta.created')}</div><div class="mg-val" title="${esc(m.created_at)}">${fmtDate(m.created_at)}</div></div>
          <div><div class="mg-label">${t('dr.meta.updated')}</div><div class="mg-val" title="${esc(m.updated_at)}">${fmtDate(m.updated_at)}</div></div>
          <div><div class="mg-label">${t('dr.meta.size')}</div><div class="mg-val">${fmtInt(m.content.length)} ${t('common.chars')}</div></div>
          ${m.superseded_by ? `<div><div class="mg-label">${t('dr.meta.supersededBy')}</div><div class="mg-val"><span class="uid-chip" data-open="${esc(m.superseded_by)}" style="cursor:pointer">${esc(m.superseded_by)}</span></div></div>` : ''}
        </div>
      </div>

      <div class="section">
        <div class="section-label">${t('dr.curation')}</div>
        <div class="act-row">
          <div class="seg" id="dConf">
            ${Object.keys(CONF).map(c => `<button data-c="${c}" class="${m.confidence === c ? 'active' : ''}"><span class="conf-pill c-${c}">${icon(CONF[c].icon)}</span>${CONF[c].label}</button>`).join('')}
          </div>
          ${m.status === 'active'
            ? `<button class="btn" id="dArchive">${t('dr.archiveSoft')}</button>`
            : `<button class="btn" id="dRestore">${t('common.restore')}</button>`}
        </div>
      </div>

      <div class="section">
        <div class="section-label">${t('dr.relations')} <span style="letter-spacing:0;text-transform:none">(${m.relations.length})</span></div>
        ${rels}
        <div class="rel-add">
          <div class="act-row" style="align-items:stretch">
            <div class="picker" style="flex:2;min-width:200px">
              <input type="text" id="relTarget" placeholder="${t('dr.rel.target.placeholder')}" autocomplete="off">
              <div class="picker-results" id="relResults" hidden></div>
            </div>
            <input type="text" id="relType" list="relTypesDL" placeholder="${t('dr.rel.type.placeholder')}" style="flex:1;min-width:130px">
            <button class="btn" id="relCreate">${t('dr.rel.link')}</button>
          </div>
          <input type="text" id="relNote" placeholder="${t('dr.rel.note.placeholder')}">
          <datalist id="relTypesDL">${relOptions()}</datalist>
        </div>
      </div>

      <div class="section">
        <div class="section-label">${t('dr.history')} <span style="letter-spacing:0;text-transform:none">(${m.edit_history.length})</span></div>
        ${hist}
      </div>

      <div class="section">
        <details class="danger-zone">
          <summary>${t('dz.summary')}</summary>
          <div class="dz-body">
            <div class="dz-hint">${t('dz.hint', { uid: esc(m.uid) })}</div>
            <div class="act-row">
              <input type="text" id="dzPhrase" placeholder="DELETE ${esc(m.uid)}" style="flex:1" autocomplete="off">
              <button class="btn btn-danger" id="dzGo" disabled>${t('dz.button')}</button>
            </div>
          </div>
        </details>
      </div>
    </div>`;

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
    dq('#dEdit').addEventListener('click', () => {
      const box = dq('#dEditBox');
      box.hidden = !box.hidden;
      if (!box.hidden) { dq('#dEditText').value = m.content; dq('#dEditText').focus(); }
    });
    dq('#dEditCancel').addEventListener('click', () => { dq('#dEditBox').hidden = true; });
    dq('#dEditSave').addEventListener('click', async () => {
      try {
        await api(`/api/memories/${seg(uid)}/content`, {
          body: { content: dq('#dEditText').value, note: dq('#dEditNote').value } });
        toast(t('dr.contentUpdated'), 'ok');
        openRecord(uid); refreshBehind();
      } catch (err) { toast(err.message, 'bad'); }
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
    } catch (err) { toast(err.message, 'bad'); }
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
      await api(`/api/memories/${seg(uid)}/status`, { body: { status: 'archived', reason } });
      toast(t('dr.archived'), 'ok'); openRecord(uid); refreshBehind();
    } catch (err) { toast(err.message, 'bad'); }
  });
  const dRest = dq('#dRestore');
  if (dRest) dRest.addEventListener('click', async () => {
    try {
      await api(`/api/memories/${seg(uid)}/status`, { body: { status: 'active' } });
      toast(t('dr.restored'), 'ok'); openRecord(uid); refreshBehind();
    } catch (err) { toast(err.message, 'bad'); }
  });

  /* relations */
  drawer.querySelectorAll('[data-delrel]').forEach(b => b.addEventListener('click', async () => {
    const ok = await confirmModal({
      title: t('dr.rel.removeModal.title'),
      body: t('dr.rel.removeModal.body'),
      okLabel: t('dr.rel.removeModal.ok'), danger: true });
    if (!ok) return;
    try {
      await api(`/api/relations/${seg(b.dataset.delrel)}`, { method: 'DELETE' });
      toast(t('dr.rel.removed'), 'ok'); openRecord(uid);
    } catch (err) { toast(err.message, 'bad'); }
  }));

  let relPick = null;
  const relInput = dq('#relTarget'), relResults = dq('#relResults');
  const doLookup = debounce(async () => {
    const q = relInput.value.trim();
    try {
      const r = await api(`/api/lookup?q=${seg(q)}&exclude=${seg(uid)}`);
      relResults.innerHTML = r.items.map(it => `
        <div class="picker-item" data-pick="${esc(it.uid)}">
          <span class="dot" style="--c:${typeColor(it.type)}"></span>
          <span class="uid-chip" style="cursor:inherit">${esc(it.uid)}</span>
          <span class="snippet">${esc(it.snippet)}</span>
        </div>`).join('') || `<div class="picker-item">${t('lookup.empty')}</div>`;
      relResults.hidden = false;
      relResults.querySelectorAll('[data-pick]').forEach(it => it.addEventListener('mousedown', () => {
        relPick = it.dataset.pick;
        relInput.value = it.dataset.pick;
        relResults.hidden = true;
      }));
    } catch { /* lookup is best-effort */ }
  }, 280);
  relInput.addEventListener('input', () => { relPick = null; doLookup(); });
  relInput.addEventListener('focus', doLookup);
  relInput.addEventListener('blur', () => setTimeout(() => { relResults.hidden = true; }, 180));

  dq('#relCreate').addEventListener('click', async () => {
    const target = relPick || relInput.value.trim();
    const relType = dq('#relType').value.trim();
    if (!target || !relType) { toast(t('dr.rel.pickBoth'), 'bad'); return; }
    try {
      await api('/api/relations', { body: { from_uid: uid, to_uid: target, relation_type: relType, note: dq('#relNote').value } });
      toast(t('dr.rel.created'), 'ok'); openRecord(uid);
    } catch (err) { toast(err.message, 'bad'); }
  });

  /* history diffs (lazy) */
  const histRev = m.edit_history.slice().reverse();
  drawer.querySelectorAll('[data-diff]').forEach(b => b.addEventListener('click', () => {
    const i = b.dataset.diff;
    const body = drawer.querySelector(`[data-diffbody="${i}"]`);
    if (body.hidden && !body.innerHTML)
      body.innerHTML = renderDiff(histRev[i].prev_content, histRev[i].new_content);
    body.hidden = !body.hidden;
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
    } catch (err) { toast(err.message, 'bad'); }
  });
}

function openMetaModal(m) {
  const dl = cachedDomains().map(d => `<option value="${esc(d.domain)}">`).join('');
  const modal = openModal({
    title: t('mm.title'),
    bodyHTML: `
      <div class="field"><label>${t('mm.type')}</label>
        <select id="mmType">${TYPE_ORDER.map(tp => `<option ${tp === m.type ? 'selected' : ''}>${tp}</option>`).join('')}
        ${TYPE_ORDER.includes(m.type) ? '' : `<option selected>${esc(m.type)}</option>`}</select></div>
      <div class="field"><label>${t('dr.meta.domain')}</label>
        <input type="text" id="mmDomain" value="${esc(m.domain)}" list="mmDomainsDL"><datalist id="mmDomainsDL">${dl}</datalist></div>
      <div class="field"><label>${t('mm.tags.label')}</label>
        <input type="text" id="mmTags" value="${esc(m.tags)}"></div>
      <div class="field"><label>${t('dr.meta.session')}</label>
        <input type="text" id="mmSession" value="${esc(m.session)}"></div>
      <div style="font-size:11px;color:var(--ink-4)">${t('mm.hint')}</div>`,
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
    } catch (err) { toast(err.message, 'bad'); }
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
