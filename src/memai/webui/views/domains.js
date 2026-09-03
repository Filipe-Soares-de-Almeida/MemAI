/* Domains: the buckets memories are filed under -- a TREE of them, since a
   domain is a path ('acme/x100/p200') and a subject contains subjects.

   Read left to right. Each column lists the levels inside the one picked in
   the column before it, and the pane on the right is the level that is
   picked: what it holds, what merely belongs to it, and the operations that
   act on the whole bucket.

   It was one flat table with a twist per row and an indent rail. That draws
   the shape of the tree honestly and it makes WALKING it a scroll -- a
   branch nine levels down opened nine rows apart, and the row you were
   comparing it against had scrolled off. Columns give every level the same
   place on screen whatever its depth.

   Re-nesting is a drag by the handle. Nothing is written on the drop: the
   move joins a queue in the footer, the row it came from says where it is
   going, and Apply is what runs it. A re-home reindexes every memory in the
   subtree, so it is not something a slip of the mouse should commit. */

import { esc, fmtInt, fmtAgo } from '../core/dom.js';
import { api } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, failed, openModal, closeModal, confirmModal, promptModal,
         openCtxMenu, setPressed } from '../core/ui.js';
import { typeTag, getDomains, invalidateDomains, byDomainPath, domainLeaf,
         domainDatalist, domainSegments, inDomainPath, DOMAIN_SEP } from '../core/shared.js';
import { pickerFor, pickerValue, wirePicker, fixedItems } from '../core/pick.js';
import { domainPickerHTML, wireDomainPicker } from '../core/domain-picker.js';
import { moveToProjectModal } from '../core/projects.js';
import { go, refreshBehind } from '../core/router.js';
import { openRecord } from './record.js';
import { t } from '../i18n.js';

const CASE_MODES = ['preserve', 'lower', 'upper'];

/* Whether the archived branches are drawn at all. Off by default: a domain
   whose memories were all archived used to sit in the list looking exactly
   as live as the rest, which is the complaint this answers. Nothing vanishes
   silently -- the count is on the toggle. Module-level because an action
   re-renders the view behind its own modal, and the reader's choice has to
   survive that. */
let showArchived = false;

/* Moves that have been dropped but not written: [{ from, to, memories }].
   Module-level for the same reason -- and because Apply runs them in the
   order they were made, which a re-render must not shuffle. */
let queue = [];

/* A branch nothing is filed under BUT its own archived memories. Both other
   readings stay live: one active memory anywhere below it makes the level
   active, and so does a cross-listing INTO it -- a subject other branches
   still point at is not a dead branch, whatever is filed under it.
   Derived, not stored: a domain exists because memories name it, so its
   status is the status of what names it. */
const isArchived = d =>
  Boolean(d.subtree_archived) && !d.subtree_active && !d.subtree_also;

/* A purely cross-cutting subject: nothing is filed under it, and memories
   living in other branches point at it. */
const isCrossing = d => Boolean(d.subtree_also) && !d.subtree_active && !d.subtree_archived;

const pathOf = (parent, leaf) => (parent ? `${parent}${DOMAIN_SEP}${leaf}` : leaf);

