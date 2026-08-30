/* Maintenance: store health, the rebuild/backup operations, the dedup
   review, and the audit trail over the edits table. */

import { $, esc, fmtInt, fmtBytes, fmtDate } from '../core/dom.js';
import { api, seg } from '../core/api.js';
import { toast, failed, confirmModal, promptModal } from '../core/ui.js';
import { typeTag, typeClass, uidChip, statusTag, wireCopyChips, failedHTML, retryable,
         getDomains, typeItems, domainDatalist } from '../core/shared.js';
import { pickerFor, pickerValue, setPickerValue, wirePicker, fixedItems } from '../core/pick.js';
import { openRecord } from './record.js';
import { t } from '../i18n.js';

/* Free pages worth an amber dot and a mention. Below it the file is simply
   in use and compacting would give back nothing anyone would notice. */
const RECLAIM_WARN = 262144;

const OPS = {
  'fts': { path: '/api/maintenance/fts-rebuild', body: {},
           msg: r => t('mn.msg.fts', { n: fmtInt(r.rows) }) },
  /* Both of these delete and both are irreversible, so both ask first.
     Clean orphans DELETES rows; VACUUM rewrites the file and discards the
     free pages an undo would have needed. */
  'orphans': { path: '/api/maintenance/clean-orphans', body: {},
               confirm: t('mn.confirm.orphans'),
               msg: r => t('mn.msg.orphans', { r: r.relations_removed }) },
  'vacuum': { path: '/api/maintenance/vacuum', body: {},
              confirm: t('mn.confirm.vacuum'),
              msg: r => t('mn.msg.vacuum', { a: fmtBytes(r.before), b: fmtBytes(r.after) }) },
  'backup': { path: '/api/maintenance/backup', body: {},
              msg: r => t('mn.msg.backup', { name: r.path.split(/[\\/]/).pop(), size: fmtBytes(r.size) }) },
  /* Confirm-first like the two above, for a different reason: nothing is
     deleted, but the bodies whose fields sit under a header line are
     rewritten. The server backs the store up before it starts. */
  'sectionize': { path: '/api/maintenance/sectionize', body: {},
                  confirm: t('mn.confirm.sectionize'),
                  msg: r => t('mn.msg.sectionize', { t: fmtInt(r.total), n: fmtInt(r.rewritten),
                                                     q: fmtInt(r.needs_review) }) },
  /* Clearing renders is NOT in the confirm-first group above: a render is a
     cache of a diagram that is still there, so the worst case is that the
     next read redraws it. */
  'prune-renders': { path: '/api/maintenance/prune-renders', body: {},
                     msg: r => t('mn.msg.pruned', { n: fmtInt(r.pruned), size: fmtBytes(r.bytes) }) },
  'prune-renders-all': { path: '/api/maintenance/prune-renders', body: { all: true },
                         msg: r => t('mn.msg.pruned', { n: fmtInt(r.pruned), size: fmtBytes(r.bytes) }) },
};

/* Must match db.SVG_RETENTION_MODES; the server rejects anything else. */
const RETENTION = ['1d', '7d', '30d', 'never'];
/* The intervals worth a click. `memai-hook stop --warden-minutes` takes any
   value the store accepts; these are the ones a person actually picks. */
const WARDEN_MINUTES = [5, 10, 20, 30, 60];

