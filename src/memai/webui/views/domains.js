/* Domains: the buckets memories are filed under -- a TREE of them, since a
   domain is a path ('acme/x100/p200') and a subject contains subjects.
   Plus the operations that act on a whole bucket: a rename (which nests when
   the target is a path, and merges when it already exists), a case/shape
   normalization pass, archiving a level, and deleting one. */

import { esc, fmtInt, fmtAgo } from '../core/dom.js';
import { api } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, failed, openModal, closeModal, confirmModal, promptModal,
         setPressed } from '../core/ui.js';
import { typeColor, TYPE_ORDER, getDomains, invalidateDomains, byDomainPath, domainLeaf,
         domainDatalist, inDomainPath, domainGuides, domainRailHTML } from '../core/shared.js';
import { pickerFor, pickerValue, wirePicker, fixedItems } from '../core/pick.js';
import { moveToStoreModal } from '../core/stores.js';
import { go, refreshBehind } from '../core/router.js';
import { t } from '../i18n.js';

const CASE_MODES = ['preserve', 'lower', 'upper'];

/* Collapsed branches, by path. Module-level so applying a rename (which
   re-renders behind the modal) does not throw the reader back to the top of
   a tree they had just navigated. Collapsed rather than expanded is what is
   remembered, so a brand-new branch arrives open. */
const collapsed = new Set();

/* Whether the archived branches are drawn at all. Off by default: a domain
   whose memories were all archived used to sit in the list looking exactly
   as live as the rest, which is the complaint this answers. Nothing vanishes
   silently -- the count is in the header and on the toggle. Module-level for
   the same reason `collapsed` is: an action re-renders the view behind its
   own modal, and the reader's choice has to survive that. */
let showArchived = false;

/* A branch nothing is filed under BUT its own archived memories. Both other
   readings stay live: one active memory anywhere below it makes the level
   active, and so does a cross-listing INTO it -- a subject other branches
   still point at is not a dead branch, whatever is filed under it.
   Derived, not stored: a domain exists because memories name it, so its
   status is the status of what names it. */
const isArchived = d =>
  Boolean(d.subtree_archived) && !d.subtree_active && !d.subtree_also;