export async function renderDomains(view, params, ctx) {
  const domains = await getDomains(true);
  const cfg = await api('/api/config').catch(() => ({ domain_case: 'preserve' }));
  if (ctx.stale()) return;

  const byPath = new Map(domains.map(d => [d.domain, d]));
  /* a path in the URL that no longer exists lands you at the roots rather
     than on a column of nothing -- a rename or a delete can happen between
     the link being made and being followed */
  const path = byPath.has(params.get('path') || '') ? params.get('path') : '';
  const here = byPath.get(path) || null;
  /* a queue is about the tree as it stands; navigating away from the view
     drops it, and so does a tree that no longer holds what it names */
  queue = queue.filter(m => byPath.has(m.from));

  const named = domains.filter(d => !d.implicit).length;
  const roots = domains.filter(d => !d.parent).length;
  const archived = domains.filter(isArchived).length;

  view.innerHTML = `<div class="dom-shell">
    <div class="dom-bar">
      <h2 class="sr-only">${t('do.title')}</h2>
      ${crumbHTML(path)}
      <span class="dom-bar-sub">${t('do.sub.count', { n: fmtInt(named) })} · ${
        t('do.sub.roots', { n: fmtInt(roots) })}</span>
      <span class="dom-bar-end">
        <!-- 293 domains is not a tree anybody walks to find one. The picker
             filters as you type and draws the same rails the columns do;
             picking a row is a navigation to that level. -->
        ${domainPickerHTML({ id: 'domFind', value: '', cls: 'dom-find',
                             anyLabel: t('do.find'), ariaLabel: t('do.find') })}
        ${archived ? `<button class="btn btn-sm" id="domArchived"></button>` : ''}
        <button type="button" class="icon-btn" id="domMore" title="${t('do.storeMenu')}"
                aria-label="${t('do.storeMenu')}">${icon('maintenance')}</button>
      </span>
    </div>

    <div class="dom-cols" id="domCols">
      <!-- the columns scroll sideways rather than sharing the width with the
           pane: a path nine levels deep must not shrink the pane to nothing,
           and the pane is where the level is actually read -->
      <div class="dom-strip" id="domStrip">${columnsHTML(domains, path)}</div>
      <div class="dom-detail" id="domDetail">${here ? '' : detailEmptyHTML()}</div>
    </div>

    <div class="dom-queue" id="domQueue"${queue.length ? '' : ' hidden'}></div>
  </div>`;

  drawQueue();
  wireColumns(view, domains, path);
  wireDomainPicker(view, {
    id: 'domFind', domains, anyLabel: t('do.find'),
    onPick: to => go('domains', to ? { path: to } : {}),
  });
  /* the deepest column is the one being worked in, so it is the one the
     strip is scrolled to -- walking in should not leave you looking at the
     roots you have already passed */
  const strip = view.querySelector('#domStrip');
  strip.scrollLeft = strip.scrollWidth;

  const archBtn = view.querySelector('#domArchived');
  if (archBtn) {
    const label = () => {
      archBtn.textContent = t(showArchived ? 'do.tree.archivedHide' : 'do.tree.archivedShow',
                              { n: fmtInt(archived) });
      setPressed(archBtn, showArchived);
    };
    label();
    archBtn.onclick = () => { showArchived = !showArchived; refreshBehind(); };
  }

  /* Store-wide, not level-wide: the casing policy is a property of the file,
     so it does not belong on a level's own action row. */
  view.querySelector('#domMore').addEventListener('click', e => {
    const r = e.currentTarget.getBoundingClientRect();
    openCtxMenu(r.left, r.bottom + 4, [
      { label: t('do.case.title'), run: () => openCaseModal(cfg) },
      { label: t('do.case.normalize'), run: openNormalizeModal },
    ]);
  });

  if (here) loadDetail(view, here, domains);
}

/* ─── the path, as a bar ──────────────────────────────────────────────── */

function crumbHTML(path) {
  const segs = domainSegments(path);
  if (!segs.length) return `<span class="dom-crumb"><span class="dom-crumb-none">${t('do.crumb.roots')}</span></span>`;
  return `<span class="dom-crumb">${segs.map((seg, i) => {
    const upto = segs.slice(0, i + 1).join(DOMAIN_SEP);
    const last = i === segs.length - 1;
    return `${i ? '<span class="dom-crumb-sep">/</span>' : ''}<button type="button"
      class="dom-crumb-seg${last ? ' on' : ''}" data-goto="${esc(upto)}">${esc(seg)}</button>`;
  }).join('')}</span>`;
}

/* ─── the columns ─────────────────────────────────────────────────────── */

/* One column per level of the path, plus one for the children of the level
   that is picked -- so the column you would walk into next is already open
   and a leaf simply has none. */