export async function renderMaintenance(view) {
  const keepItems = RETENTION.map(m => ({ value: m, label: t('mn.rn.mode.' + m) }));
  const onOffItems = [{ value: 'on', label: t('mn.wd.on') },
                      { value: 'off', label: t('mn.wd.off') }];
  const everyItems = WARDEN_MINUTES.map(
    n => ({ value: String(n), label: t('mn.wd.mins', { n }) }));
  const types = typeItems({ any: t('common.allTypes') });
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
          <button class="btn" data-op="orphans">${t('mn.op.orphans')}</button>
          <button class="btn" data-op="vacuum">${t('mn.op.vacuum')}</button>
          <button class="btn btn-solid" data-op="backup">${t('mn.op.backup')}</button>
        </div>
        <h3 class="panel-title" style="margin-top:20px">${t('mn.backups')}</h3>
        <div id="backupsBody" class="hint">—</div>

        <!-- Renders are the one thing here that accumulates on disk without
             anyone asking, so the setting sits next to what it affects
             rather than in a settings page nobody opens. -->
        <h3 class="panel-title" style="margin-top:20px">${t('mn.rn.title')}
          <span class="panel-aside">${t('mn.rn.aside')}</span></h3>
        <div class="list-toolbar toolbar-sm" style="margin-bottom:6px">
          <label class="inline-label">${t('mn.rn.keep')}
            ${pickerFor({ id: 'rnKeep', items: keepItems, ariaLabel: t('mn.rn.keep') })}</label>
          <button class="btn btn-sm" data-op="prune-renders">${t('mn.rn.now')}</button>
          <button class="btn btn-sm" data-op="prune-renders-all">${t('mn.rn.all')}</button>
        </div>
        <div id="rnBody" class="hint">—</div>

        <!-- The warden is the one thing here that spends TOKENS rather than
             disk, and it spends them whether or not it finds anything, so the
             switch belongs where the other running costs are read. -->
        <h3 class="panel-title" style="margin-top:20px">${t('mn.wd.title')}
          <span class="panel-aside">${t('mn.wd.aside')}</span></h3>
        <div class="list-toolbar toolbar-sm" style="margin-bottom:6px">
          <label class="inline-label">${t('mn.wd.state')}
            ${pickerFor({ id: 'wdOn', items: onOffItems, ariaLabel: t('mn.wd.state') })}</label>
          <label class="inline-label">${t('mn.wd.every')}
            ${pickerFor({ id: 'wdEvery', items: everyItems, ariaLabel: t('mn.wd.every') })}</label>
        </div>
        <div class="hint">${t('mn.wd.body')}</div>
      </div>
    </div>

    <div class="panel" style="margin-bottom:14px">
      <h3 class="panel-title">${t('mn.dd.title')}
        <span class="panel-aside">${t('mn.dd.aside')}</span></h3>
      <div class="list-toolbar toolbar-sm" style="margin-bottom:6px">
        <label class="inline-label">
          ${t('mn.dd.threshold')} <input type="range" id="ddThr" min="0.45" max="0.95" step="0.05" value="0.60">
          <b id="ddThrVal">0.60</b></label>
        ${pickerFor({ id: 'ddType', items: types, ariaLabel: t('common.allTypes') })}
        <input type="text" id="ddDomain" placeholder="${t('mn.dd.domainPh')}"
               aria-label="${t('mn.dd.domainPh')}" list="ddDomainsDL" style="max-width:200px">
        <datalist id="ddDomainsDL"></datalist>
        <button class="btn btn-solid btn-sm" id="ddRun">${t('mn.dd.run')}</button>
      </div>
      <div id="ddBody"><div class="empty">${t('mn.dd.hint')}</div></div>
    </div>

    <div class="panel" style="margin-bottom:14px">
      <h3 class="panel-title">${t('mn.sc.title')}
        <span class="panel-aside">${t('mn.sc.aside')}</span>
        <button class="btn btn-sm" data-op="sectionize">${t('mn.sc.run')}</button></h3>
      <div id="scBody"><div class="loading"><span class="spin"></span></div></div>
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
    if (dl) dl.innerHTML = domainDatalist(ds);
  }).catch(() => {});

  const loadHealth = retryable('#healthBody', async () => {
    const h = await api('/api/maintenance/health');
    const rows = [];
    const push = (level, name, detail) =>
      rows.push(`<div class="check-row"><span class="check-dot ${level}"></span>
        <span class="check-name">${name}</span><span class="check-detail">${detail}</span></div>`);
    /* the server sends a detail only when something is wrong, and that
       detail is SQLite's own message -- so the healthy wording is ours */
    push(h.integrity.ok ? 'ok' : 'bad', t('mn.h.integrity'),
      h.integrity.detail ? esc(h.integrity.detail) : t('mn.h.quickClean'));
    push(h.fts.ok ? 'ok' : 'bad', t('mn.h.fts'),
      `${h.fts.detail ? esc(h.fts.detail) : t('mn.h.ftsConsistent')} · ${t('mn.h.rows', { a: fmtInt(h.fts.rows), b: fmtInt(h.fts.expected) })}`);
    push(h.relations.orphans === 0 ? 'ok' : 'warn', t('mn.h.relations'),
      h.relations.orphans === 0 ? t('mn.h.noOrphans') : t('mn.h.orphanEdges', { n: h.relations.orphans }));
    /* the same threshold decides the dot and whether naming what freed the
       pages helps: under it there is nothing to compact, so the reason is
       noise rather than a prompt */
    const reclaim = h.file.reclaimable > RECLAIM_WARN;
    const why = reclaim && h.file.compact_reason === 'vector_store'
      ? ` · ${t('mn.h.diskFromVectors')}` : '';
    push(reclaim ? 'warn' : 'ok', t('mn.h.disk'),
      t('mn.h.diskDetail', { size: fmtBytes(h.file.size), wal: h.file.wal_size ? ` + ${fmtBytes(h.file.wal_size)} WAL` : '', rec: fmtBytes(h.file.reclaimable) }) + why);
    if (!$('#healthBody')) return;
    $('#healthBody').innerHTML = rows.join('');
    $('#backupsBody').innerHTML = h.backups.length
      ? h.backups.map(b => `<div class="backup-row"><span>${esc(b.name)}</span><span>${fmtBytes(b.size)}</span></div>`).join('')
      : t('mn.backups.empty');
    /* health already carries the count, the size and the active window, so
       the renders row rides the same fetch rather than adding one */
    const rn = $('#rnBody');
    if (rn) {
      rn.innerHTML = h.renders.files
        ? t('mn.rn.usage', { n: fmtInt(h.renders.files), size: fmtBytes(h.renders.bytes) })
        : t('mn.rn.empty');
      const keep = $('#rnKeep');
      /* the control reflects the stored value; only a pick writes one */
      const stored = keepItems.find(it => it.value === h.renders.retention);
      if (keep && stored) setPickerValue(keep, stored);
    }
  });
  loadHealth();

  const loadSections = retryable('#scBody', async () => {
    const s = await api('/api/maintenance/sections-queue');
    const body = $('#scBody');
    if (!body) return;
    /* three states, and they are not the same: a store nobody has read, one
       that came out clean, and one holding bodies a human has to settle */
    const head = !s.migrated ? t('mn.sc.notRead')
      : s.queue.length ? t('mn.sc.pending', { n: fmtInt(s.queue.length) })
      : t('mn.sc.clean');
    body.innerHTML = `<div class="hint" style="margin-bottom:8px">${head}</div>`
      + s.queue.map(e => `<button type="button" class="sc-row" data-uid="${esc(e.uid)}">
          <span class="sc-row-head">${typeTag(e.type)} ${statusTag(e.status)}
            <span class="sc-row-uid">${esc(e.uid)}</span>
            <span class="sc-domain">${esc(e.domain || '')}</span></span>
          <span class="sc-detail">${esc(e.detail)}</span>
          <span class="sc-snippet">${esc(e.snippet)}</span>
        </button>`).join('');
    body.querySelectorAll('.sc-row').forEach(
      r => r.addEventListener('click', () => openRecord(r.dataset.uid)));
  });
  loadSections();

  wirePicker(view, { id: 'rnKeep', items: fixedItems(keepItems), onPick: async mode => {
    try {
      await api('/api/config', { body: { svg_retention: mode } });
      toast(t('mn.msg.retention', { mode: t('mn.rn.mode.' + mode) }), 'ok');
    } catch (err) { failed('err.maintenance', err); }
  } });

  /* Both controls reflect the stored value; only a pick writes one. The
     interval stays on file while the warden is off, so turning it back on
     does not lose the choice. */
  api('/api/config').then(cfg => {
    const on = $('#wdOn');
    const every = $('#wdEvery');
    const state = onOffItems.find(it => it.value === (cfg.warden_enabled ? 'on' : 'off'));
    const mins = everyItems.find(it => it.value === String(cfg.warden_minutes));
    if (on && state) setPickerValue(on, state);
    if (every && mins) setPickerValue(every, mins);
  }).catch(() => {});

  wirePicker(view, { id: 'wdOn', items: fixedItems(onOffItems), onPick: async value => {
    const enabled = value === 'on';
    try {
      const cfg = await api('/api/config', { body: { warden_enabled: enabled } });
      toast(enabled ? t('mn.msg.wardenOn', { n: cfg.warden_minutes })
                    : t('mn.msg.wardenOff'), 'ok');
    } catch (err) { failed('err.maintenance', err); }
  } });

  wirePicker(view, { id: 'wdEvery', items: fixedItems(everyItems), onPick: async value => {
    try {
      await api('/api/config', { body: { warden_minutes: Number(value) } });
      toast(t('mn.msg.wardenEvery', { n: value }), 'ok');
    } catch (err) { failed('err.maintenance', err); }
  } });
  wirePicker(view, { id: 'ddType', items: fixedItems(types), onPick: () => {} });
  /* the same wrapped loader the failure's own Retry calls, so Refresh and Retry
     cannot end up doing two different things */
  $('#hRefresh').addEventListener('click', loadHealth);

  view.querySelectorAll('[data-op]').forEach(b => b.addEventListener('click', async () => {
    const op = OPS[b.dataset.op];
    if (op.confirm && !(await confirmModal({ title: t('mn.confirm.title'), body: op.confirm, okLabel: t('common.run') }))) return;
    b.disabled = true;
    b.setAttribute('aria-busy', 'true');
    const prev = b.textContent;
    /* the label stays beside the spinner: replacing it left the button with
       no accessible name at all, and nothing on screen saying which of the
       six operations was the one running */
    b.innerHTML = `<span class="spin"></span>${esc(prev)}`;
    try {
      const r = await api(op.path, { body: op.body });
      toast(op.msg(r), 'ok');
      loadHealth().catch(() => {});
      loadSections().catch(() => {});
    } catch (err) { failed('err.maintenance', err); }
    b.disabled = false;
    b.removeAttribute('aria-busy');
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
      if (pickerValue(view, 'ddType')) qs.set('type', pickerValue(view, 'ddType'));
      if ($('#ddDomain').value.trim()) qs.set('domain', $('#ddDomain').value.trim());
      const r = await api(`/api/maintenance/dedup?${qs}`);
      if (!r.pairs.length) { body.innerHTML = `<div class="empty">${t('mn.dd.none')}</div>`; return; }
      body.innerHTML = r.pairs.map((p, i) => `
        <div class="dedup-pair">
          <div style="display:flex;justify-content:space-between;align-items:baseline">
            <span class="hint-sm">${t('mn.dd.overlap')} <b style="color:var(--ink)">${(p.ratio * 100).toFixed(0)}%</b></span>
            <button class="btn btn-sm" data-linkdup="${i}">${t('mn.dd.linkDup')}</button>
          </div>
          <div class="ratio-bar"><div class="ratio-fill" style="--v:${p.ratio.toFixed(3)}"></div></div>
          <div class="pair-cards">
            ${[p.a, p.b].map(mm => `
              <div class="pair-card">
                <div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap">
                  ${typeTag(mm.type)} ${uidChip(mm.uid)} ${statusTag(mm.status)}
                  <span class="hint-sm">${fmtDate(mm.created_at)}</span>
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
            /* sink the card a step instead of dimming it: the snippet is what
               you would re-read to check you archived the right one */
            btn.closest('.pair-card').classList.add('decided');
          } catch (err) { failed('err.status', err); }
        }));
      body.querySelectorAll('[data-linkdup]').forEach(btn =>
        btn.addEventListener('click', async () => {
          const p = r.pairs[btn.dataset.linkdup];
          try {
            await api('/api/relations', { body: { from_uid: p.a.uid, to_uid: p.b.uid, relation_type: 'duplicates', note: t('mn.dd.linkNote', { p: (p.ratio * 100).toFixed(0) }) } });
            toast(t('mn.dd.linked'), 'ok');
            btn.disabled = true;
          } catch (err) { failed('err.relation', err); }
        }));
    } catch (err) {
      body.innerHTML = failedHTML(err);
      /* the scan's inputs are untouched and still on screen, so retrying is
         literally pressing the button that started it */
      body.querySelector('[data-retry]').addEventListener('click', () => $('#ddRun').click());
    }
  });

  /* audit */
  const loadAudit = retryable('#auditBody', async () => {
    const r = await api('/api/audit?limit=120');
    const host = $('#auditBody');
    if (!host) return;
    host.innerHTML = r.entries.length ? `
      <div class="table-scroll">
      <table class="table">
        <thead><tr><th>${t('mn.au.th.when')}</th><th>${t('mn.au.th.memory')}</th><th>${t('common.domain')}</th><th>${t('mn.au.th.event')}</th><th class="num">${t('mn.au.th.delta')}</th></tr></thead>
        <tbody>${r.entries.map(e => `
          <tr class="clickable" data-uid="${esc(e.memory_uid)}">
            <td style="white-space:nowrap" title="${esc(e.edited_at)}">${fmtDate(e.edited_at)}</td>
            <td><button type="button" class="type-tag ${typeClass(e.type)}"
                        aria-label="${esc(t('a11y.openRecord', { uid: e.memory_uid }))}"><span class="dot"></span>${esc(e.memory_uid)}</button></td>
            <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(e.domain || '—')}</td>
            <td style="max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(e.note)}">${esc(e.note || '') || t('mn.au.contentEdit')}</td>
            <td class="num">${e.content_changed ? `${e.prev_len} → ${e.new_len}` : '<span style="color:var(--ink-3)">—</span>'}</td>
          </tr>`).join('')}</tbody>
      </table>
      </div>` : `<div class="empty">${t('mn.au.empty')}</div>`;
    host.querySelectorAll('[data-uid]').forEach(tr =>
      tr.addEventListener('click', () => openRecord(tr.dataset.uid)));
  });
  loadAudit();
  $('#auRefresh').addEventListener('click', loadAudit);
}
