/* Optimization runs: batches of suggested edits waiting for a human to
   accept or reject them. Level 1 is a grid of runs, level 2 is one run
   with its suggestions grouped by kind. */

import { $, esc, fmtDate } from '../core/dom.js';
import { api, seg } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, failed, confirmModal, setPressed } from '../core/ui.js';
import { typeTag, uidChip, statusTag, wireCopyChips, failedHTML } from '../core/shared.js';
import { go } from '../core/router.js';
import { openRecord } from './record.js';
import { t } from '../i18n.js';

/* ─── one suggestion, rendered by kind ───────────────────────────────── */

/* the kinds the before/after pair below knows how to render. Anything else
   is shown as its payload rather than as two empty boxes -- see optRaw. */
const DIFF_KINDS = new Set(['compact', 'reword', 'retag', 'redomain',
                            'set_confidence', 'archive']);

function optBefore(s) {
  const tg = s.target || {};
  switch (s.kind) {
    case 'compact': case 'reword': return esc(tg.snippet || '');
    case 'retag': return esc(tg.tags || '—');
    case 'redomain': return esc(tg.domain || '—');
    case 'set_confidence': return esc(tg.confidence || '—');
    case 'archive': return esc(tg.status || 'active');
    default: return '';
  }
}

function optAfter(s) {
  const p = s.payload || {};
  switch (s.kind) {
    case 'compact': case 'reword': return esc(p.new_content || '');
    case 'retag': return esc(p.tags || '—');
    case 'redomain': return esc(p.domain || '—');
    case 'set_confidence': return esc(p.confidence || '—');
    case 'archive': return 'archived' + (p.reason ? ` · ${esc(p.reason)}` : '');
    default: return '';
  }
}

function optRelBody(s) {
  const p = s.payload || {}, peers = s.peers || {};
  const pair = s.kind === 'link'
    ? [[t('op.role.from'), peers.from_uid], [t('op.role.to'), peers.to_uid]]
    : [[t('op.role.keep'), peers.keep_uid], [t('op.role.drop'), peers.drop_uid]];
  const rel = s.kind === 'link' ? esc(p.relation_type || 'relates_to') : 'supersedes';
  const areas = ['l1', 'l2'], bodies = ['b1', 'b2'];
  return `<div class="opt-rel">
    ${pair.map(([role, m], i) => `
      <span class="opt-label" style="grid-area:${areas[i]}">${esc(role)}</span>
      <div class="opt-peer-body" style="grid-area:${bodies[i]}">
        ${m ? `<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">${typeTag(m.type)} ${uidChip(m.uid)} ${statusTag(m.status)}</div>
          <div class="snippet">${esc(m.snippet || '')}</div>` : `<div class="snippet">${t('op.missing')}</div>`}
      </div>`).join('')}
    <div class="opt-arrow" title="${t('op.relType.title')}">${rel}${icon('arrow-right')}</div>
  </div>`;
}

function optDistillBody(s) {
  const p = s.payload || {};
  const srcs = (s.sources || []).map(m => `
    <div class="opt-peer-body">
      ${m && !m.missing ? `<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">${typeTag(m.type)} ${uidChip(m.uid)} ${statusTag(m.status)}</div>
        <div class="snippet">${esc(m.snippet || '')}</div>` : `<div class="snippet">${t('op.missing')}</div>`}
    </div>`).join('');
  return `<div class="opt-distill">
    <span class="opt-label">${t('op.distill.sources', { n: (s.sources || []).length })}</span>
    ${srcs}
    <div class="opt-arrow" title="${t('op.relType.title')}">supersedes${icon('arrow-right')}</div>
    <span class="opt-label">${t('op.distill.new')}${p.new_type ? ` · ${esc(p.new_type)}` : ''}${p.domain ? ` · ${esc(p.domain)}` : ''}</span>
    <div class="snippet">${esc(p.new_content || '')}</div>
  </div>`;
}

/* A kind with no renderer here still has to be readable. This used to fall
   through to an empty before/after pair with a live Apply button under it,
   which asked for a decision about nothing. */