export async function renderDomains(view, params, ctx) {
  const domains = await getDomains(true);
  const cfg = await api('/api/config').catch(() => ({ domain_case: 'preserve' }));
  if (ctx.stale()) return;

  const caseItems = CASE_MODES.map(mode => ({ value: mode, label: t('do.case.mode.' + mode) }));

  const roots = domains.filter(d => !d.parent).length;
  const named = domains.filter(d => !d.implicit).length;
  const collisions = domains.filter(d => d.collides_with).length;
  /* A subject nothing is filed under, that memories are cross-listed into:
     the end-to-end flow whose steps all live in other branches. Worth
     counting in the header, since it is the reason to read the last column. */
  const crossing = domains.filter(d => d.subtree_also && !d.subtree_active && !d.subtree_archived).length;
  /* Rows the archived filter would hide -- implicit levels included, since
     hiding one is what makes a branch disappear. Deliberately NOT in the
     header beside "N named domains": that count excludes the implicit
     levels, so the two numbers side by side would contradict each other.
     It belongs on the control that acts on it anyway. */
  const archived = domains.filter(isArchived).length;

  view.innerHTML = `<div class="anim">
    <div class="view-head">
      <h2 class="view-title">${t('do.title')}</h2>
      <div class="view-sub">${t('do.sub.count', { n: fmtInt(named) })} · ${t('do.sub.roots', { n: fmtInt(roots) })}${
        crossing ? ` · ${t('do.sub.crossing', { n: fmtInt(crossing) })}` : ''}${
        collisions ? ` · <span style="color:var(--warn)">${t('do.sub.collide', { n: collisions })}</span>` : ''}</div>
    </div>
    <div class="panel" style="margin-bottom:14px">
      <h3 class="panel-title">${t('do.case.title')}</h3>
      <div class="intro">${t('do.case.desc')}</div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        ${pickerFor({ id: 'caseMode', value: cfg.domain_case, items: caseItems,
                      ariaLabel: t('do.case.title') })}
        <button class="btn btn-solid" id="caseSave">${t('do.case.save')}</button>
        <button class="btn" id="caseNorm">${t('do.case.normalize')}</button>
      </div>
    </div>
    <div class="panel">
      <div class="intro">${t('do.tree.intro')}</div>
      <div class="act-row" style="margin-bottom:10px">
        <button class="btn btn-sm" id="domExpand">${t('do.tree.expandAll')}</button>
        <button class="btn btn-sm" id="domCollapse">${t('do.tree.collapseAll')}</button>
        <!-- only where the choice exists: with nothing archived the toggle
             hides nothing, and an inert control is noise -->
        ${archived ? `<button class="btn btn-sm" id="domArchived"></button>` : ''}
      </div>
      <div class="table-scroll">
        <table class="table">
          <thead><tr><th>${t('common.domain')}</th><th class="num">${t('do.th.active')}</th><th class="num">${t('do.th.archived')}</th><th class="num" title="${esc(t('do.th.alsoWhy'))}">${t('do.th.also')}</th><th>${t('do.th.types')}</th><th class="num">${t('common.lastActivity')}</th><th></th></tr></thead>
          <tbody id="domRows"></tbody>
        </table>
      </div>
      <div id="domEmpty"></div>
    </div>
  </div>`;

  const body = view.querySelector('#domRows');
  const empty = view.querySelector('#domEmpty');
  const draw = () => {
    body.innerHTML = rows(domains);
    wire(body, domains, draw);
    /* Every row filtered away is not the same as an empty store: a store
       whose every domain is archived would otherwise draw a table with no
       rows and no reason given for it. */
    empty.innerHTML = !domains.length ? `<div class="empty">${t('do.empty')}</div>`
      : body.childElementCount ? '' : `<div class="empty">${t('do.emptyArchived')}</div>`;
  };
  draw();

  view.querySelector('#domExpand').onclick = () => { collapsed.clear(); draw(); };
  view.querySelector('#domCollapse').onclick = () => {
    domains.forEach(d => { if (d.children) collapsed.add(d.domain); });
    draw();
  };

  const archBtn = view.querySelector('#domArchived');
  if (archBtn) {
    const label = () => {
      archBtn.textContent = t(showArchived ? 'do.tree.archivedHide' : 'do.tree.archivedShow',
                              { n: fmtInt(archived) });
      setPressed(archBtn, showArchived);
    };
    label();
    archBtn.onclick = () => { showArchived = !showArchived; label(); draw(); };
  }

  /* Picking a casing does not APPLY it -- Save does, and Normalize is what
     touches what is already stored. So the pick only holds a value. */
  wirePicker(view, { id: 'caseMode', items: fixedItems(caseItems), onPick: () => {} });
  view.querySelector('#caseSave').onclick = async () => {
    try {
      await api('/api/config', { body: { domain_case: pickerValue(view, 'caseMode') } });
      toast(t('do.case.saved'), 'ok');
    } catch (err) { failed('err.save', err); }
  };
  view.querySelector('#caseNorm').onclick = () => openNormalizeModal();
}

/* What the tree IS right now: tree order, minus the archived branches unless
   they were asked for. Filtering per row takes the branch whole -- one active
   memory anywhere below a level makes that level active, so an archived
   level's descendants are all archived too.

   The twists and the "n below" counts are read off THIS and not off the
   server's raw child count: a branch whose children are all archived would
   otherwise offer a twist that opens onto nothing. */
function present(domains) {
  return domains.slice().sort(byDomainPath).filter(d => showArchived || !isArchived(d));
}

/* ...and what is drawn: a collapsed branch hides its whole subtree rather
   than just its children. */
