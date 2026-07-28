/* Diagram list: one card per flow, with the soundness checks the store
   reports for it. Filtering is client-side -- the list is small and the
   whole point is to scan it. */

import { $, esc, fmtInt, fmtAgo } from '../core/dom.js';
import { api } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, promptModal } from '../core/ui.js';
import { statusTag, confPill, uidChip, wireCopyChips, getDomains,
         invalidateDomains } from '../core/shared.js';
import { go } from '../core/router.js';
import { openRecord } from './record.js';
import { t } from '../i18n.js';

const ISSUE_ORDER = ['empty', 'no_start', 'many_starts', 'unreachable',
                     'dead_end', 'no_end'];

/* A flow starts as a start→end skeleton and is grown on the canvas: there
   is no useful "empty diagram", and typing a graph as text is not the
   point of the type. */
export const newDiagramSkeleton = ({ title, domain = '', tags = '' }) =>
  api('/api/diagrams', { body: {
    title, domain, tags,
    nodes: [
      { key: 'start', shape: 'start', label: t('dg.skeleton.start') },
      { key: 'finish', shape: 'end', label: t('dg.skeleton.end') },
    ],
    edges: [{ from: 'start', to: 'finish' }] } });

async function promptNewDiagram(domain = '') {
  const title = await promptModal({
    title: t('dgl.newTitle'), body: t('dgl.newBody'),
    label: t('dg.meta.name'), placeholder: t('nm.titlePh'), okLabel: t('dgl.new'),
  });
  if (title === null || !title.trim()) return;
  try {
    const r = await newDiagramSkeleton({ title, domain });
    invalidateDomains();
    go('diagram', { uid: r.uid });
  } catch (err) { toast(err.message, 'bad'); }
}

function issueChip(issue) {
  const label = t(`dg.issue.${issue.kind}`);
  const keys = issue.keys.length ? `: ${issue.keys.join(', ')}` : '';
  return `<span class="dgl-issue" title="${esc(t(`dg.issueWhy.${issue.kind}`))}">${esc(label)}${esc(keys)}</span>`;
}