function optRaw(s) {
  return `<div class="opt-unknown">
    <span class="opt-label">${t('op.unknownKind')}</span>
    <pre class="snippet">${esc(JSON.stringify(s.payload ?? {}, null, 2))}</pre>
  </div>`;
}

function optButtons(s) {
  if (s.status === 'pending') return `
    <button class="btn btn-solid btn-sm" data-apply="${s.id}">${t('common.apply')}</button>
    <button class="btn btn-sm" data-reject="${s.id}">${t('common.reject')}</button>`;
  if (s.status === 'applied') return `<button class="btn btn-sm" data-revert="${s.id}">${t('common.undo')}</button>`;
  return '';
}

function optPreview(s) {
  if (!s.target) return '';
  return `<div class="opt-preview">
    <span class="opt-label">${t('op.underReview')}</span>
    <div class="snippet">${esc(s.target.snippet || '')}</div>
  </div>`;
}

function optCard(s) {
  const statusChip = s.status === 'applied' ? `<span class="status-tag opt-applied">${t('op.applied')}</span>`
    : s.status === 'rejected' ? `<span class="status-tag archived">${t('op.rejected')}</span>` : '';
  const verified = s.verified
    ? `<div class="opt-verified" title="${t('op.verifiedTitle')}">${icon('confirmed')}${t('op.verified', { v: esc(s.verified) })}</div>`
    : `<div class="opt-verified muted">${icon('unverified')}${t('op.noVerified')}</div>`;
  const relKind = s.kind === 'link' || s.kind === 'merge' || s.kind === 'distill';
  const bodyHtml = s.kind === 'distill' ? optDistillBody(s)
    : relKind ? optRelBody(s)
    : !DIFF_KINDS.has(s.kind) ? optRaw(s) : `<div class="opt-diff">
      <span class="opt-label" style="grid-area:bl">${t('op.before')}</span>
      <span class="opt-label" style="grid-area:al">${t('op.after')}</span>
      <div class="snippet" style="grid-area:bs">${optBefore(s)}</div>
      <div class="opt-arrow" style="grid-area:arrow">→</div>
      <div class="snippet" style="grid-area:as">${optAfter(s)}</div>
    </div>`;
  const openUid = s.target_uid || s.new_uid;   /* distill: open the created memory once applied */
  return `<div class="pair-card opt-card ${s.status !== 'pending' ? 'decided' : ''}">
    <div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap">
      <span class="opt-kind">${esc(s.kind)}</span>
      ${s.target_uid ? uidChip(s.target_uid) : ''}
      ${statusChip}
      <span style="flex:1"></span>
      ${openUid ? `<button class="btn btn-sm" data-openopt="${esc(openUid)}">${t('common.openRecord')}</button>` : ''}
      ${optButtons(s)}
    </div>
    ${s.rationale ? `<div class="opt-rationale">${esc(s.rationale)}</div>` : ''}
    ${relKind ? '' : optPreview(s)}
    ${bodyHtml}
    ${verified}
  </div>`;
}

/* "applied 8 · 2 failed" used to be painted 'bad' -- total-failure red for a
   mostly-successful batch -- and res.failed, which carries the id AND the reason
   for every one that did not go through, was thrown away. So the screen said
   something went wrong and gave you no way to find out what. A partial result is
   a warning, and the ids go where they can be read. */
function reportApplied(res) {
  const bad = res.failed || [];
  if (!bad.length) { toast(t('op.toast.appliedN', { n: res.applied }), 'ok'); return; }
  toast(t('op.toast.appliedN', { n: res.applied }) + t('op.toast.failedN', { m: bad.length }),
        'warn', { detail: bad.map(f => `#${f.id}: ${f.error}`).join(' · ') });
}

/* ─── entry point ────────────────────────────────────────────────────── */

export async function renderOptimization(view, params, ctx) {
  const runs = (await api('/api/optimization/runs')).runs;
  if (ctx.stale()) return;
  const runId = Number(params.get('run') || 0);
  const meta = runId ? runs.find(r => r.id === runId) : null;
  if (meta) renderOptRun(view, meta);
  else renderOptRunList(view, runs);
}

/* level 1 — searchable grid of run cards */