function visible(list) {
  const hidden = [...collapsed];
  return list.filter(d =>
    !hidden.some(c => d.domain !== c && d.domain.startsWith(`${c}/`)));
}

function rows(domains) {
  const here = present(domains);
  const kids = new Map();
  here.forEach(d => kids.set(d.parent, (kids.get(d.parent) || 0) + 1));
  const list = visible(here);
  /* The same shape the filter select draws, from the same function -- read
     over the VISIBLE list, so the last row left in a filtered branch gets the
     closing elbow rather than a line running on into the row below it. */
  const guides = domainGuides(list);
  return list.map((d, i) => {
    /* `tp`, not `t` -- t is the translator, and shadowing it here would
       break the next line that reaches for it */
    const dots = TYPE_ORDER.filter(tp => d.types[tp]).map(tp =>
      `<span class="dot" style="--c:${typeColor(tp)}" title="${esc(tp)}: ${fmtInt(d.types[tp])}"></span>`).join('');
    const collide = d.collides_with
      ? `<button type="button" class="collide-chip" data-merge="${esc(d.collides_with[0])}" data-into="${esc(d.domain)}"
           title="${t('do.collide.title', { list: esc(d.collides_with.join(', ')) })}">${icon('approx')}${esc(domainLeaf(d.collides_with[0]))}</button>` : '';
    const children = kids.get(d.domain) || 0;
    const open = children && !collapsed.has(d.domain);
    const twist = children
      ? `<button type="button" class="dom-twist" data-twist="${esc(d.domain)}" aria-expanded="${open}"
           aria-label="${esc(t(open ? 'do.tree.collapse' : 'do.tree.expand', { domain: d.domain }))}">${icon('chevron-right')}</button>`
      : `<span class="dom-twist leafpad">${icon('chevron-right')}</span>`;
    /* The rail draws over the indent rather than replacing it: its columns
       are the same width the ::before spacer reserves, so the tree lines up
       exactly where it used to and the lines still reach the row above and
       below (see .dom-rail -- it is positioned against the whole cell box,
       padding included, or every vertical would stop short of the next). */
    const rail = domainRailHTML(guides[i], { leaf: !children });
    /* A parent's own counts and its subtree's are different facts and both
       matter: 0 of its own with 40 below it is exactly the shape that made
       one flat bucket per subject stop working. */
    const roll = (own, sub) => sub > own
      ? `<span class="dom-roll" title="${esc(t('do.rollupWhy'))}">+${fmtInt(sub - own)}</span>` : '';
    const latest = d.latest_at || d.subtree_latest_at;
    /* Filed here versus cross-listed here are different facts, so the count
       is its own column. A row with only the second one is a subject that
       organizes memories living elsewhere -- said outright, or it reads as
       an empty branch somebody forgot to delete. */
    const crossing = d.subtree_also && !d.subtree_active && !d.subtree_archived;
    /* Said on the row, not only by a zero in the active column: the whole
       point of archiving a level is that it stops reading as live. */
    const dead = isArchived(d)
      ? `<span class="status-tag archived" title="${esc(t('do.tree.archivedWhy'))}">${t('do.tree.archivedTag')}</span>` : '';
    return `<tr title="${esc(d.domain)}"${dead ? ' class="dom-dead"' : ''}>
      <td class="dom-td">
        ${rail}
        <span class="dom-cell" style="--d:${d.depth - 1}">
          ${twist}
          <span class="dom-leaf${d.implicit ? ' implicit' : ''}">${esc(domainLeaf(d.domain))}</span>
          ${children ? `<span class="dom-kids">${t('do.tree.kids', { n: children })}</span>` : ''}
          ${crossing ? `<span class="dom-kids" title="${esc(t('do.tree.crossingWhy'))}">${t('do.tree.crossing')}</span>` : ''}
          ${dead}
          ${collide}
        </span>
      </td>
      <td class="num">${fmtInt(d.active)}${roll(d.active, d.subtree_active)}</td>
      <td class="num" style="color:var(--ink-3)">${fmtInt(d.archived)}${roll(d.archived, d.subtree_archived)}</td>
      <td class="num" style="color:var(--ink-3)">${d.subtree_also ? fmtInt(d.also) + roll(d.also, d.subtree_also) : ''}</td>
      <td><span class="type-dots">${dots}</span></td>
      <td class="num" style="color:var(--ink-3)" title="${esc(latest)}">${fmtAgo(latest)}</td>
      <td class="actions">
        <button class="btn btn-sm" data-see="${esc(d.domain)}">${t('common.view')}</button>
        <!-- offered on an implicit level too: it holds no memory of its own,
             but moving it takes the subtree hanging off it -->
        <button class="btn btn-sm" data-ren="${esc(d.domain)}">${t('do.rn.move')}</button>
        <!-- to another STORE, subtree and archived memories included; unlike
             the rename above, the memories leave this file -->
        <button class="btn btn-sm" data-mv="${esc(d.domain)}">${t('do.act.toStore')}</button>
        <!-- One direction at a time, chosen by what is under the level:
             something active to archive, otherwise something archived to
             bring back. A purely cross-cutting level has neither, and gets
             neither button. -->
        ${d.subtree_active
          ? `<button class="btn btn-sm" data-arch="${esc(d.domain)}">${t('do.act.archive')}</button>`
          : d.subtree_archived
            ? `<button class="btn btn-sm" data-rest="${esc(d.domain)}">${t('do.act.restore')}</button>` : ''}
        <button type="button" class="icon-btn danger" data-del="${esc(d.domain)}"
                title="${t('do.act.delete')}"
                aria-label="${esc(t('do.act.deleteAria', { domain: d.domain }))}">${icon('trash')}</button>
      </td>
    </tr>`;
  }).join('');
}

