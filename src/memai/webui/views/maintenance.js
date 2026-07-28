/* Maintenance: store health, the rebuild/backup operations, the dedup
   review, and the audit trail over the edits table. */

import { $, esc, fmtInt, fmtBytes, fmtDate } from '../core/dom.js';
import { api, seg } from '../core/api.js';
import { toast, confirmModal, promptModal } from '../core/ui.js';
import { typeTag, typeClass, uidChip, statusTag, wireCopyChips,
         getDomains, TYPE_ORDER, TYPE_LABEL } from '../core/shared.js';
import { openRecord } from './record.js';
import { t } from '../i18n.js';

const OPS = {
  'fts': { path: '/api/maintenance/fts-rebuild', body: {},
           msg: r => t('mn.msg.fts', { n: fmtInt(r.rows) }) },
  'reembed-missing': { path: '/api/maintenance/reembed', body: { mode: 'missing' },
                       msg: r => t('mn.msg.backfilled', { n: fmtInt(r.embedded), t: fmtInt(r.total) }) },
  'reembed-all': { path: '/api/maintenance/reembed', body: { mode: 'all' },
                   confirm: t('mn.confirm.reembedAll'),
                   msg: r => t('mn.msg.recomputed', { n: fmtInt(r.total) }) },
  'orphans': { path: '/api/maintenance/clean-orphans', body: {},
               msg: r => t('mn.msg.orphans', { r: r.relations_removed, v: r.vectors_removed }) },
  'vacuum': { path: '/api/maintenance/vacuum', body: {},
              msg: r => t('mn.msg.vacuum', { a: fmtBytes(r.before), b: fmtBytes(r.after) }) },
  'backup': { path: '/api/maintenance/backup', body: {},
              msg: r => t('mn.msg.backup', { name: r.path.split(/[\\/]/).pop(), size: fmtBytes(r.size) }) },
};