function optRunCard(r) {
  const state = !r.total ? `<span class="opt-run-state s-empty">${t('op.card.empty')}</span>`
    : r.pending ? `<span class="opt-run-state s-pending">${t('op.card.pendingN', { n: r.pending })}</span>`
    : `<span class="opt-run-state s-done">${t('op.card.done')}</span>`;
  const seg2 = (n, color, label) => n
    ? `<div class="meter-seg" style="flex:${n};background:${color}" title="${esc(label)}: ${n}"></div>` : '';
  const meter = r.total ? `<div class="meter opt-run-meter">
      ${seg2(r.applied, 'var(--ok)', t('op.applied'))}
      ${seg2(r.pending, 'var(--warn)', t('op.pending'))}
      ${seg2(r.rejected, 'var(--bad)', t('op.rejected'))}
    </div>` : '';
  const kinds = (r.kinds || []).map(k =>
    `<span class="opt-kind-chip${k.pending ? ' has-pending' : ''}">${esc(k.kind)}<b>${k.pending ? `${k.pending}/` : ''}${k.total}</b></span>`).join('');
  const backup = r.backup_path
    ? `<span class="opt-run-backup" title="${esc(t('op.backupNote', { name: r.backup_path.split(/[\\/]/).pop() }))}">${icon('confirmed')}${t('op.card.backup')}</span>` : '';
  return `<div class="opt-run-card${r.pending ? ' has-pending' : ''}" data-run="${r.id}" role="button" tabindex="0"
       aria-label="${esc(t('op.card.aria', { id: r.id, n: r.total, p: r.pending }))}">
    <div class="opt-run-top">
      <span class="opt-run-id">#${r.id}</span>
      <span class="opt-run-date">${fmtDate(r.created_at)} · ${t('op.nSuggestions', { n: r.total })}</span>
      <span style="flex:1"></span>
      ${state}
    </div>
    ${r.note ? `<div class="opt-run-note" title="${esc(r.note)}">${esc(r.note)}</div>` : ''}
    ${meter}
    <div class="opt-run-foot">
      <span>${t('op.summary', { p: r.pending, a: r.applied, r: r.rejected })}</span>
      <span style="flex:1"></span>
      ${backup}
    </div>
    ${kinds ? `<div class="opt-run-kinds">${kinds}</div>` : ''}
  </div>`;
}

function renderOptRunList(view, runs) {
  view.innerHTML = `<div class="anim">
    <div class="view-head">
      <h2 class="view-title">${t('op.title')}</h2>
      <div class="view-sub">${t('op.sub')}</div>
    </div>
    ${runs.length ? `
    <div class="list-toolbar">
      <input type="search" id="optSearch" placeholder="${t('op.searchRuns')}" aria-label="${t('op.searchRuns')}">
      <button type="button" class="btn btn-sm" id="optOnlyPending" aria-pressed="false">${t('op.onlyPending')}</button>
      <span class="panel-aside" id="optRunsCount" aria-live="polite"></span>
    </div>
    <div class="opt-run-grid" id="optRunGrid"></div>
    ` : `<div class="empty">${t('op.emptyRuns')}</div>`}
  </div>`;
  if (!runs.length) return;

  let q = '', onlyPending = false;
  const grid = $('#optRunGrid');
  const draw = () => {
    const needle = q.trim().toLowerCase();
    const shown = runs.filter(r => {
      if (onlyPending && !r.pending) return false;
      if (!needle) return true;
      const hay = `#${r.id} ${r.note || ''} ${fmtDate(r.created_at)} ${(r.kinds || []).map(k => k.kind).join(' ')}`.toLowerCase();
      return needle.split(/\s+/).every(w => hay.includes(w));
    });
    $('#optRunsCount').textContent = t('op.runsCount', { n: shown.length });
    grid.innerHTML = shown.length ? shown.map(optRunCard).join('')
      : `<div class="empty" style="grid-column:1/-1">${t('op.noRunsMatch')}</div>`;
    grid.querySelectorAll('.opt-run-card').forEach(card => {
      const open = () => go('optimization', { run: card.dataset.run });
      card.addEventListener('click', open);
      card.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
      });
    });
  };
  $('#optSearch').addEventListener('input', e => { q = e.target.value; draw(); });
  const pendBtn = $('#optOnlyPending');
  pendBtn.addEventListener('click', () => {
    onlyPending = !onlyPending;
    setPressed(pendBtn, onlyPending);
    draw();
  });
  draw();
}