function columnsHTML(domains, path) {
  const segs = domainSegments(path);
  const parents = ['', ...segs.map((_, i) => segs.slice(0, i + 1).join(DOMAIN_SEP))];
  return parents.map((parent, i) => {
    const kids = childrenOf(domains, parent);
    if (i && !kids.length) return '';        /* a leaf opens no empty column */
    return columnHTML(domains, parent, kids, segs[i] ? pathOf(parent, segs[i]) : '', i);
  }).join('');
}

const childrenOf = (domains, parent) => domains
  .filter(d => (d.parent || '') === parent)
  .filter(d => showArchived || !isArchived(d))
  .sort(byDomainPath);

function columnHTML(domains, parent, kids, picked, depth) {
  const head = parent
    ? `${esc(domainLeaf(parent))} · ${kids.length}`
    : t('do.col.roots', { n: kids.length });
  return `<div class="dom-col" data-parent="${esc(parent)}" style="--depth:${depth}">
    <div class="dom-col-head">${head}</div>
    <div class="dom-col-body" data-drop-parent="${esc(parent)}">
      ${kids.map(d => levelHTML(d, d.domain === picked)).join('')}
      <div class="dom-col-rest" data-drop-parent="${esc(parent)}"></div>
      ${parent ? '' : `<div class="dom-col-hint">${t('do.drop.hint')}</div>`}
    </div>
  </div>`;
}

function levelHTML(d, on) {
  const queued = queue.find(m => m.from === d.domain);
  const kids = d.children;
  const count = d.subtree_active || d.active;
  return `<div class="dom-level${on ? ' on' : ''}${queued ? ' queued' : ''}"
       draggable="true" data-path="${esc(d.domain)}" tabindex="0"
       role="button" aria-current="${on ? 'true' : 'false'}"
       title="${esc(d.domain)}">
    <span class="dom-grip" aria-hidden="true">${icon('grip')}</span>
    <span class="dom-name${d.implicit ? ' implicit' : ''}">${esc(domainLeaf(d.domain))}</span>
    ${queued ? `<span class="dom-arrow">→ ${esc(queued.to)}</span>` : ''}
    ${isCrossing(d)
      ? `<span class="dom-count crossing" title="${esc(t('do.tree.crossingWhy'))}">${
          t('do.col.alsoN', { n: fmtInt(d.subtree_also) })}</span>`
      : `<span class="dom-count">${fmtInt(count)}</span>`}
    ${isArchived(d) ? `<span class="status-tag archived"
        title="${esc(t('do.tree.archivedWhy'))}">${t('do.tree.archivedTag')}</span>` : ''}
    ${kids ? icon('chevron-right', { cls: 'dom-into' }) : ''}
  </div>`;
}

/* ─── the pane ────────────────────────────────────────────────────────── */

function detailEmptyHTML() {
  return `<div class="dom-detail-empty">
    <div class="mi-empty-title">${t('do.det.pickTitle')}</div>
    <p class="hint">${t('do.det.pickBody')}</p>
  </div>`;
}

async function loadDetail(view, node, domains) {
  const host = view.querySelector('#domDetail');
  if (!host) return;
  let data;
  try { data = await api(`/api/domains/detail?domain=${encodeURIComponent(node.domain)}`); }
  catch (err) { failed('err.load', err); return; }
  if (!host.isConnected) return;
  host.innerHTML = detailHTML(node, data);
  wireDetail(host, node, domains);
}

