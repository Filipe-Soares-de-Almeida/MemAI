/* Domains: the buckets memories are filed under, plus the two operations
   that move memories between them -- a rename (which merges if the target
   exists) and a case normalization pass. */

import { esc, fmtInt, fmtAgo } from '../core/dom.js';
import { api } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, failed, openModal, closeModal } from '../core/ui.js';
import { typeColor, TYPE_ORDER, getDomains, invalidateDomains } from '../core/shared.js';
import { go, refreshBehind } from '../core/router.js';
import { t } from '../i18n.js';

const CASE_MODES = ['preserve', 'lower', 'upper'];

export async function renderDomains(view, params, ctx) {
  const domains = await getDomains(true);
  const cfg = await api('/api/config').catch(() => ({ domain_case: 'preserve' }));
  if (ctx.stale()) return;

  const rows = domains.map(d => {
    /* `tp`, not `t` -- t is the translator, and shadowing it here would
       break the next line that reaches for it */
    const dots = TYPE_ORDER.filter(tp => d.types[tp]).map(tp =>
      `<span class="dot" style="--c:${typeColor(tp)}" title="${esc(tp)}: ${fmtInt(d.types[tp])}"></span>`).join('');
    const collide = d.collides_with
      ? `<button type="button" class="collide-chip" data-merge="${esc(d.collides_with[0])}" data-into="${esc(d.domain)}"
           title="${t('do.collide.title', { list: esc(d.collides_with.join(', ')) })}">${icon('approx')}${esc(d.collides_with[0])}</button>` : '';
    return `<tr>
      <td style="color:var(--ink)">${esc(d.domain)} ${collide}</td>
      <td class="num">${fmtInt(d.active)}</td>
      <td class="num" style="color:var(--ink-3)">${fmtInt(d.archived)}</td>
      <td><span class="type-dots">${dots}</span></td>
      <td class="num" style="color:var(--ink-3)" title="${esc(d.latest_at)}">${fmtAgo(d.latest_at)}</td>
      <td class="actions">
        <button class="btn btn-sm" data-see="${esc(d.domain)}">${t('common.view')}</button>
        <button class="btn btn-sm" data-ren="${esc(d.domain)}">${t('common.rename')}</button>
      </td>
    </tr>`;
  }).join('');

  const collisions = domains.filter(d => d.collides_with).length;

  view.innerHTML = `<div class="anim">
    <div class="view-head">
      <h2 class="view-title">${t('do.title')}</h2>
      <div class="view-sub">${t('do.sub.count', { n: fmtInt(domains.length) })}${collisions ? ` · <span style="color:var(--warn)">${t('do.sub.collide', { n: collisions })}</span>` : ''}</div>
    </div>
    <div class="panel" style="margin-bottom:14px">
      <h3 class="panel-title">${t('do.case.title')}</h3>
      <div style="font-size:11.5px;color:var(--ink-3);margin-bottom:10px">${t('do.case.desc')}</div>
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
      <div class="table-scroll">
        <table class="table">
          <thead><tr><th>${t('common.domain')}</th><th class="num">${t('do.th.active')}</th><th class="num">${t('do.th.archived')}</th><th>${t('do.th.types')}</th><th class="num">${t('common.lastActivity')}</th><th></th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      ${rows ? '' : `<div class="empty">${t('do.empty')}</div>`}
    </div>
  </div>`;

  view.querySelectorAll('[data-see]').forEach(b =>
    b.addEventListener('click', () => go('memories', { domain: b.dataset.see, status: '' })));
  view.querySelectorAll('[data-ren]').forEach(b =>
    b.addEventListener('click', () => openRenameModal(b.dataset.ren, domains)));
  view.querySelectorAll('[data-merge]').forEach(chip =>
    chip.addEventListener('click', () => openRenameModal(chip.dataset.merge, domains, chip.dataset.into)));

  const modeSel = view.querySelector('#caseMode');
  view.querySelector('#caseSave').onclick = async () => {
    try {
      await api('/api/config', { body: { domain_case: modeSel.value } });
      toast(t('do.case.saved'), 'ok');
    } catch (err) { failed('err.save', err); }
  };
  view.querySelector('#caseNorm').onclick = () => openNormalizeModal();
}

async function openNormalizeModal() {
  let plan;
  try {
    plan = await api('/api/domains/normalize', { body: { dry_run: true } });
  } catch (err) { failed('err.load', err); return; }
  if (plan.mode === 'preserve') { toast(t('do.case.preserveHint'), ''); return; }
  if (!plan.plan.length) { toast(t('do.case.none'), 'ok'); return; }
  const rows = plan.plan.map(e => `<tr>
    <td>${esc(e.from)}</td>
    <td style="color:var(--ink)">${esc(e.to)}</td>
    <td class="num">${fmtInt(e.count)}</td>
    <td><span style="color:${e.action === 'merge' ? 'var(--warn)' : 'var(--ink-3)'}">${t('do.norm.act.' + e.action)}</span></td>
  </tr>`).join('');
  const modal = openModal({
    title: t('do.norm.title'),
    bodyHTML: `
      <div style="font-size:12px;margin-bottom:8px">${t('do.norm.intro', { renames: plan.renames, merges: plan.merges, mode: t('do.case.mode.' + plan.mode) })}</div>
      ${plan.merges ? `<div style="font-size:11.5px;color:var(--warn);margin-bottom:8px">${t('do.norm.mergeWarn')}</div>` : ''}
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
  const dl = domains.map(d => `<option value="${esc(d.domain)}">`).join('');
  const modal = openModal({
    title: presetTo ? t('do.rn.merge') : t('do.rn.rename'),
    bodyHTML: `
      <div class="field"><label for="rnFrom">${t('do.rn.from')}</label>
        <input type="text" id="rnFrom" value="${esc(from)}" disabled></div>
      <div class="field"><label for="rnTo">${t('do.rn.to')}</label>
        <input type="text" id="rnTo" value="${esc(presetTo)}" list="rnDL" placeholder="${t('do.rn.placeholder')}">
        <datalist id="rnDL">${dl}</datalist></div>
      <div id="rnWarn" style="font-size:11.5px;color:var(--warn)" hidden>${t('do.rn.warn')}</div>
      <div style="font-size:11px;color:var(--ink-3)">${t('do.rn.hint')}</div>`,
    footHTML: `<button class="btn" data-x>${t('common.cancel')}</button><button class="btn btn-solid" data-ok>${t('common.apply')}</button>`,
  });
  const toInput = modal.querySelector('#rnTo');
  const check = () => {
    modal.querySelector('#rnWarn').hidden =
      !domains.some(d => d.domain === toInput.value.trim() && d.domain !== from);
  };
  toInput.addEventListener('input', check); check();
  modal.querySelector('[data-x]').onclick = closeModal;
  modal.querySelector('[data-ok]').onclick = async () => {
    try {
      const r = await api('/api/domains/rename', { body: { from, to: toInput.value.trim() } });
      closeModal();
      toast(t('do.rn.moved', { n: r.affected }) + (r.merged ? t('do.rn.merged') : ''), 'ok');
      invalidateDomains();
      refreshBehind();
    } catch (err) { failed('err.domain', err); }
  };
}
