/* Domains: the buckets memories are filed under -- a TREE of them, since a
   domain is a path ('acme/x100/p200') and a subject contains subjects.
   Plus the operations that move memories between buckets: a rename (which
   nests when the target is a path, and merges when it already exists) and a
   case/shape normalization pass. */

import { esc, fmtInt, fmtAgo } from '../core/dom.js';
import { api } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, failed, openModal, closeModal } from '../core/ui.js';
import { typeColor, TYPE_ORDER, getDomains, invalidateDomains,
         byDomainPath, domainLeaf, domainDatalist } from '../core/shared.js';
import { go, refreshBehind } from '../core/router.js';
import { t } from '../i18n.js';

const CASE_MODES = ['preserve', 'lower', 'upper'];

/* Collapsed branches, by path. Module-level so applying a rename (which
   re-renders behind the modal) does not throw the reader back to the top of
   a tree they had just navigated. Collapsed rather than expanded is what is
   remembered, so a brand-new branch arrives open. */
const collapsed = new Set();

export async function renderDomains(view, params, ctx) {
  const domains = await getDomains(true);
  const cfg = await api('/api/config').catch(() => ({ domain_case: 'preserve' }));
  if (ctx.stale()) return;

  const roots = domains.filter(d => !d.parent).length;
  const named = domains.filter(d => !d.implicit).length;
  const collisions = domains.filter(d => d.collides_with).length;

  view.innerHTML = `<div class="anim">
    <div class="view-head">
      <h2 class="view-title">${t('do.title')}</h2>
      <div class="view-sub">${t('do.sub.count', { n: fmtInt(named) })} · ${t('do.sub.roots', { n: fmtInt(roots) })}${
        collisions ? ` · <span style="color:var(--warn)">${t('do.sub.collide', { n: collisions })}</span>` : ''}</div>
    </div>
    <div class="panel" style="margin-bottom:14px">
      <h3 class="panel-title">${t('do.case.title')}</h3>
      <div class="intro">${t('do.case.desc')}</div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <select id="caseMode" aria-label="${t('do.case.title')}">
          ${CASE_MODES.map(mode =>
            `<option value="${mode}"${cfg.domain_case === mode ? ' selected' : ''}>${t('do.case.mode.' + mode)}</option>`).join('')}
        </select>
        <button class="btn btn-solid" id="caseSave">${t('do.case.save')}</button>
        <button class="btn" id="caseNorm">${t('do.case.normalize')}</button>
      </div>
    </div>
    <div class="panel">
      <div class="intro">${t('do.tree.intro')}</div>
      <div class="act-row" style="margin-bottom:10px">
        <button class="btn btn-sm" id="domExpand">${t('do.tree.expandAll')}</button>
        <button class="btn btn-sm" id="domCollapse">${t('do.tree.collapseAll')}</button>
      </div>
      <div class="table-scroll">
        <table class="table">
          <thead><tr><th>${t('common.domain')}</th><th class="num">${t('do.th.active')}</th><th class="num">${t('do.th.archived')}</th><th>${t('do.th.types')}</th><th class="num">${t('common.lastActivity')}</th><th></th></tr></thead>
          <tbody id="domRows"></tbody>
        </table>
      </div>
      <div id="domEmpty">${domains.length ? '' : `<div class="empty">${t('do.empty')}</div>`}</div>
    </div>
  </div>`;

  const body = view.querySelector('#domRows');
  const draw = () => { body.innerHTML = rows(domains); wire(body, domains, draw); };
  draw();

  view.querySelector('#domExpand').onclick = () => { collapsed.clear(); draw(); };
  view.querySelector('#domCollapse').onclick = () => {
    domains.forEach(d => { if (d.children) collapsed.add(d.domain); });
    draw();
  };

  const modeSel = view.querySelector('#caseMode');
  view.querySelector('#caseSave').onclick = async () => {
    try {
      await api('/api/config', { body: { domain_case: modeSel.value } });
      toast(t('do.case.saved'), 'ok');
    } catch (err) { failed('err.save', err); }
  };
  view.querySelector('#caseNorm').onclick = () => openNormalizeModal();
}

/* Every row the reader can currently see: tree order, and a collapsed
   branch hides its whole subtree rather than just its children. */
function visible(domains) {
  const hidden = [...collapsed];
  return domains.slice().sort(byDomainPath).filter(d =>
    !hidden.some(c => d.domain !== c && d.domain.startsWith(`${c}/`)));
}

function rows(domains) {
  return visible(domains).map(d => {
    /* `tp`, not `t` -- t is the translator, and shadowing it here would
       break the next line that reaches for it */
    const dots = TYPE_ORDER.filter(tp => d.types[tp]).map(tp =>
      `<span class="dot" style="--c:${typeColor(tp)}" title="${esc(tp)}: ${fmtInt(d.types[tp])}"></span>`).join('');
    const collide = d.collides_with
      ? `<button type="button" class="collide-chip" data-merge="${esc(d.collides_with[0])}" data-into="${esc(d.domain)}"
           title="${t('do.collide.title', { list: esc(d.collides_with.join(', ')) })}">${icon('approx')}${esc(domainLeaf(d.collides_with[0]))}</button>` : '';
    const open = d.children && !collapsed.has(d.domain);
    const twist = d.children
      ? `<button type="button" class="dom-twist" data-twist="${esc(d.domain)}" aria-expanded="${open}"
           aria-label="${esc(t(open ? 'do.tree.collapse' : 'do.tree.expand', { domain: d.domain }))}">${icon('chevron-right')}</button>`
      : `<span class="dom-twist leafpad">${icon('chevron-right')}</span>`;
    /* A parent's own counts and its subtree's are different facts and both
       matter: 0 of its own with 40 below it is exactly the shape that made
       one flat bucket per subject stop working. */
    const roll = (own, sub) => sub > own
      ? `<span class="dom-roll" title="${esc(t('do.rollupWhy'))}">+${fmtInt(sub - own)}</span>` : '';
    const latest = d.latest_at || d.subtree_latest_at;
    return `<tr title="${esc(d.domain)}">
      <td>
        <span class="dom-cell" style="--d:${d.depth - 1}">
          ${twist}
          <span class="dom-leaf${d.implicit ? ' implicit' : ''}">${esc(domainLeaf(d.domain))}</span>
          ${d.children ? `<span class="dom-kids">${t('do.tree.kids', { n: d.children })}</span>` : ''}
          ${collide}
        </span>
      </td>
      <td class="num">${fmtInt(d.active)}${roll(d.active, d.subtree_active)}</td>
      <td class="num" style="color:var(--ink-3)">${fmtInt(d.archived)}${roll(d.archived, d.subtree_archived)}</td>
      <td><span class="type-dots">${dots}</span></td>
      <td class="num" style="color:var(--ink-3)" title="${esc(latest)}">${fmtAgo(latest)}</td>
      <td class="actions">
        <button class="btn btn-sm" data-see="${esc(d.domain)}">${t('common.view')}</button>
        <!-- offered on an implicit level too: it holds no memory of its own,
             but moving it takes the subtree hanging off it -->
        <button class="btn btn-sm" data-ren="${esc(d.domain)}">${t('do.rn.move')}</button>
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
  body.querySelectorAll('[data-merge]').forEach(chip =>
    chip.addEventListener('click', () => openRenameModal(chip.dataset.merge, domains, chip.dataset.into)));
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