function detailHTML(d, data) {
  const row = m => `<button type="button" class="dom-mem" data-uid="${esc(m.uid)}">
    ${typeTag(m.type)}
    <span class="dom-mem-title">${esc(m.title || m.content)}</span>
    <span class="dom-mem-age">${fmtAgo(m.created_at)}</span>
  </button>`;
  const more = data.filed_total - data.filed.length;

  return `<div class="dom-detail-head">
    <div class="dom-detail-name">
      <span class="dom-detail-leaf">${esc(domainLeaf(d.domain))}</span>
      <span class="dom-detail-path">${esc(d.domain)}</span>
    </div>
    <div class="dom-detail-facts">
      <span><b>${fmtInt(d.active)}</b> ${t('do.det.here')}</span>
      <span><b>${fmtInt(d.subtree_active - d.active)}</b> ${t('do.det.below')}</span>
      <span><b>${fmtInt(d.archived)}</b> ${t('do.det.archived')}</span>
      <span class="crossing"><b>${fmtInt(d.also)}</b> ${t('do.det.alsoHere')}</span>
      <span>${t('do.det.last', { when: fmtAgo(d.latest_at || d.subtree_latest_at) })}</span>
    </div>
    <div class="act-row">
      <button class="btn btn-solid btn-sm" data-open>${t('do.det.openMemories')}</button>
      <button class="btn btn-sm" data-move>${t('do.rn.move')}</button>
      ${d.subtree_active
        ? `<button class="btn btn-sm" data-arch>${t('do.act.archive')}</button>`
        : d.subtree_archived
          ? `<button class="btn btn-sm" data-rest>${t('do.act.restore')}</button>` : ''}
      <button class="btn btn-sm" data-more>${t('do.det.more')}</button>
    </div>
  </div>
  <div class="dom-detail-body">
    <div class="mg-label">${t('do.det.storedHere')}</div>
    <div class="dom-mems">
      ${data.filed.map(row).join('') || `<div class="empty">${t('do.det.nothingFiled')}</div>`}
      ${more > 0 ? `<button type="button" class="dom-mem-more" data-open>${
        t('do.det.andMore', { n: fmtInt(more) })}</button>` : ''}
    </div>
    ${data.crossing.length ? `
    <div class="dom-crossing">
      <div class="mg-label">${t('do.det.crossingTitle')}</div>
      ${data.crossing.map(m => `<button type="button" class="dom-cross" data-uid="${esc(m.uid)}">
        <span class="dom-cross-home">${esc(m.domain)}</span>
        <span class="dom-mem-title">${esc(m.title || m.content)}</span>
      </button>`).join('')}
    </div>` : ''}
  </div>`;
}

function wireDetail(host, d, domains) {
  /* status='' so the memories view shows the subtree whole -- a domain
     filter there covers descendants, which is the point of picking one */
  host.querySelectorAll('[data-open]').forEach(b => b.addEventListener('click',
    () => go('memories', { domain: d.domain, status: '' })));
  host.querySelector('[data-move]').addEventListener('click',
    () => openRenameModal(d.domain, domains));
  host.querySelector('[data-arch]')?.addEventListener('click', () => archiveDomain(d));
  host.querySelector('[data-rest]')?.addEventListener('click', () => restoreDomain(d));
  host.querySelectorAll('[data-uid]').forEach(b => b.addEventListener('click',
    () => openRecord(b.dataset.uid)));
  host.querySelector('[data-more]').addEventListener('click', e => {
    const r = e.currentTarget.getBoundingClientRect();
    openCtxMenu(r.left, r.bottom + 4, [
      { label: t('do.act.toProject'), run: async () => {
        if (!await moveToProjectModal({ domain: d.domain })) return;
        invalidateDomains();
        refreshBehind();
      } },
      { sep: true },
      { label: t('do.act.delete'), danger: true, run: () => openDeleteModal(d, domains) },
    ]);
  });
}

/* ─── picking, and dragging ───────────────────────────────────────────── */