export async function renderMaintenance(view) {
  view.innerHTML = `<div class="anim">
    <div class="view-head">
      <h2 class="view-title">${t('mn.title')}</h2>
      <div class="view-sub">${t('mn.sub')}</div>
    </div>
    <div class="grid grid-2" style="margin-bottom:14px">
      <div class="panel">
        <h3 class="panel-title">${t('mn.health')} <button class="btn btn-sm" id="hRefresh">${t('mn.rerun')}</button></h3>
        <div id="healthBody"><div class="loading"><span class="spin"></span></div></div>
      </div>
      <div class="panel">
        <h3 class="panel-title">${t('mn.ops')}</h3>
        <div class="mnt-actions">
          <button class="btn" data-op="fts">${t('mn.op.fts')}</button>
          <button class="btn" data-op="reembed-missing">${t('mn.op.reembedMissing')}</button>
          <button class="btn" data-op="reembed-all">${t('mn.op.reembedAll')}</button>
          <button class="btn" data-op="orphans">${t('mn.op.orphans')}</button>
          <button class="btn" data-op="vacuum">${t('mn.op.vacuum')}</button>
          <button class="btn btn-solid" data-op="backup">${t('mn.op.backup')}</button>
        </div>
        <h3 class="panel-title" style="margin-top:20px">${t('mn.backups')}</h3>
        <div id="backupsBody" style="font-size:11.5px;color:var(--ink-3)">—</div>
      </div>
    </div>

    <div class="panel" style="margin-bottom:14px">
      <h3 class="panel-title">${t('mn.dd.title')}
        <span class="panel-aside">${t('mn.dd.aside')}</span></h3>
      <div class="list-toolbar" style="margin-bottom:6px">
        <label style="display:flex;align-items:center;gap:8px;font-size:11.5px;color:var(--ink-3)">
          ${t('mn.dd.threshold')} <input type="range" id="ddThr" min="0.45" max="0.95" step="0.05" value="0.60" style="width:130px">
          <b id="ddThrVal" style="color:var(--ink)">0.60</b></label>
        <select id="ddType"><option value="">${t('common.allTypes')}</option>
          ${TYPE_ORDER.map(tp => `<option value="${tp}">${TYPE_LABEL[tp]}</option>`).join('')}</select>
        <input type="text" id="ddDomain" placeholder="${t('mn.dd.domainPh')}" list="ddDomainsDL" style="max-width:200px">
        <datalist id="ddDomainsDL"></datalist>
        <button class="btn btn-solid btn-sm" id="ddRun">${t('mn.dd.run')}</button>
      </div>
      <div id="ddBody"><div class="empty">${t('mn.dd.hint')}</div></div>
    </div>

    <div class="panel">
      <h3 class="panel-title">${t('mn.au.title')} <span class="panel-aside">${t('mn.au.aside')}</span>
        <button class="btn btn-sm" id="auRefresh">${t('common.refresh')}</button></h3>
      <div id="auditBody"><div class="loading"><span class="spin"></span></div></div>
    </div>
  </div>`;

  getDomains().then(ds => {
    const dl = $('#ddDomainsDL');
    /* the view may have been swapped while this was in flight */
    if (dl) dl.innerHTML = ds.map(d => `<option value="${esc(d.domain)}">`).join('');
  }).catch(() => {});

  const loadHealth = async () => {
    const h = await api('/api/maintenance/health');
    const rows = [];
    const push = (level, name, detail) =>
      rows.push(`<div class="check-row"><span class="check-dot ${level}"></span>
        <span class="check-name">${name}</span><span class="check-detail">${detail}</span></div>`);
    push(h.integrity.ok ? 'ok' : 'bad', t('mn.h.integrity'), h.integrity.detail ? esc(h.integrity.detail) : t('mn.h.quickClean'));
    push(h.fts.ok ? 'ok' : 'bad', t('mn.h.fts'),
      `${esc(h.fts.detail)} · ${t('mn.h.rows', { a: fmtInt(h.fts.rows), b: fmtInt(h.fts.expected) })}`);
    if (!h.vectors.ready) push('warn', t('mn.h.vectors'), t('mn.h.vecUnavailable'));
    else push(h.vectors.missing === 0 && h.vectors.orphans === 0 ? 'ok' : 'warn', t('mn.h.vectors'),
      `${t('mn.h.vecDetail', { a: fmtInt(h.vectors.rows), b: fmtInt(h.vectors.expected), m: h.vectors.missing, o: h.vectors.orphans })} · ${esc((h.vectors.model || '').split(/[\\/]/).pop())} ${esc(h.vectors.dim)}d${h.vectors.model_available ? '' : t('mn.h.modelUnavailable')}`);
    push(h.relations.orphans === 0 ? 'ok' : 'warn', t('mn.h.relations'),
      h.relations.orphans === 0 ? t('mn.h.noOrphans') : t('mn.h.orphanEdges', { n: h.relations.orphans }));
    push(h.file.reclaimable > 262144 ? 'warn' : 'ok', t('mn.h.disk'),
      t('mn.h.diskDetail', { size: fmtBytes(h.file.size), wal: h.file.wal_size ? ` + ${fmtBytes(h.file.wal_size)} WAL` : '', rec: fmtBytes(h.file.reclaimable) }));
    if (!$('#healthBody')) return;
    $('#healthBody').innerHTML = rows.join('');
    $('#backupsBody').innerHTML = h.backups.length
      ? h.backups.map(b => `<div class="backup-row"><span>${esc(b.name)}</span><span>${fmtBytes(b.size)}</span></div>`).join('')
      : t('mn.backups.empty');
  };
  loadHealth().catch(err => {
    if ($('#healthBody')) $('#healthBody').innerHTML = `<div class="empty">${esc(err.message)}</div>`;
  });
  $('#hRefresh').addEventListener('click', () => loadHealth().catch(e => toast(e.message, 'bad')));

  view.querySelectorAll('[data-op]').forEach(b => b.addEventListener('click', async () => {
    const op = OPS[b.dataset.op];
    if (op.confirm && !(await confirmModal({ title: t('mn.confirm.title'), body: op.confirm, okLabel: t('common.run') }))) return;
    b.disabled = true;
    const prev = b.textContent;
    b.innerHTML = '<span class="spin"></span>';
    try {
      const r = await api(op.path, { body: op.body });
      toast(op.msg(r), 'ok');
      loadHealth().catch(() => {});
    } catch (err) { toast(err.message, 'bad'); }
    b.disabled = false;
    b.textContent = prev;
  }));

  /* dedup */
  $('#ddThr').addEventListener('input', e => {
    $('#ddThrVal').textContent = Number(e.target.value).toFixed(2);
  });
  $('#ddRun').addEventListener('click', async () => {
    const body = $('#ddBody');
    body.innerHTML = '<div class="loading"><span class="spin"></span></div>';
    try {
      const qs = new URLSearchParams({ threshold: $('#ddThr').value });
      if ($('#ddType').value) qs.set('type', $('#ddType').value);
      if ($('#ddDomain').value.trim()) qs.set('domain', $('#ddDomain').value.trim());
      const r = await api(`/api/maintenance/dedup?${qs}`);
      if (!r.pairs.length) { body.innerHTML = `<div class="empty">${t('mn.dd.none')}</div>`; return; }
      body.innerHTML = r.pairs.map((p, i) => `
        <div class="dedup-pair">
          <div style="display:flex;justify-content:space-between;align-items:baseline">
            <span style="font-size:11px;color:var(--ink-3)">${t('mn.dd.overlap')} <b style="color:var(--ink)">${(p.ratio * 100).toFixed(0)}%</b></span>
            <button class="btn btn-sm" data-linkdup="${i}">${t('mn.dd.linkDup')}</button>
          </div>
          <div class="ratio-bar"><div class="ratio-fill" style="width:${(p.ratio * 100).toFixed(0)}%"></div></div>
          <div class="pair-cards">
            ${[p.a, p.b].map(mm => `
              <div class="pair-card">
                <div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap">
                  ${typeTag(mm.type)} ${uidChip(mm.uid)} ${statusTag(mm.status)}
                  <span style="color:var(--ink-4);font-size:10px">${fmtDate(mm.created_at)}</span>
                </div>
                ${mm.domain ? `<span class="chip">${esc(mm.domain)}</span>` : ''}
                <div class="snippet">${esc(mm.content)}</div>
                <div class="act-row">
                  <button class="btn btn-sm" data-openm="${esc(mm.uid)}">${t('common.openRecord')}</button>
                  <button class="btn btn-sm" data-archm="${esc(mm.uid)}">${t('mn.dd.archiveThis')}</button>
                </div>
              </div>`).join('')}
          </div>
        </div>`).join('');
      wireCopyChips(body);
      body.querySelectorAll('[data-openm]').forEach(btn =>
        btn.addEventListener('click', () => openRecord(btn.dataset.openm)));
      body.querySelectorAll('[data-archm]').forEach(btn =>
        btn.addEventListener('click', async () => {
          const reason = await promptModal({
            title: t('mn.dd.archTitle'), label: t('bulk.reason.label'),
            placeholder: t('mn.dd.archPh'), okLabel: t('common.archive'), danger: true });
          if (reason === null) return;
          try {
            await api(`/api/memories/${seg(btn.dataset.archm)}/status`, {
              body: { status: 'archived', reason: reason || t('mn.dd.dupReason') } });
            toast(t('dr.archived'), 'ok');
            btn.closest('.pair-card').style.opacity = .4;
          } catch (err) { toast(err.message, 'bad'); }
        }));
      body.querySelectorAll('[data-linkdup]').forEach(btn =>
        btn.addEventListener('click', async () => {
          const p = r.pairs[btn.dataset.linkdup];
          try {
            await api('/api/relations', { body: { from_uid: p.a.uid, to_uid: p.b.uid, relation_type: 'duplicates', note: t('mn.dd.linkNote', { p: (p.ratio * 100).toFixed(0) }) } });
            toast(t('mn.dd.linked'), 'ok');
            btn.disabled = true;
          } catch (err) { toast(err.message, 'bad'); }
        }));
    } catch (err) { body.innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
  });

  /* audit */
  const loadAudit = async () => {
    const r = await api('/api/audit?limit=120');
    const host = $('#auditBody');
    if (!host) return;
    host.innerHTML = r.entries.length ? `
      <table class="table">
        <thead><tr><th>${t('mn.au.th.when')}</th><th>${t('mn.au.th.memory')}</th><th>${t('common.domain')}</th><th>${t('mn.au.th.event')}</th><th class="num">${t('mn.au.th.delta')}</th></tr></thead>
        <tbody>${r.entries.map(e => `
          <tr style="cursor:pointer" data-uid="${esc(e.memory_uid)}">
            <td style="white-space:nowrap" title="${esc(e.edited_at)}">${fmtDate(e.edited_at)}</td>
            <td><span class="type-tag ${typeClass(e.type)}"><span class="dot"></span>${esc(e.memory_uid)}</span></td>
            <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(e.domain || '—')}</td>
            <td style="max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(e.note)}">${esc(e.note || '') || t('mn.au.contentEdit')}</td>
            <td class="num">${e.content_changed ? `${e.prev_len} → ${e.new_len}` : '<span style="color:var(--ink-4)">—</span>'}</td>
          </tr>`).join('')}</tbody>
      </table>` : `<div class="empty">${t('mn.au.empty')}</div>`;
    host.querySelectorAll('[data-uid]').forEach(tr =>
      tr.addEventListener('click', () => openRecord(tr.dataset.uid)));
  };
  loadAudit().catch(err => {
    if ($('#auditBody')) $('#auditBody').innerHTML = `<div class="empty">${esc(err.message)}</div>`;
  });
  $('#auRefresh').addEventListener('click', () => loadAudit().catch(e => toast(e.message, 'bad')));
}
