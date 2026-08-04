/* Diagram list: one card per flow, with the soundness checks the store
   reports for it. Filtering is client-side -- the list is small and the
   whole point is to scan it. */

import { $, esc, fmtInt, fmtAgo } from '../core/dom.js';
import { api } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, failed, promptModal } from '../core/ui.js';
import { statusTag, confPill, uidChip, wireCopyChips, getDomains,
         invalidateDomains, domainSegments, inDomainPath,
         DOMAIN_SEP } from '../core/shared.js';
import { domainPickerHTML, wireDomainPicker } from '../core/domain-picker.js';
import { go } from '../core/router.js';
import { openRecord } from './record.js';
import { t } from '../i18n.js';

const ISSUE_ORDER = ['empty', 'no_start', 'many_starts', 'unreachable',
                     'dead_end', 'no_end'];

/* A flow starts as a start→end skeleton and is grown on the canvas: there
   is no useful "empty diagram", and typing a graph as text is not the
   point of the type. */
export const newDiagramSkeleton = ({ title, domain = '', also = '', tags = '' }) =>
  api('/api/diagrams', { body: {
    title, domain, also, tags,
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
  } catch (err) { failed('err.create', err); }
}

function issueChip(issue) {
  const label = t(`dg.issue.${issue.kind}`);
  const keys = issue.keys.length ? `: ${issue.keys.join(', ')}` : '';
  const why = t(`dg.issueWhy.${issue.kind}`);
  /* role="note" so the aria-label has something to attach to -- a label on a
     bare span is dropped, which left the explanation of a structural fault
     visible only to a mouse hovering it. tabindex so a keyboard can stop on
     it and hear the same thing. */
  return `<span class="dgl-issue" role="note" tabindex="0"
                title="${esc(why)}" aria-label="${esc(`${label}${keys} — ${why}`)}">${esc(label)}${esc(keys)}</span>`;
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
      <div class="intro">${t('dgl.intro')}</div>
      <div class="act-row">
        ${domainPickerHTML({ id: 'dglDomain', value: state.domain,
                             ariaLabel: t('common.allDomains') })}
        <div class="seg" id="dglStatus" role="group" aria-label="${t('mem.status.aria')}">
          <button type="button" data-v="active" aria-pressed="${state.status === 'active'}">${t('common.active')}</button>
          <button type="button" data-v="" aria-pressed="${state.status === ''}">${t('common.all')}</button>
        </div>
        <input type="text" id="dglFilter" placeholder="${t('dgl.filter')}" aria-label="${t('dgl.filter')}"
               style="flex:1;min-width:160px" autocomplete="off">
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
  wireDomainPicker(view, { id: 'dglDomain', domains, onPick: domain => nav({ domain }) });
  view.querySelectorAll('#dglStatus button').forEach(b =>
    b.addEventListener('click', () => nav({ status: b.dataset.v })));
  $('#dglNew').addEventListener('click', () => promptNewDiagram(state.domain));

  /* `branch` is the group the card is being drawn under. It is not always
     where the flow is filed -- a flow whose home is outside the current
     filter is drawn under the cross-listing that brought it into the list,
     and it says so, or it reads as filed there. */
  const card = (d, branch) => {
    const issues = d.issues.slice().sort(
      (a, b) => ISSUE_ORDER.indexOf(a.kind) - ISSUE_ORDER.indexOf(b.kind));
    const away = branch !== homeOf(d);
    return `<div class="dgl-card${issues.length ? ' has-issues' : ''}">
      <div class="dgl-top">
        <button type="button" class="dgl-title" data-edit="${esc(d.uid)}" title="${esc(d.title)}">${esc(d.title || '—')}</button>
        ${statusTag(d.status)} ${confPill(d.confidence)}
      </div>
      ${d.summary ? `<div class="dgl-summary" title="${esc(d.summary)}">${esc(d.summary)}</div>` : ''}
      <div class="dgl-stats">
        <span>${t('dgl.steps', { n: d.nodes })}</span>
        <span>${t('dgl.conns', { n: d.edges })}</span>
        <span>${t('dgl.linked', { n: d.links })}</span>
        <!-- counted from both ends: a flow nothing leaves but three arrive
             into is as tied into the set as the one that made those jumps -->
        ${d.jumps ? `<span title="${t('dgl.jumpsWhy')}">${t('dgl.jumps', { n: d.jumps })}</span>` : ''}
        <span title="${t('dgl.documentedWhy')}">${t('dgl.documented', { n: d.documented, total: d.nodes })}</span>
      </div>
      <div class="dgl-issues">
        ${issues.length ? issues.map(issueChip).join('')
          : `<span class="dgl-sound">${icon('confirmed')}${t('dgl.sound')}</span>`}
      </div>
      <div class="dgl-foot">
        ${away ? `<span class="chip" title="${esc(t('dgl.alsoWhy'))}">${t('dgl.also')}</span>` : ''}
        ${d.domain ? `<button type="button" class="chip clickable" data-fdomain="${esc(d.domain)}"
                aria-label="${esc(t('a11y.filterDomain', { domain: d.domain }))}">${esc(d.domain)}</button>` : ''}
        ${(d.also || []).map(p => `<button type="button" class="chip clickable" data-fdomain="${esc(p)}"
                aria-label="${esc(t('a11y.filterDomain', { domain: p }))}">${esc(p)}</button>`).join('')}
        ${uidChip(d.uid)}
        <span class="spacer"></span>
        <span class="dgl-when" title="${esc(d.updated_at)}">${fmtAgo(d.updated_at)}</span>
        <button class="btn btn-sm" data-record="${esc(d.uid)}">${t('dg.record')}</button>
        <button class="btn btn-sm btn-solid" data-edit="${esc(d.uid)}">${t('dr.openEditor')}</button>
      </div>
    </div>`;
  };

  /* Flows are grouped by the segment of their domain just BELOW the active
     filter -- the outermost one with nothing filtered: a store grows one
     diagram per routine, and thirty cards in one flat grid is a list to read
     rather than a set to navigate. The heading filters one level deeper, so
     picking 'acme' turns the headings below into its modules.

     ONE card per flow, always. A flow cross-listed into three subjects used
     to be drawn under each of their branches, and three cards with one uid
     read as three records however the copies are marked; the cross-listings
     are on the card as chips, and filtering to one of them is how you see
     that subject's set. So a flow is grouped where it is FILED, and only
     falls back to a cross-listing when its own path is not in the list at
     all -- outside the active filter, or absent, which is how a purely
     cross-cutting subject still shows the routines that are steps of it. */
  const scope = domainSegments(state.domain);
  const branchOf = p => inDomainPath(p, state.domain)
    ? domainSegments(p).slice(0, scope.length + 1).join(DOMAIN_SEP)
    : null;
  /* Where the flow belongs: its own branch, and the leftover pile for one
     filed nowhere -- which is a home, not an exile, so a card sitting in the
     pile is not "cross-listed here". */
  const homeOf = d => branchOf(d.domain);
  /* `also` arrives sorted by path, so the fallback is the first cross-listing
     in scope -- the same branch on every draw, not whichever came back first.
     Nothing in scope at all leaves the card in the leftover pile: the server
     matched it, and dropping it out of the grid is worse than filing it
     loosely. */
  const groupOf = d => (d.domain ? homeOf(d) : null)
    ?? (d.also || []).map(branchOf).find(Boolean)
    ?? '';

  const grid = $('#dglGrid');
  const draw = () => {
    const q = $('#dglFilter').value.trim().toLowerCase();
    const shown = q
      ? data.items.filter(d => `${d.title} ${d.summary} ${d.domain} ${(d.also || []).join(' ')} ${d.tags}`
          .toLowerCase().includes(q))
      : data.items;
    const groups = new Map();
    for (const d of shown) {
      const k = groupOf(d);
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(d);
    }
    /* undomained last: it is the leftover pile, not a branch */
    const order = [...groups.keys()].sort((a, b) =>
      (a === '') - (b === '') || a.localeCompare(b));
    grid.innerHTML = !shown.length
      ? `<div class="empty">${data.total ? t('dgl.noMatch') : `${t('dgl.empty')}<div class="dg-empty" style="margin-top:8px">${t('dgl.emptyHint')}</div>`}</div>`
      : order.length < 2
        /* one branch draws no heading, but the cards still get told which one
           they are under: a flow only cross-listed into the filtered subject
           has to say so, or it reads as filed there */
        ? `<div class="dgl-grid">${shown.map(d => card(d, groupOf(d))).join('')}</div>`
        : order.map(k => `<section class="dgl-group">
            <div class="dgl-group-head">
              ${k ? `<button type="button" class="chip clickable" data-fdomain="${esc(k)}"
                       aria-label="${esc(t('a11y.filterDomain', { domain: k }))}">${esc(k)}</button>`
                  : `<span class="chip">${t('dgl.noDomain')}</span>`}
              <span class="dgl-group-n">${t('dgl.groupCount', { n: groups.get(k).length })}</span>
            </div>
            <div class="dgl-grid">${groups.get(k).map(d => card(d, k)).join('')}</div>
          </section>`).join('');
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