function wire(body, domains, draw) {
  body.querySelectorAll('[data-twist]').forEach(b =>
    b.addEventListener('click', () => {
      const path = b.dataset.twist;
      collapsed.has(path) ? collapsed.delete(path) : collapsed.add(path);
      draw();
    }));
  /* status='' so the memories view shows the subtree whole -- a domain
     filter there covers descendants, which is the point of picking one */
  body.querySelectorAll('[data-see]').forEach(b =>
    b.addEventListener('click', () => go('memories', { domain: b.dataset.see, status: '' })));
  body.querySelectorAll('[data-ren]').forEach(b =>
    b.addEventListener('click', () => openRenameModal(b.dataset.ren, domains)));
  body.querySelectorAll('[data-mv]').forEach(b =>
    b.addEventListener('click', async () => {
      const moved = await moveToStoreModal({ domain: b.dataset.mv });
      if (!moved) return;
      invalidateDomains();
      refreshBehind();
    }));
  body.querySelectorAll('[data-merge]').forEach(chip =>
    chip.addEventListener('click', () => openRenameModal(chip.dataset.merge, domains, chip.dataset.into)));

  const node = path => domains.find(d => d.domain === path);
  body.querySelectorAll('[data-arch]').forEach(b =>
    b.addEventListener('click', () => archiveDomain(node(b.dataset.arch))));
  body.querySelectorAll('[data-rest]').forEach(b =>
    b.addEventListener('click', () => restoreDomain(node(b.dataset.rest))));
  body.querySelectorAll('[data-del]').forEach(b =>
    b.addEventListener('click', () => openDeleteModal(node(b.dataset.del), domains)));
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
      refreshBehind();
    } catch (err) { failed('err.domainPurge', err); }
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
    try {
      const r = await api('/api/domains/rename', { body: { from, to: toInput.value.trim() } });
      closeModal();
      toast(t('do.rn.moved', { n: r.affected }) + (r.merged ? t('do.rn.merged') : ''), 'ok');
      invalidateDomains();
      refreshBehind();
    } catch (err) { failed('err.domain', err); }
  };
}