function wireColumns(view, domains, path) {
  const cols = view.querySelector('#domCols');
  const byPath = new Map(domains.map(d => [d.domain, d]));

  view.querySelectorAll('[data-goto]').forEach(b =>
    b.addEventListener('click', () => go('domains', { path: b.dataset.goto })));

  cols.querySelectorAll('.dom-level').forEach(el => {
    const open = () => go('domains', { path: el.dataset.path });
    el.addEventListener('click', open);
    el.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
    });
    el.addEventListener('dragstart', e => {
      e.dataTransfer.setData('text/plain', el.dataset.path);
      e.dataTransfer.effectAllowed = 'move';
      cols.classList.add('dragging');
      el.classList.add('is-dragged');
    });
    el.addEventListener('dragend', () => {
      cols.classList.remove('dragging');
      el.classList.remove('is-dragged');
      cols.querySelectorAll('.drop-on').forEach(x => x.classList.remove('drop-on'));
    });
  });

  /* Two kinds of target, and the difference is what the drop MEANS. On a
     level: nest under it. On the empty space of a column: become a sibling
     of that column's rows, which at the roots column is a promotion to the
     top level. */
  const targets = [...cols.querySelectorAll('.dom-level'), ...cols.querySelectorAll('[data-drop-parent]')];
  targets.forEach(el => {
    el.addEventListener('dragover', e => {
      const from = dragSource(cols);
      const to = targetPath(el);
      if (!from || !canMove(from, to)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      el.classList.add('drop-on');
    });
    el.addEventListener('dragleave', () => el.classList.remove('drop-on'));
    el.addEventListener('drop', e => {
      e.preventDefault();
      e.stopPropagation();
      el.classList.remove('drop-on');
      const from = e.dataTransfer.getData('text/plain');
      const to = targetPath(el);
      if (!from || !canMove(from, to)) return;
      enqueue(from, to, byPath);
    });
  });
}

const dragSource = cols => cols.querySelector('.is-dragged')?.dataset.path || '';
const targetPath = el => (el.dataset.path !== undefined
  ? el.dataset.path : el.dataset.dropParent);

/* The two moves the server refuses, said here rather than at a 400: a level
   cannot go inside itself or inside its own subtree, and moving it where it
   already lives is not a move. */
function canMove(from, to) {
  if (to === from || inDomainPath(to, from)) return false;
  return pathOf(to, domainLeaf(from)) !== from;
}

function enqueue(from, to, byPath) {
  const node = byPath.get(from);
  const target = pathOf(to, domainLeaf(from));
  queue = queue.filter(m => m.from !== from);
  queue.push({
    from, to: target,
    /* what the move actually costs: the whole subtree is reindexed, and the
       cross-listings into it are rewritten to follow it */
    memories: (node?.subtree_active || 0) + (node?.subtree_archived || 0)
              + (node?.subtree_also || 0),
  });
  refreshBehind();
}

function drawQueue() {
  const host = document.getElementById('domQueue');
  if (!host) return;
  host.hidden = !queue.length;
  if (!queue.length) return;
  const memories = queue.reduce((a, m) => a + m.memories, 0);
  host.innerHTML = `
    <span class="dom-queue-text">
      <b>${t('do.q.moves', { n: queue.length })}</b> ·
      ${t('do.q.reindexed', { n: fmtInt(memories) })}
    </span>
    <span class="dom-queue-list">${queue.map(m =>
      `<span class="dom-queue-item"><code>${esc(m.from)}</code> → <code>${esc(m.to)}</code></span>`
    ).join('')}</span>
    <span class="dom-queue-end">
      <button class="btn btn-sm" data-discard>${t('do.q.discard')}</button>
      <button class="btn btn-solid btn-sm" data-apply>${t('do.q.apply', { n: queue.length })}</button>
    </span>`;
  host.querySelector('[data-discard]').addEventListener('click', () => {
    queue = [];
    refreshBehind();
  });
  host.querySelector('[data-apply]').addEventListener('click', applyQueue);
}

async function applyQueue() {
  /* In the order they were dropped. Two moves can name the same branch --
     nest A under B, then B under C -- and running them out of order would
     make the second one address a path the first has already retired. */
  const runs = [...queue];
  let affected = 0;
  try {
    for (const m of runs) {
      const r = await api('/api/domains/rename', { body: { from: m.from, to: m.to } });
      affected += r.affected;
    }
  } catch (err) { failed('err.domain', err); }
  queue = [];
  toast(t('do.q.done', { affected: fmtInt(affected) }), 'ok');
  invalidateDomains();
  refreshBehind();
}