export async function renderDiagrams(view, params, ctx) {
  const state = {
    status: params.has('status') ? params.get('status') : 'active',
    domain: params.get('domain') || '',
  };
  /* status is sent even when empty -- "" means "any status", which is not
     the same request as omitting the filter */
  const qs = new URLSearchParams({ status: state.status });
  if (state.domain) qs.set('domain', state.domain);
  const [domains, data] = await Promise.all([
    getDomains().catch(() => []),
    api(`/api/diagrams?${qs}`),
  ]);
  if (ctx.stale()) return;

  view.innerHTML = `<div class="anim">
    <div class="view-head">
      <h2 class="view-title">${t('dgl.title')}</h2>
      <div class="view-sub">${t('dgl.sub', { n: fmtInt(data.total) })}${
        data.with_issues
          ? ` · <span style="color:var(--warn)">${t('dgl.subIssues', { n: data.with_issues })}</span>`
          : (data.total ? ` · <span style="color:var(--ok)">${t('dgl.allSound')}</span>` : '')}</div>
    </div>

    <div class="panel" style="margin-bottom:14px">
      <div style="font-size:11.5px;color:var(--ink-4);margin-bottom:10px">${t('dgl.intro')}</div>
      <div class="act-row">
        <select id="dglDomain">
          <option value="">${t('common.allDomains')}</option>
          ${domains.map(d => `<option value="${esc(d.domain)}" ${d.domain === state.domain ? 'selected' : ''}>${esc(d.domain)}</option>`).join('')}
        </select>
        <div class="seg" id="dglStatus">
          <button data-v="active" class="${state.status === 'active' ? 'active' : ''}">${t('common.active')}</button>
          <button data-v="" class="${state.status === '' ? 'active' : ''}">${t('common.all')}</button>
        </div>
        <input type="text" id="dglFilter" placeholder="${t('dgl.filter')}" style="flex:1;min-width:160px" autocomplete="off">
        <button class="btn btn-solid" id="dglNew">${t('dgl.new')}</button>
      </div>
    </div>

    <div id="dglGrid"></div>
  </div>`;

  const nav = patch => {
    const p = { ...state, ...patch };
    const out = {};
    if (p.domain) out.domain = p.domain;
    if (p.status === '') out.status = '';
    go('diagrams', out);
  };
  $('#dglDomain').addEventListener('change', e => nav({ domain: e.target.value }));
  view.querySelectorAll('#dglStatus button').forEach(b =>
    b.addEventListener('click', () => nav({ status: b.dataset.v })));
  $('#dglNew').addEventListener('click', () => promptNewDiagram(state.domain));

  const card = d => {
    const issues = d.issues.slice().sort(
      (a, b) => ISSUE_ORDER.indexOf(a.kind) - ISSUE_ORDER.indexOf(b.kind));
    return `<div class="dgl-card${issues.length ? ' has-issues' : ''}">
      <div class="dgl-top">
        <span class="dgl-title" data-edit="${esc(d.uid)}" title="${esc(d.title)}">${esc(d.title || '—')}</span>
        ${statusTag(d.status)} ${confPill(d.confidence)}
      </div>
      ${d.summary ? `<div class="dgl-summary" title="${esc(d.summary)}">${esc(d.summary)}</div>` : ''}
      <div class="dgl-stats">
        <span>${t('dgl.steps', { n: d.nodes })}</span>
        <span>${t('dgl.conns', { n: d.edges })}</span>
        <span>${t('dgl.linked', { n: d.links })}</span>
        <span title="${t('dgl.documentedWhy')}">${t('dgl.documented', { n: d.documented, total: d.nodes })}</span>
      </div>
      <div class="dgl-issues">
        ${issues.length ? issues.map(issueChip).join('')
          : `<span class="dgl-sound">${icon('confirmed')}${t('dgl.sound')}</span>`}
      </div>
      <div class="dgl-foot">
        ${d.domain ? `<span class="chip clickable" data-fdomain="${esc(d.domain)}">${esc(d.domain)}</span>` : ''}
        ${uidChip(d.uid)}
        <span class="spacer"></span>
        <span class="dgl-when" title="${esc(d.updated_at)}">${fmtAgo(d.updated_at)}</span>
        <button class="btn btn-sm" data-record="${esc(d.uid)}">${t('dg.record')}</button>
        <button class="btn btn-sm btn-solid" data-edit="${esc(d.uid)}">${t('dr.openEditor')}</button>
      </div>
    </div>`;
  };

  const grid = $('#dglGrid');
  const draw = () => {
    const q = $('#dglFilter').value.trim().toLowerCase();
    const shown = q
      ? data.items.filter(d => `${d.title} ${d.summary} ${d.domain} ${d.tags}`.toLowerCase().includes(q))
      : data.items;
    grid.innerHTML = shown.length
      ? `<div class="dgl-grid">${shown.map(card).join('')}</div>`
      : `<div class="empty">${data.total ? t('dgl.noMatch') : `${t('dgl.empty')}<div class="dg-empty" style="margin-top:8px">${t('dgl.emptyHint')}</div>`}</div>`;
    wireCopyChips(grid);
    grid.querySelectorAll('[data-edit]').forEach(el =>
      el.addEventListener('click', () => go('diagram', { uid: el.dataset.edit })));
    grid.querySelectorAll('[data-record]').forEach(el =>
      el.addEventListener('click', e => { e.stopPropagation(); openRecord(el.dataset.record); }));
    grid.querySelectorAll('[data-fdomain]').forEach(el =>
      el.addEventListener('click', e => { e.stopPropagation(); nav({ domain: el.dataset.fdomain }); }));
  };
  $('#dglFilter').addEventListener('input', draw);
  draw();
}
