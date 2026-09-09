/* Maintenance: the store's own upkeep, as six workspaces behind one tab
   strip -- the backups it has taken, the file they are copies of, the
   bodies it could not read into fields, the memories that say the same
   thing, everything that has happened to it, and the warden that reads it
   on a session's behalf.

   Only ONE of those is ever on screen. The other five stay built and
   hidden rather than being thrown away, because two of them hold work
   that cannot be re-made for free: a dedup scan is a quadratic sweep over
   the whole store, and a log you have scrolled back through is a place
   you were reading. */

import { $, esc, fmtInt, fmtBytes, fmtDate, fmtAgo } from '../core/dom.js';
import { api, seg } from '../core/api.js';
import { toast, failed, confirmModal, promptModal } from '../core/ui.js';
import { typeTag, typeClass, uidChip, statusTag, wireCopyChips, failedHTML, retryable,
         getDomains, typeItems, domainDatalist } from '../core/shared.js';
import { pickerFor, pickerValue, setPickerValue, wirePicker, fixedItems } from '../core/pick.js';
import { icon } from '../core/icons.js';
import { openRecord } from './record.js';
import { I18N, t } from '../i18n.js';

/* The address of each workspace, as ?tab= and as the order of the strip. */
const TABS = ['backups', 'storage', 'sections', 'dupes', 'log', 'warden'];

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

const DAY = 86400000;

/* What a backup was taken FOR, out of its own filename. db.backup_name
   writes `<project>-[<kind>-]<YYYYmmdd>-<HHMMSS>.db`, and the kind is the
   only record of the reason -- nothing in the store says why a file on
   disk exists. */
function backupKind(name, project) {
  let s = String(name).replace(/\.db$/i, '');
  const head = `${project}-`;
  if (s.toLowerCase().startsWith(head.toLowerCase())) s = s.slice(head.length);
  return s.replace(/-?\d{8}-\d{6}$/, '');
}

function reasonLabel(kind) {
  const run = /^optimize-run(\d+)$/.exec(kind);
  if (run) return t('mn.bk.reason.optimize', { n: run[1] });
  if (kind === 'sectionize') return t('mn.bk.reason.sectionize');
  if (!kind) return t('mn.bk.reason.hand');
  return kind;
}

/* The shelf's date buckets. Today and this week are named; anything older
   falls back to its month, with the year once it is not this one. */
function dateBucket(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return t('mn.bk.older');
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return t('mn.bk.today');
  if (now - d < 7 * DAY) return t('mn.bk.thisWeek');
  const month = I18N.months[d.getMonth()] || '';
  return d.getFullYear() === now.getFullYear() ? month : `${month} ${d.getFullYear()}`;
}

/* A stacked bar's segments, as percentages of their own total. Widths, not
   values: a segment of zero has to come out as zero rather than as a
   hairline, and the last one takes the rounding. */
function barHTML(parts) {
  const total = parts.reduce((n, p) => n + Math.max(0, p.value), 0) || 1;
  return `<div class="mnt-bar">${parts.map(p =>
    `<span style="width:${(Math.max(0, p.value) / total * 100).toFixed(2)}%;background:${p.fill}"></span>`).join('')}</div>`;
}

const legendHTML = parts => `<div class="mnt-legend">${parts.map(p =>
  `<span><span class="dot" style="--c:${p.fill}"></span>${esc(p.name)} · ${fmtBytes(p.value)}</span>`).join('')}</div>`;