/* ─── archive / restore a level ───────────────────────────────────────────
   A domain has no status of its own -- it is named by the memories filed
   under it -- so this is the per-memory archive over a scope, subtree
   included. Cross-listings are left alone: a memory that merely belongs to
   the subject lives in another branch. */

async function archiveDomain(d) {
  const reason = await promptModal({
    title: t('do.arch.title'),
    body: `${t('do.arch.body', { n: fmtInt(d.subtree_active), domain: esc(d.domain) })}${
      d.subtree_also ? ` ${t('do.arch.crossing')}` : ''}`,
    label: t('bulk.reason.label'),
    okLabel: t('common.archive'),
    danger: true,
  });
  if (reason === null) return;
  setDomainStatus(d.domain, 'archived', reason);
}

async function restoreDomain(d) {
  const ok = await confirmModal({
    title: t('do.rest.title'),
    body: t('do.rest.body', { n: fmtInt(d.subtree_archived), domain: esc(d.domain) }),
    okLabel: t('common.restore'),
  });
  if (ok) setDomainStatus(d.domain, 'active', '');
}

async function setDomainStatus(domain, status, reason) {
  try {
    const r = await api('/api/domains/status', { body: { domain, status, reason } });
    /* Nothing to do is a result, not a failure -- and not silence either:
       the button was live because the level had counts, so a zero here means
       the tree was read before somebody else's write. */
    if (!r.affected) { toast(t('do.arch.nothing')); return; }
    /* Undo restores the uids the server actually flipped, never "everything
       archived in the scope" -- that would revive whatever had been archived
       long before, for reasons of its own. Offered only when the server sent
       the list, which it withholds past what /api/bulk would accept back. */
    const undo = status === 'archived' && r.uids.length ? {
      action: {
        label: t('common.undo'),
        run: () => api('/api/bulk', { body: { action: 'restore', uids: r.uids } })
          .then(() => {
            toast(t('do.arch.undone', { n: fmtInt(r.uids.length) }), 'ok');
            invalidateDomains();
            refreshBehind();
          })
          .catch(err => failed('err.bulk', err)),
      },
    } : {};
    toast(t(status === 'archived' ? 'do.arch.done' : 'do.rest.done',
            { n: fmtInt(r.affected) }), 'ok', undo);
    invalidateDomains();
    refreshBehind();
  } catch (err) { failed('err.domain', err); }
}

/* ─── delete a level ──────────────────────────────────────────────────────
   Deleting a domain is deleting every memory filed in it, which is the app's
   one irreversible act N times over -- so it asks for exactly what the
   per-memory purge asks for: the phrase typed out, printed once and never
   pre-filled, with the reversible option named beside it. */