/* level 2 — one run, suggestions grouped by kind */

function renderOptRun(view, initialMeta) {
  const runId = initialMeta.id;
  let meta = initialMeta;
  view.innerHTML = `<div class="anim">
    <a class="opt-back" href="#/optimization">${icon('chevron-left')}${t('op.backToRuns')}</a>
    <div class="view-head" style="margin-top:8px">
      <h2 class="view-title">${t('op.runTitle', { id: runId })} <em class="opt-run-when">· ${fmtDate(meta.created_at)}</em></h2>
      ${meta.note ? `<div class="view-sub">${esc(meta.note)}</div>` : ''}
    </div>
    <div class="panel" style="margin-bottom:14px">
      <div class="list-toolbar" style="align-items:center;margin-bottom:0">
        <span id="optSummary" class="panel-aside"></span>
        <span style="flex:1"></span>
        <button type="button" class="btn btn-sm" id="optHideApplied" aria-pressed="false"></button>
        <button type="button" class="btn btn-sm" id="optApplyAll">${t('op.applyAll')}</button>
        <button type="button" class="btn btn-danger btn-sm" id="optDiscard">${t('op.discard')}</button>
      </div>
      <div id="optBackup" style="font-size:11px;color:var(--ink-3);margin-top:6px"></div>
    </div>
    <div id="optBody"><div class="loading"><span class="spin"></span></div></div>
  </div>`;

  const syncMeta = () => {
    $('#optSummary').textContent = t('op.summary', { p: meta.pending, a: meta.applied, r: meta.rejected });
    $('#optBackup').textContent = meta.backup_path
      ? t('op.backupNote', { name: meta.backup_path.split(/[\\/]/).pop() }) : '';
  };
  const refreshMeta = async () => {
    const rs = (await api('/api/optimization/runs')).runs;
    meta = rs.find(r => r.id === runId) || meta;
    if ($('#optSummary')) syncMeta();
  };
  syncMeta();

  let hideApplied = false;
  const hideBtn = $('#optHideApplied');
  const syncHideBtn = () => {
    hideBtn.textContent = hideApplied ? t('op.showApplied') : t('op.hideApplied');
    setPressed(hideBtn, hideApplied);
  };
  syncHideBtn();

  const loadRun = async () => {
    const body = $('#optBody');
    if (!body) return;
    /* Every accept, reject and undo comes back through here, so this is a
       re-read of the same run far more often than it is a first read. Which
       groups were open and where the reader had scrolled to are theirs, not
       the render's -- deciding twenty suggestions used to mean being sent
       back to the top of a fully re-folded page twenty times. */
    const scroller = $('#view');
    const keepScroll = scroller ? scroller.scrollTop : 0;
    const wasOpen = new Set([...body.querySelectorAll('.opt-group')]
      .filter(d => d.open).map(d => d.dataset.kind));
    const rerender = !!body.querySelector('.opt-group');
    body.innerHTML = '<div class="loading"><span class="spin"></span></div>';
    try {
      const r = await api(`/api/optimization/suggestions?run=${seg(runId)}`);
      if (!body.isConnected) return;
      if (!r.suggestions.length) { body.innerHTML = `<div class="empty">${t('op.emptyRun')}</div>`; return; }
      const shown = hideApplied ? r.suggestions.filter(s => s.status !== 'applied') : r.suggestions;
      if (!shown.length) { body.innerHTML = `<div class="empty">${t('op.allApplied')}</div>`; return; }

      const groups = new Map();
      shown.forEach(s => { if (!groups.has(s.kind)) groups.set(s.kind, []); groups.get(s.kind).push(s); });
      body.innerHTML = [...groups.entries()].map(([kind, list]) => {
        const pend = list.filter(s => s.status === 'pending').length;
        const count = pend ? t('op.group.countPending', { p: pend, t: list.length })
          : t('op.group.countAll', { t: list.length });
        /* open by default on a first read, and as the reader left it after */
        const open = rerender ? wasOpen.has(kind) : true;
        return `<details class="opt-group" data-kind="${esc(kind)}"${open ? ' open' : ''}>
          <summary>
            ${icon('chevron-right', { cls: 'opt-group-caret' })}
            <span class="opt-group-kind">${esc(kind)}</span>
            <span class="opt-group-count">${count}</span>
            <span style="flex:1"></span>
            ${pend ? `<button class="btn btn-sm" data-applykind="${esc(kind)}" data-npend="${pend}">${t('op.group.apply', { n: pend })}</button>` : ''}
          </summary>
          <div class="opt-group-body">${list.map(optCard).join('')}</div>
        </details>`;
      }).join('');
      if (scroller) scroller.scrollTop = keepScroll;
      wireCopyChips(body);

      const act = (btn, path, bodyObj) => async () => {
        btn.disabled = true;
        try {
          const res = await api(path, { body: bodyObj });
          toast(res && res.backup ? t('op.toast.appliedBackup') : t('op.toast.done'), 'ok');
          await refreshMeta();
          await loadRun();
        } catch (err) { failed('err.optimize', err); btn.disabled = false; }
      };
      body.querySelectorAll('[data-apply]').forEach(b => b.addEventListener('click', act(b, '/api/optimization/apply', { id: +b.dataset.apply })));
      body.querySelectorAll('[data-reject]').forEach(b => b.addEventListener('click', act(b, '/api/optimization/reject', { id: +b.dataset.reject })));
      body.querySelectorAll('[data-revert]').forEach(b => b.addEventListener('click', act(b, '/api/optimization/revert', { id: +b.dataset.revert })));
      body.querySelectorAll('[data-openopt]').forEach(b => b.addEventListener('click', () => openRecord(b.dataset.openopt)));
      body.querySelectorAll('[data-applykind]').forEach(b => b.addEventListener('click', async e => {
        e.preventDefault();   /* keep the <details> from toggling */
        e.stopPropagation();
        const kind = b.dataset.applykind, n = +b.dataset.npend;
        if (!(await confirmModal({ title: t('op.group.applyConfirm.title'),
          body: t('op.group.applyConfirm.body', { n, kind, id: runId }),
          okLabel: t('op.group.applyConfirm.ok') }))) return;
        b.disabled = true;
        try {
          const res = await api('/api/optimization/apply-all', { body: { run: runId, kind } });
          reportApplied(res);
          await refreshMeta();
          await loadRun();
        } catch (err) { failed('err.optimize', err); b.disabled = false; }
      }));
    } catch (err) {
      if (!body.isConnected) return;
      body.innerHTML = failedHTML(err);
      body.querySelector('[data-retry]').addEventListener('click', loadRun);
    }
  };

  hideBtn.addEventListener('click', () => {
    hideApplied = !hideApplied;
    syncHideBtn();
    loadRun();
  });

  $('#optApplyAll').addEventListener('click', async () => {
    if (!meta.pending) { toast(t('op.toast.nothingPending'), ''); return; }
    if (!(await confirmModal({ title: t('op.applyAllConfirm.title'),
      body: t('op.applyAllConfirm.body', { n: meta.pending, id: runId }),
      okLabel: t('op.applyAllConfirm.ok') }))) return;
    try {
      const r = await api('/api/optimization/apply-all', { body: { run: runId } });
      reportApplied(r);
      await refreshMeta();
      await loadRun();
    } catch (err) { failed('err.optimize', err); }
  });

  $('#optDiscard').addEventListener('click', async () => {
    if (!(await confirmModal({ title: t('op.discardConfirm.title'),
      body: t('op.discardConfirm.body', { id: runId }),
      okLabel: t('op.discardConfirm.ok'), danger: true }))) return;
    try {
      await api(`/api/optimization/runs/${seg(runId)}`, { method: 'DELETE' });
      toast(t('op.toast.discarded'), 'ok');
      go('optimization');
    } catch (err) { failed('err.optimize', err); }
  });

  loadRun();
}