export async function renderMaintenance(view, params) {
  const keepItems = RETENTION.map(m => ({ value: m, label: t('mn.rn.mode.' + m) }));
  const onOffItems = [{ value: 'on', label: t('mn.wd.on') },
                      { value: 'off', label: t('mn.wd.off') }];
  const everyItems = WARDEN_MINUTES.map(
    n => ({ value: String(n), label: t('mn.wd.mins', { n }) }));
  const types = typeItems({ any: t('common.allTypes') });

  const opened = TABS.includes(params.get('tab')) ? params.get('tab') : TABS[0];

  view.innerHTML = `<div class="anim">
    <h2 class="sr-only">${t('mn.title')}</h2>
    <div class="mnt-head">
      <div class="mnt-tabs" role="tablist" aria-label="${t('mn.title')}">
        ${TABS.map(id => `<button type="button" class="mnt-tab" role="tab" data-tab="${id}"
            id="mntTab-${id}" aria-controls="mntPanel-${id}" aria-selected="${id === opened}">
            ${t('mn.tab.' + id)}<span class="mnt-tab-badge" data-badge="${id}" hidden></span></button>`).join('')}
      </div>
      <!-- Which store these tabs are acting on. The CONDITION it is in is
           read on Health, which counts the same defects and owns the
           index; this view only acts, so it says which file it is acting
           on and leaves the diagnosis there. -->
      <div class="mnt-state">
        <span class="mnt-store" id="mntStore">${t('mn.checks.running')}</span>
        <button class="btn btn-sm" id="hRefresh">${t('common.refresh')}</button>
      </div>
    </div>
    <div class="mnt-panels">
      ${TABS.map(id => `<section class="mnt-panel" role="tabpanel" data-panel="${id}"
          id="mntPanel-${id}" aria-labelledby="mntTab-${id}" tabindex="0"
          ${id === opened ? '' : 'hidden'}></section>`).join('')}
    </div>
  </div>`;

  /* the last health answer: the store line, and the numbers two of the tabs
     are made of -- fetched once rather than once per tab */
  let health = null;
  const built = new Set();
  const panel = id => view.querySelector(`[data-panel="${id}"]`);

  /* ── the tab strip ─────────────────────────────────────────────────── */

  const BUILD = {};

  function show(id) {
    view.querySelectorAll('.mnt-tab').forEach(
      b => b.setAttribute('aria-selected', String(b.dataset.tab === id)));
    view.querySelectorAll('.mnt-panel').forEach(
      p => { p.hidden = p.dataset.panel !== id; });
    /* replaceState, not go(): a tab is an ADDRESS worth deep-linking, not a
       step worth pressing Back through. Six of them in the history would
       put five presses between this view and the one you came from. */
    history.replaceState(null, '', `#/maintenance?tab=${id}`);
    if (!built.has(id)) { built.add(id); BUILD[id](); }
  }

  view.querySelectorAll('.mnt-tab').forEach(b =>
    b.addEventListener('click', () => show(b.dataset.tab)));

  /* Left/Right walk the strip, which is what a tablist owes a keyboard --
     without it the only way between six workspaces is six Tab presses. */
  view.querySelector('.mnt-tabs').addEventListener('keydown', e => {
    const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const at = TABS.indexOf(e.target.dataset.tab);
    const next = TABS[(at + step + TABS.length) % TABS.length];
    show(next);
    view.querySelector(`#mntTab-${next}`).focus();
  });

  const badge = (id, n) => {
    const el = view.querySelector(`[data-badge="${id}"]`);
    if (!el) return;
    el.textContent = n ? fmtInt(n) : '';
    el.hidden = !n;
  };

  /* ── health: the store line, and the data two tabs are made of ────── */

  const loadHealth = retryable('#mntStore', async () => {
    const h = await api('/api/maintenance/health');
    const line = $('#mntStore');
    if (!line) return;   /* the view was swapped mid-flight */
    health = h;
    line.textContent = `${h.project} · ${fmtBytes(h.file.size)}`;
    if (built.has('backups')) paintBackups();
    if (built.has('storage')) paintStorage();
  });

  $('#hRefresh').addEventListener('click', loadHealth);

  /* ── operations, wherever their buttons ended up ───────────────────── */

  function wireOps(root) {
    root.querySelectorAll('[data-op]').forEach(b => b.addEventListener('click', async () => {
      const op = OPS[b.dataset.op];
      if (op.confirm && !(await confirmModal({ title: t('mn.confirm.title'), body: op.confirm, okLabel: t('common.run') }))) return;
      b.disabled = true;
      b.setAttribute('aria-busy', 'true');
      const prev = b.textContent;
      /* the label stays beside the spinner: replacing it left the button with
         no accessible name at all, and nothing on screen saying which of the
         operations was the one running */
      b.innerHTML = `<span class="spin"></span>${esc(prev)}`;
      try {
        const r = await api(op.path, { body: op.body });
        toast(op.msg(r), 'ok');
        loadHealth().catch(() => {});
        if (built.has('sections')) loadSections().catch(() => {});
      } catch (err) { failed('err.maintenance', err); }
      b.disabled = false;
      b.removeAttribute('aria-busy');
      b.textContent = prev;
    }));
  }

  /* ── backups ───────────────────────────────────────────────────────── */

  /* Two shelves' worth of state: which one is being read, how it is
     grouped, and what is ticked on it. A tick belongs to the fresh shelf --
     a file inside a zip cannot be acted on without unzipping first. */
  let bkGroup = 'date';
  let bkZip = null;          /* the archive being read, or null for the shelf */
  let bkSel = new Set();
  let shelf = null;          /* /api/maintenance/backups, in full */

  /* health carries a SHORT list of backups for the summary strip. The shelf
     is drawn from its own endpoint instead: a file the list does not show
     cannot be ticked, and a shelf that hides its thirteenth file is a shelf
     that cannot archive it. */
  const loadShelf = retryable('#bkBody', async () => {
    const fresh = await api('/api/maintenance/backups');
    if (!$('#bkBody')) return;
    shelf = fresh;
    const names = new Set(shelf.shelf.map(f => f.name));
    bkSel = new Set([...bkSel].filter(n => names.has(n)));
    if (bkZip && !shelf.archives.some(a => a.name === bkZip)) bkZip = null;
    paintBackups();
  });

  BUILD.backups = () => {
    panel('backups').innerHTML = `<div class="mnt-two">
      <aside class="mnt-rail2">
        <div>
          <div class="mnt-rail-head">${t('mn.bk.fresh')} <b id="bkTotal"></b></div>
          <div id="bkShelves"></div>
        </div>
        <div>
          <div class="mnt-rail-head is-zip">${t('mn.bk.archived')} <b id="bkZipTotal"></b></div>
          <div id="bkArchives"></div>
        </div>
        <div class="mnt-disk">
          <div class="mnt-disk-label">${t('mn.bk.onDisk')}</div>
          <div class="mnt-disk-value" id="bkDiskAll">—</div>
          <div class="mnt-disk-split" id="bkDiskSplit"></div>
          <div id="bkBar"></div>
          <button class="btn btn-sm" id="bkStorage">${t('mn.bk.storageDetail')}</button>
        </div>
      </aside>
      <section class="panel mnt-flush">
        <div class="mnt-shelf-head">
          <span class="mnt-shelf-title" id="bkTitle">—</span>
          <span class="mnt-shelf-meta" id="bkMeta"></span>
          <div class="mnt-shelf-acts" id="bkActs"></div>
        </div>
        <div class="mnt-shelf-body" id="bkBody"><div class="loading"><span class="spin"></span></div></div>
        <div class="mnt-sel" id="bkSelBar"></div>
      </section>
    </div>`;
    $('#bkStorage').addEventListener('click', () => show('storage'));
    loadShelf();
  };

  /* Every control on this tab redraws from the server rather than adjusting
     the lists in place: archiving moves files between two of them, and the
     sizes on the rail are the point of having done it. */
  const afterShelfWrite = async (msg) => {
    toast(msg, 'ok');
    bkSel.clear();
    await loadShelf();
    loadHealth().catch(() => {});
  };

  function paintBackups() {
    if (!shelf || !$('#bkBody')) return;
    const files = shelf.shelf.map(b => ({ ...b, kind: backupKind(b.name, shelf.project) }));
    const loose = files.reduce((n, f) => n + f.size, 0);
    const zipped = shelf.archives.reduce((n, a) => n + a.size, 0);
    const store = health ? health.file.size : 0;
    const archive = bkZip ? shelf.archives.find(a => a.name === bkZip) : null;

    $('#bkTotal').textContent = fmtBytes(loose);
    $('#bkShelves').innerHTML = `<button type="button" class="mnt-shelf" data-shelf=""
      aria-current="${!archive}">${icon('folder')}<span class="mnt-shelf-name">${esc(shelf.project)}</span>
      <span class="mnt-shelf-count">${fmtInt(files.length)}</span></button>`;

    $('#bkZipTotal').textContent = fmtBytes(zipped);
    $('#bkArchives').innerHTML = shelf.archives.length
      ? shelf.archives.map(a => `<button type="button" class="mnt-shelf is-zip" data-shelf="${esc(a.name)}"
          aria-current="${archive === a}" title="${esc(a.name)}">${icon('archive')}
          <span class="mnt-shelf-name">${esc(archiveLabel(a.name, shelf.project))}</span>
          <span class="mnt-shelf-count">${fmtInt(a.count)}</span></button>`).join('')
      : `<p class="mnt-rail-empty">${t('mn.bk.noZips')}</p>`;

    $('#bkDiskAll').textContent = fmtBytes(store + loose + zipped);
    $('#bkDiskSplit').textContent = t('mn.bk.diskSplit', {
      store: fmtBytes(store), loose: fmtBytes(loose), zip: fmtBytes(zipped) });
    $('#bkBar').innerHTML = barHTML([
      { value: store, fill: 'var(--accent)' },
      { value: loose, fill: 'rgba(187, 134, 252, .42)' },
      { value: zipped, fill: 'var(--zip)' },
    ]);

    paintShelfHead(archive, files, loose);
    paintShelfBody(archive, files);
    paintSelBar(archive, files);

    view.querySelectorAll('[data-shelf]').forEach(b => b.addEventListener('click', () => {
      bkZip = b.dataset.shelf || null;
      bkSel.clear();
      paintBackups();
    }));
  }

  function paintShelfHead(archive, files, loose) {
    $('#bkTitle').textContent = archive
      ? archiveLabel(archive.name, shelf.project) : shelf.project;
    $('#bkMeta').textContent = archive
      ? t('mn.bk.zipMeta', { n: fmtInt(archive.count), size: fmtBytes(archive.size),
                             raw: fmtBytes(archive.raw) })
      : t('mn.bk.shelfMeta', { n: fmtInt(files.length), size: fmtBytes(loose) });
    $('#bkActs').innerHTML = archive
      ? `<button class="btn btn-sm" id="bkUnzip">${t('mn.bk.unzip')}</button>
         <button class="btn btn-sm btn-danger" id="bkDropZip">${t('mn.bk.deleteZip')}</button>`
      : `<span class="inline-label">${t('mn.bk.groupBy')}
           <span class="seg" id="bkGroupSeg" role="group" aria-label="${t('mn.bk.groupBy')}">
             <button type="button" data-g="date" aria-pressed="${bkGroup === 'date'}">${t('mn.bk.byDate')}</button>
             <button type="button" data-g="reason" aria-pressed="${bkGroup === 'reason'}">${t('mn.bk.byReason')}</button>
           </span></span>
         <button class="btn btn-solid btn-sm" data-op="backup">${t('mn.op.backup')}</button>`;

    if (archive) {
      $('#bkUnzip').addEventListener('click', async () => {
        if (!(await confirmModal({ title: t('mn.bk.unzip'),
          body: t('mn.confirm.unzip', { n: archive.count, name: archive.name }),
          okLabel: t('mn.bk.unzip') }))) return;
        try {
          const r = await api('/api/maintenance/unarchive', { body: { name: archive.name } });
          bkZip = null;
          await afterShelfWrite(t('mn.msg.unzipped', { n: fmtInt(r.restored.length) }));
        } catch (err) { failed('err.maintenance', err); }
      });
      $('#bkDropZip').addEventListener('click', async () => {
        if (!(await confirmModal({ title: t('mn.bk.deleteZip'),
          body: t('mn.confirm.deleteZip', { n: archive.count, name: archive.name }),
          okLabel: t('mn.bk.deleteZip') }))) return;
        try {
          const r = await api('/api/maintenance/archive-delete', { body: { name: archive.name } });
          bkZip = null;
          await afterShelfWrite(t('mn.msg.zipDeleted', { n: fmtInt(r.count), name: archive.name }));
        } catch (err) { failed('err.maintenance', err); }
      });
      return;
    }
    wireOps($('#bkActs'));
    $('#bkGroupSeg').addEventListener('click', e => {
      const b = e.target.closest('[data-g]');
      if (!b || b.dataset.g === bkGroup) return;
      bkGroup = b.dataset.g;
      paintBackups();
    });
  }

  function paintShelfBody(archive, files) {
    const body = $('#bkBody');
    body.classList.toggle('pickable', !archive);
    if (archive) {
      body.innerHTML = archive.count
        ? fileRowsHTML([{ name: t('mn.bk.inside'), rows: archive.members }], false)
        : `<div class="empty">${t('mn.bk.emptyZip')}</div>`;
      return;
    }
    if (!files.length) {
      body.innerHTML = `<div class="empty">${t('mn.backups.empty')}</div>`;
      return;
    }
    /* by date the buckets come out in the order the files already are --
       newest first; by reason they are collected as they are first met */
    const groups = new Map();
    for (const f of files) {
      const key = bkGroup === 'date' ? dateBucket(f.mtime) : reasonLabel(f.kind);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(f);
    }
    const all = files.length && files.every(f => bkSel.has(f.name));
    body.innerHTML = `<div class="mnt-file mnt-file-head">
        <span><input type="checkbox" id="bkAll" ${all ? 'checked' : ''}
          aria-label="${esc(t('mn.bk.selectAll'))}" title="${esc(t('mn.bk.selectAll'))}"></span>
        <span></span><span>${t('mn.bk.th.backup')}</span>
        <span>${t('mn.bk.th.taken')}</span><span class="num">${t('mn.bk.th.size')}</span>
      </div>`
      + fileRowsHTML([...groups].map(([name, rows]) => ({ name, rows })), true);

    /* A tick redraws the row it is in and the bar that counts it, never the
       shelf: rebuilding the list under a checkbox takes the focus off it,
       and ticking a run of files with the keyboard then stops after one. */
    const afterTick = () => {
      $('#bkAll').checked = files.length > 0 && files.every(f => bkSel.has(f.name));
      body.querySelectorAll('[data-pick]').forEach(el =>
        el.closest('.mnt-file').classList.toggle('is-picked', bkSel.has(el.dataset.pick)));
      paintSelBar(null, files);
    };
    $('#bkAll').addEventListener('change', e => {
      if (e.target.checked) files.forEach(f => bkSel.add(f.name));
      else bkSel.clear();
      body.querySelectorAll('[data-pick]').forEach(el => { el.checked = bkSel.has(el.dataset.pick); });
      afterTick();
    });
    body.querySelectorAll('[data-pick]').forEach(box => box.addEventListener('change', () => {
      if (box.checked) bkSel.add(box.dataset.pick); else bkSel.delete(box.dataset.pick);
      afterTick();
    }));
  }

  /* One writer for both shelves. A zip's members carry no reason and cannot
     be ticked, so `pick` is what the two arrangements differ by. */
  function fileRowsHTML(groups, pick) {
    return groups.map(({ name, rows }) => `
      <div class="mnt-group">
        <span class="mnt-group-name">${esc(name)}</span>
        <span class="mnt-group-rule"></span>
        <span class="mnt-group-meta">${t('mn.bk.shelfMeta', {
          n: fmtInt(rows.length), size: fmtBytes(rows.reduce((n, r) => n + r.size, 0)) })}</span>
      </div>` + rows.map(f => `
      <div class="mnt-file${pick && bkSel.has(f.name) ? ' is-picked' : ''}">
        ${pick ? `<span><input type="checkbox" data-pick="${esc(f.name)}"
          ${bkSel.has(f.name) ? 'checked' : ''}
          aria-label="${esc(f.name)}"></span>` : ''}
        ${icon('db-file')}
        <div class="mnt-file-main">
          <!-- Grouped by reason, the heading already says what the backup was
               taken for, and repeating it on every row under it says nothing.
               The filename moves up and the row loses its second line. -->
          ${!pick || bkGroup === 'reason' ? `
          <div class="mnt-file-label mnt-file-mono" title="${esc(f.name)}">${esc(f.name)}</div>` : `
          <div class="mnt-file-label">${esc(reasonLabel(f.kind))}</div>
          <div class="mnt-file-sub"><span class="mnt-file-name" title="${esc(f.name)}">${esc(f.name)}</span></div>`}
        </div>
        <span class="mnt-file-when" title="${esc(fmtDate(f.mtime))}">${esc(fmtAgo(f.mtime))}</span>
        <span class="mnt-file-size">${fmtBytes(f.size)}</span>
      </div>`).join('')).join('');
  }

  function paintSelBar(archive, files) {
    const bar = $('#bkSelBar');
    if (archive) {
      bar.innerHTML = `<span class="mnt-sel-text">${t('mn.bk.zipReadOnly')}</span>`;
      return;
    }
    const picked = files.filter(f => bkSel.has(f.name));
    const size = picked.reduce((n, f) => n + f.size, 0);
    bar.innerHTML = `<span class="mnt-sel-text${picked.length ? ' is-on' : ''}">${picked.length
        ? t('mn.bk.selN', { n: fmtInt(picked.length), size: fmtBytes(size) })
        : t('mn.bk.selNone')}</span>
      <button class="btn btn-sm mnt-sel-zip" id="bkArchive" ${picked.length ? '' : 'disabled'}>
        ${icon('archive')}${t('mn.bk.archiveSel')}</button>`;
    $('#bkArchive').addEventListener('click', async () => {
      const names = picked.map(f => f.name);
      if (!(await confirmModal({ title: t('mn.bk.archiveSel'),
        body: t('mn.confirm.archive', { n: names.length, size: fmtBytes(size) }),
        okLabel: t('mn.bk.archiveSel') }))) return;
      try {
        const r = await api('/api/maintenance/archive', { body: { names } });
        await afterShelfWrite(t('mn.msg.archived', {
          n: fmtInt(r.added), name: r.archive,
          raw: fmtBytes(r.raw), size: fmtBytes(r.size) }));
      } catch (err) { failed('err.maintenance', err); }
    });
  }

  /* `General-2026-09.zip` is read as the month it holds: the project is the
     shelf it sits under and the extension is how it is stored. */
  const archiveLabel = (name, project) =>
    name.replace(/\.zip$/, '').replace(new RegExp(`^${project}-`), '');

  /* ── storage ───────────────────────────────────────────────────────── */

  BUILD.storage = () => {
    panel('storage').innerHTML = `<div class="mnt-stack">
      <section class="panel">
        <h3 class="panel-title">${t('mn.st.file')}
          <span class="panel-aside" id="stPath" title=""></span></h3>
        <div id="stFile"><div class="loading"><span class="spin"></span></div></div>
      </section>

      <section class="panel">
        <h3 class="panel-title">${t('mn.st.compact')}
          <span class="panel-aside">${t('mn.st.compactAside')}</span></h3>
        <div id="stSteps"></div>
      </section>

      <!-- The two structures beside the memories that can fall out of step
           with them, and the operation that puts each one back. Health
           counts these defects and says whether there are any; the line
           here is the operation naming what it would act on, because a
           repair with no number beside it is a button you cannot decide
           about. -->
      <section class="panel">
        <h3 class="panel-title">${t('mn.fix.title')}</h3>
        <div id="stFix"></div>
      </section>

      <!-- Renders are the one thing here that accumulates on disk without
           anyone asking, so the setting sits next to what it affects
           rather than in a settings page nobody opens. -->
      <section class="panel">
        <h3 class="panel-title">${t('mn.rn.title')}</h3>
        <p class="intro">${t('mn.rn.aside')}</p>
        <div class="list-toolbar toolbar-sm">
          <label class="inline-label">${t('mn.rn.keep')}
            ${pickerFor({ id: 'rnKeep', items: keepItems, ariaLabel: t('mn.rn.keep') })}</label>
          <button class="btn btn-sm" data-op="prune-renders">${t('mn.rn.now')}</button>
          <button class="btn btn-sm" data-op="prune-renders-all">${t('mn.rn.all')}</button>
        </div>
        <div id="rnBody" class="hint">—</div>
      </section>
    </div>`;
    wireOps(panel('storage'));
    wirePicker(view, { id: 'rnKeep', items: fixedItems(keepItems), onPick: async mode => {
      try {
        await api('/api/config', { body: { svg_retention: mode } });
        toast(t('mn.msg.retention', { mode: t('mn.rn.mode.' + mode) }), 'ok');
      } catch (err) { failed('err.maintenance', err); }
    } });
    if (health) paintStorage();
  };

  function paintStorage() {
    if (!health || !$('#stFile')) return;
    const h = health;
    const used = Math.max(0, h.file.size - h.file.reclaimable);
    const parts = [
      { name: t('mn.st.inUse'), value: used, fill: 'var(--accent)' },
      { name: t('mn.st.free'), value: h.file.reclaimable, fill: 'var(--warn)' },
    ];
    if (h.file.wal_size) parts.push({ name: t('mn.st.wal'), value: h.file.wal_size, fill: 'rgba(187, 134, 252, .42)' });
    const why = h.file.reclaimable > RECLAIM_WARN && h.file.compact_reason === 'vector_store'
      ? `<div class="mnt-note">${t('mn.h.diskFromVectors')}</div>` : '';
    /* the folder and the filename identify the store; the rest of an
       absolute path is the machine, and it pushes the heading off its row */
    const seg2 = h.file.path.split(/[\\/]/).slice(-2).join('/');
    $('#stPath').textContent = seg2;
    $('#stPath').title = h.file.path;
    $('#stFile').innerHTML = barHTML(parts) + legendHTML(parts) + why;

    /* Compacting discards the free pages an undo would have needed, so the
       backup is not advice sitting beside the button -- it is the step
       before it, and the button stays shut until one exists. */
    const backed = h.backups.length > 0;
    const newest = backed ? h.backups[0] : null;
    $('#stSteps').innerHTML = `
      <div class="mnt-step ${backed ? 'done' : 'now'}">
        <span class="mnt-step-n">${backed ? '&#10003;' : '1'}</span>
        <div class="mnt-step-body">
          <div class="mnt-step-name">${t('mn.st.step.backup')}</div>
          <div class="mnt-step-note">${backed
            ? t('mn.st.step.backupDone', { when: esc(fmtAgo(newest.mtime)), size: fmtBytes(newest.size) })
            : t('mn.st.step.backupNone')}</div>
        </div>
        <button class="btn btn-sm" data-op="backup">${t('mn.op.backup')}</button>
      </div>
      <div class="mnt-step ${backed ? 'now' : 'locked'}">
        <span class="mnt-step-n">2</span>
        <div class="mnt-step-body">
          <div class="mnt-step-name">${t('mn.st.step.compact')}</div>
          <div class="mnt-step-note">${backed
            ? t('mn.st.step.compactNote', { a: fmtBytes(h.file.size), b: fmtBytes(used) })
            : t('mn.st.step.locked')}</div>
        </div>
        <button class="btn btn-sm btn-danger" data-op="vacuum" ${backed ? '' : 'disabled'}>${t('mn.op.vacuum')}</button>
      </div>`;
    wireOps($('#stSteps'));

    /* The rebuild is offered whether or not the index looks consistent: it
       writes the index again from the memories and destroys nothing, so it
       is also the answer to a suspicion a count comparison cannot settle.
       Cleaning references DELETES rows, so it is offered only once there
       is something to delete -- a destructive control over an empty set is
       a trap. */
    $('#stFix').innerHTML = `
      <div class="mnt-fix">
        <span class="mnt-fix-name">${t('mn.h.fts')}</span>
        <span class="mnt-fix-state">${h.fts.detail ? esc(h.fts.detail) : t('mn.h.ftsConsistent')}
          · ${t('mn.h.rows', { a: fmtInt(h.fts.rows), b: fmtInt(h.fts.expected) })}</span>
        <button class="btn btn-sm" data-op="fts">${t('mn.op.fts')}</button>
      </div>
      <div class="mnt-fix">
        <span class="mnt-fix-name">${t('mn.fix.refs')}</span>
        <span class="mnt-fix-state">${h.relations.orphans === 0
          ? t('mn.h.noOrphans') : t('mn.h.orphanEdges', { n: h.relations.orphans })}</span>
        ${h.relations.orphans === 0 ? ''
          : `<button class="btn btn-sm btn-danger" data-op="orphans">${t('mn.op.orphans')}</button>`}
      </div>`;
    wireOps($('#stFix'));

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
  }

  /* ── sections ──────────────────────────────────────────────────────── */

  const loadSections = retryable('#scBody', async () => {
    const s = await api('/api/maintenance/sections-queue');
    const body = $('#scBody');
    if (!body) return;
    badge('sections', s.queue.length);
    /* three states, and they are not the same: a store nobody has read, one
       that came out clean, and one holding bodies a human has to settle */
    const head = !s.migrated ? t('mn.sc.notRead')
      : s.queue.length ? t('mn.sc.pending', { n: fmtInt(s.queue.length) })
      : t('mn.sc.clean');
    /* A queue with nothing in it is not a heading over an empty screen: the
       three states above are the whole content, so they are the empty box. */
    if (!s.queue.length) {
      body.innerHTML = `<div class="empty">${head}</div>`;
      return;
    }
    body.innerHTML = `<p class="intro">${head}</p>`
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

  BUILD.sections = () => {
    panel('sections').innerHTML = `<section class="panel">
      <h3 class="panel-title">${t('mn.sc.head')}
        <button class="btn btn-sm" data-op="sectionize">${t('mn.sc.run')}</button></h3>
      <p class="intro">${t('mn.sc.intro')}</p>
      <div class="panel-body" id="scBody"><div class="loading"><span class="spin"></span></div></div>
    </section>`;
    wireOps(panel('sections'));
    loadSections();
  };

  /* ── duplicates ────────────────────────────────────────────────────── */

  BUILD.dupes = () => {
    panel('dupes').innerHTML = `<section class="panel">
      <h3 class="panel-title">${t('mn.dd.head')}</h3>
      <p class="intro">${t('mn.dd.aside')}</p>
      <div class="list-toolbar toolbar-sm">
        <label class="inline-label">
          ${t('mn.dd.threshold')} <input type="range" id="ddThr" min="0.45" max="0.95" step="0.05" value="0.60">
          <b id="ddThrVal">0.60</b></label>
        ${pickerFor({ id: 'ddType', items: types, ariaLabel: t('common.allTypes') })}
        <input type="text" id="ddDomain" placeholder="${t('mn.dd.domainPh')}"
               aria-label="${t('mn.dd.domainPh')}" list="ddDomainsDL" style="max-width:200px">
        <datalist id="ddDomainsDL"></datalist>
        <button class="btn btn-solid btn-sm" id="ddRun">${t('mn.dd.run')}</button>
      </div>
      <div class="panel-body" id="ddBody"><div class="empty">${t('mn.dd.hint')}</div></div>
    </section>`;

    getDomains().then(ds => {
      const dl = $('#ddDomainsDL');
      /* the view may have been swapped while this was in flight */
      if (dl) dl.innerHTML = domainDatalist(ds);
    }).catch(() => {});

    wirePicker(view, { id: 'ddType', items: fixedItems(types), onPick: () => {} });
    $('#ddThr').addEventListener('input', e => {
      $('#ddThrVal').textContent = Number(e.target.value).toFixed(2);
    });
    $('#ddRun').addEventListener('click', runDedup);
  };

  async function runDedup() {
    const body = $('#ddBody');
    body.innerHTML = '<div class="loading"><span class="spin"></span></div>';
    try {
      const qs = new URLSearchParams({ threshold: $('#ddThr').value });
      if (pickerValue(view, 'ddType')) qs.set('type', pickerValue(view, 'ddType'));
      if ($('#ddDomain').value.trim()) qs.set('domain', $('#ddDomain').value.trim());
      const r = await api(`/api/maintenance/dedup?${qs}`);
      badge('dupes', r.pairs.length);
      if (!r.pairs.length) {
        body.innerHTML = `<div class="empty">${t('mn.dd.none')}</div>`;
        return;
      }
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
      body.querySelector('[data-retry]').addEventListener('click', runDedup);
    }
  }

  /* ── the change log ────────────────────────────────────────────────── */

  const loadLog = retryable('#logBody', async () => {
    const r = await api('/api/audit?limit=200');
    const host = $('#logBody');
    if (!host) return;
    if (!r.entries.length) {
      host.innerHTML = `<div class="empty">${t('mn.au.empty')}</div>`;
      return;
    }
    /* One day per heading, in the order the server sent -- newest first,
       and an entry's own day comes from the reader's clock, not from the
       first ten characters of a UTC stamp. */
    const days = new Map();
    for (const e of r.entries) {
      const d = new Date(e.edited_at);
      const key = isNaN(d) ? String(e.edited_at).slice(0, 10)
        : `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
      if (!days.has(key)) days.set(key, []);
      days.get(key).push(e);
    }
    const today = new Date();
    const todayKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
    const pad2 = n => String(n).padStart(2, '0');
    host.innerHTML = [...days].map(([key, rows]) => `
      <div class="mnt-day">
        <span class="mnt-day-name">${key === todayKey ? t('mn.log.today') : esc(fmtDayKey(key))}</span>
        <span class="mnt-group-rule"></span>
        <span class="mnt-group-meta">${t('mn.log.count', { n: fmtInt(rows.length) })}</span>
      </div>` + rows.map(e => {
        const at = new Date(e.edited_at);
        const time = isNaN(at) ? '' : `${pad2(at.getHours())}:${pad2(at.getMinutes())}`;
        const delta = e.content_changed
          ? `${fmtInt(e.prev_len)} &rarr; ${fmtInt(e.new_len)}` : '';
        const note = e.note || '';
        return `<button type="button" class="mnt-ev" data-uid="${esc(e.memory_uid)}"
            title="${esc(note)}" aria-label="${esc(t('a11y.openRecord', { uid: e.memory_uid }))}">
          <span class="mnt-ev-time">${time}</span>
          <span class="dot ${typeClass(e.type)}"></span>
          <span class="mnt-ev-main">
            <span class="mnt-ev-title">${esc(note) || t('mn.au.contentEdit')}</span>
            <span class="mnt-ev-detail">
              <span class="mnt-ev-uid">${esc(e.memory_uid)}</span>
              <span class="mnt-ev-domain">${esc(e.domain || '—')}</span>
            </span>
          </span>
          <span class="mnt-ev-delta">${delta}</span>
        </button>`;
      }).join('')).join('');
    host.querySelectorAll('[data-uid]').forEach(
      b => b.addEventListener('click', () => openRecord(b.dataset.uid)));
  });

  /* The day heading, from a YYYY-MM-DD key. dom.js's fmtDay drops the year,
     which a log going back months cannot afford. */
  function fmtDayKey(key) {
    const [y, m, d] = String(key).split('-');
    const month = I18N.months[Number(m) - 1];
    if (!month) return key;
    const thisYear = String(new Date().getFullYear());
    return `${d} ${month}${y === thisYear ? '' : ` ${y}`}`;
  }

  BUILD.log = () => {
    panel('log').innerHTML = `<section class="panel">
      <h3 class="panel-title">${t('mn.log.head')}
        <button class="btn btn-sm" id="logRefresh">${t('common.refresh')}</button></h3>
      <p class="intro">${t('mn.au.aside')}</p>
      <div class="mnt-log" id="logBody"><div class="loading"><span class="spin"></span></div></div>
    </section>`;
    $('#logRefresh').addEventListener('click', loadLog);
    loadLog();
  };

  /* ── the warden ────────────────────────────────────────────────────── */

  BUILD.warden = () => {
    panel('warden').innerHTML = `<section class="panel mnt-short">
      <h3 class="panel-title">${t('mn.wd.title')}
        <span class="panel-aside">${t('mn.wd.aside')}</span></h3>
      <div class="list-toolbar toolbar-sm">
        <label class="inline-label">${t('mn.wd.state')}
          ${pickerFor({ id: 'wdOn', items: onOffItems, ariaLabel: t('mn.wd.state') })}</label>
        <label class="inline-label">${t('mn.wd.every')}
          ${pickerFor({ id: 'wdEvery', items: everyItems, ariaLabel: t('mn.wd.every') })}</label>
      </div>
      <p class="hint">${t('mn.wd.body')}</p>
    </section>`;

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
  };

  /* The tab that was asked for is the only one built at mount; the rest
     build when they are first opened. The sections count is the exception
     -- it is a badge on a closed tab, so its queue is asked for either
     way, and the answer is the same one the tab would have used. */
  built.add(opened);
  BUILD[opened]();
  loadHealth();
  if (opened !== 'sections') {
    api('/api/maintenance/sections-queue')
      .then(s => badge('sections', s.queue.length)).catch(() => {});
  }
}