function openDeleteModal(d, domains) {
  const filed = d.subtree_active + d.subtree_archived;
  const levels = domains.filter(x => inDomainPath(x.domain, d.domain)).length;
  const want = `DELETE ${d.domain}`;
  const modal = openModal({
    title: t('do.del.title'),
    bodyHTML: `
      <!-- A purely cross-cutting level has nothing filed under it, so the
           usual warning would open by promising to delete no memories. What
           deleting it actually does is written out instead. -->
      <div class="dz-hint">${filed
        ? t('do.del.hint', { n: fmtInt(filed), domain: esc(d.domain) })
        : t('do.del.hintCrossing', { domain: esc(d.domain) })}</div>
      <div class="hint">${t('do.del.counts', {
        active: fmtInt(d.subtree_active), archived: fmtInt(d.subtree_archived),
        levels: fmtInt(levels) })}</div>
      ${d.subtree_also ? `<div class="hint warn">${t('do.del.crossing', { n: fmtInt(d.subtree_also) })}</div>` : ''}
      <div class="dz-type">${t('dz.typeThis', { phrase: `<code>DELETE ${esc(d.domain)}</code>` })}</div>
      <div class="dz-row">
        <input type="text" id="ddPhrase" aria-label="${t('dz.phrase.aria')}" autocomplete="off">
      </div>
      <div class="dz-state" id="ddState" role="status"></div>`,
    footHTML: `<button class="btn" data-x>${t('common.cancel')}</button>
               <button class="btn btn-danger" data-ok disabled>${t('dz.button')}</button>`,
  });
  const phrase = modal.querySelector('#ddPhrase');
  const state = modal.querySelector('#ddState');
  const okBtn = modal.querySelector('[data-ok]');
  /* A greyed-out button cannot say why it is grey, so the field answers for
     it -- same wording as the record's danger zone, because it is the same
     guardrail. */
  phrase.addEventListener('input', () => {
    const ok = phrase.value === want;
    okBtn.disabled = !ok;
    state.className = `dz-state${ok ? ' armed' : ''}`;
    state.textContent = ok ? t('dz.armed') : phrase.value ? t('dz.mismatch') : '';
  });
  modal.querySelector('[data-x]').onclick = closeModal;
  okBtn.onclick = async () => {
    try {
      const r = await api('/api/domains/delete',
                          { body: { domain: d.domain, confirm: phrase.value } });
      closeModal();
      toast(t('do.del.done', { n: fmtInt(r.purged) })
            + (r.unlinked ? t('do.del.unlinked', { n: fmtInt(r.unlinked) }) : ''), 'ok');
      invalidateDomains();
      /* the level that was showing is gone; the pane goes back to its parent */
      go('domains', { path: d.parent || '' });
      refreshBehind();
    } catch (err) { failed('err.domainPurge', err); }
  };
}

/* ─── store-wide: how a domain is spelled ─────────────────────────────── */

function openCaseModal(cfg) {
  const caseItems = CASE_MODES.map(mode => ({ value: mode, label: t('do.case.mode.' + mode) }));
  const modal = openModal({
    title: t('do.case.title'),
    bodyHTML: `<div class="intro">${t('do.case.desc')}</div>
      ${pickerFor({ id: 'caseMode', value: cfg.domain_case, items: caseItems,
                    ariaLabel: t('do.case.title') })}`,
    footHTML: `<button class="btn" data-x>${t('common.cancel')}</button>
               <button class="btn btn-solid" data-ok>${t('do.case.save')}</button>`,
  });
  /* Picking a casing does not APPLY it -- Save does, and Normalize is what
     touches what is already stored. So the pick only holds a value. */
  wirePicker(modal, { id: 'caseMode', items: fixedItems(caseItems), onPick: () => {} });
  modal.querySelector('[data-x]').onclick = closeModal;
  modal.querySelector('[data-ok]').onclick = async () => {
    try {
      await api('/api/config', { body: { domain_case: pickerValue(modal, 'caseMode') } });
      closeModal();
      toast(t('do.case.saved'), 'ok');
    } catch (err) { failed('err.save', err); }
  };
}

async function openNormalizeModal() {
  let plan;
  try {
    plan = await api('/api/domains/normalize', { body: { dry_run: true } });
  } catch (err) { failed('err.load', err); return; }
  /* Nothing to do is reported the same way whatever the policy is; under
     'preserve' it also says why there was never going to be anything. */
  if (!plan.plan.length) {
    toast(plan.mode === 'preserve' ? t('do.case.preserveHint') : t('do.case.none'),
          plan.mode === 'preserve' ? '' : 'ok');
    return;
  }
  const rows = plan.plan.map(e => `<tr>
    <td>${esc(e.from)}</td>
    <td style="color:var(--ink)">${esc(e.to)}</td>
    <td class="num">${fmtInt(e.count)}</td>
    <td><span style="color:${e.action === 'merge' ? 'var(--warn)' : 'var(--ink-3)'}">${t('do.norm.act.' + e.action)}</span></td>
  </tr>`).join('');
  const modal = openModal({
    title: t('do.norm.title'),
    bodyHTML: `
      <div class="intro">${t('do.norm.intro', { renames: plan.renames, merges: plan.merges, mode: t('do.case.mode.' + plan.mode) })}</div>
      ${plan.merges ? `<div class="intro warn">${t('do.norm.mergeWarn')}</div>` : ''}
      <div class="table-scroll"><table class="table"><thead><tr>
        <th>${t('do.norm.th.from')}</th><th>${t('do.norm.th.to')}</th>
        <th class="num">${t('do.norm.th.count')}</th><th>${t('do.norm.th.action')}</th>
      </tr></thead><tbody>${rows}</tbody></table></div>`,
    footHTML: `<button class="btn" data-x>${t('common.cancel')}</button><button class="btn btn-solid" data-ok>${t('do.norm.apply')}</button>`,
  });
  modal.querySelector('[data-x]').onclick = closeModal;
  modal.querySelector('[data-ok]').onclick = async () => {
    try {
      const r = await api('/api/domains/normalize', { body: { dry_run: false } });
      closeModal();
      toast(t('do.norm.done', { n: r.moved, affected: r.affected }), 'ok');
      invalidateDomains();
      refreshBehind();
    } catch (err) { failed('err.domain', err); }
  };
}

function openRenameModal(from, domains, presetTo = '') {
  const node = domains.find(d => d.domain === from);
  const descendants = node ? node.subtree_active + node.subtree_archived
                             - node.active - node.archived : 0;
  const modal = openModal({
    title: presetTo ? t('do.rn.merge') : t('do.rn.rename'),
    bodyHTML: `
      <div class="field"><label for="rnFrom">${t('do.rn.from')}</label>
        <input type="text" id="rnFrom" value="${esc(from)}" disabled></div>
      <div class="field"><label for="rnTo">${t('do.rn.to')}</label>
        <input type="text" id="rnTo" value="${esc(presetTo)}" list="rnDL" placeholder="${t('do.rn.placeholder')}">
        <datalist id="rnDL">${domainDatalist(domains)}</datalist></div>
      <div id="rnWarn" class="hint warn" hidden>${t('do.rn.warn')}</div>
      <div id="rnCycle" class="hint warn" hidden>${t('do.rn.cycle')}</div>
      ${descendants ? `<div class="hint">${t('do.rn.subtree', { n: fmtInt(descendants) })}</div>` : ''}
      <div class="hint-sm">${t('do.rn.pathHint')}</div>
      <div class="hint-sm">${t('do.rn.hint')}</div>`,
    footHTML: `<button class="btn" data-x>${t('common.cancel')}</button><button class="btn btn-solid" data-ok>${t('common.apply')}</button>`,
  });
  const toInput = modal.querySelector('#rnTo');
  const okBtn = modal.querySelector('[data-ok]');
  /* Moving a domain under itself is the one target the server refuses, so
     say it here rather than letting the modal be submitted into a 400. */
  const check = () => {
    const to = toInput.value.trim().replace(/^\/+|\/+$/g, '');
    const cycle = Boolean(to) && (to === from || to.startsWith(`${from}/`));
    modal.querySelector('#rnCycle').hidden = !cycle;
    modal.querySelector('#rnWarn').hidden =
      cycle || !domains.some(d => d.domain === to && d.domain !== from && !d.implicit);
    okBtn.disabled = cycle;
  };
  toInput.addEventListener('input', check); check();
  modal.querySelector('[data-x]').onclick = closeModal;
  okBtn.onclick = async () => {
    const to = toInput.value.trim();
    try {
      const r = await api('/api/domains/rename', { body: { from, to } });
      closeModal();
      toast(t('do.rn.moved', { n: r.affected }) + (r.merged ? t('do.rn.merged') : ''), 'ok');
      invalidateDomains();
      go('domains', { path: to });
      refreshBehind();
    } catch (err) { failed('err.domain', err); }
  };
}
