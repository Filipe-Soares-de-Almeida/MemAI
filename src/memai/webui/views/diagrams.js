/* The diagram list: one row per documented flow, and the inspector beside it.

   A row carries what decides whether to open a flow -- whether its shape is
   sound, what it is called, how big it is and how long ago it changed. The
   summary, the counts and where the flow is filed are in the pane on the
   right, which follows the caret, and the two things you can do to a flow
   are its footer.

   Filtering is client-side: the server sends the whole set for the domain
   and status asked for, and the point of the view is to scan it. */

import { $, esc, fmtInt, fmtAgo } from '../core/dom.js';
import { api } from '../core/api.js';
import { icon } from '../core/icons.js';
import { failed, promptModal } from '../core/ui.js';
import { statusTag, uidChip, wireCopyChips, getDomains,
         invalidateDomains } from '../core/shared.js';
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

/* Worst first, so a row and the pane name the faults in the same order. */
const sortIssues = d => d.issues.slice().sort(
  (a, b) => ISSUE_ORDER.indexOf(a.kind) - ISSUE_ORDER.indexOf(b.kind));

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

/* How far a flow is explained, with the note that says what "explained"
   means -- the one number in the row whose name is not obvious. */
const explained = d => `<span title="${esc(t('dgl.documentedWhy'))}">${
  t('dgl.documented', { n: fmtInt(d.documented), total: fmtInt(d.nodes) })}</span>`;

const stat = (key, n, why = '') => `<div class="dgl-stat"${why ? ` title="${esc(why)}"` : ''}>
  <span class="mg-label">${t(key)}</span>
  <span class="dgl-stat-n">${fmtInt(n)}</span>
</div>`;

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

  /* Every row on screen, by uid: the inspector's whole source of truth, so
     nothing it shows costs a request. */
  const byUid = new Map(data.items.map(d => [d.uid, d]));

  view.innerHTML = `<div class="dgl-shell">
    <h2 class="sr-only">${t('dgl.title')}</h2>

    <div class="dgl-work">
      <div class="dgl-pane">
        <div class="list-toolbar">
          ${domainPickerHTML({ id: 'dglDomain', value: state.domain,
                               ariaLabel: t('common.allDomains') })}
          <div class="seg" id="dglStatus" role="group" aria-label="${t('mem.status.aria')}">
            <button type="button" data-v="active" aria-pressed="${state.status === 'active'}">${t('common.active')}</button>
            <button type="button" data-v="" aria-pressed="${state.status === ''}">${t('common.all')}</button>
          </div>
          <input type="search" id="dglFilter" placeholder="${t('dgl.filter')}"
                 aria-label="${t('dgl.filter')}" autocomplete="off" spellcheck="false">
          <button class="btn btn-solid" id="dglNew">${t('dgl.new')}</button>
        </div>

        <div class="dgl-list" id="dglList"></div>
      </div>

      <!-- No aria-live: the pane mirrors the row the caret is on, and the
           row announces itself as it takes focus. A live region here reads
           the same flow out twice for every press of an arrow key. -->
      <aside class="dgl-ins" id="dglIns"></aside>
    </div>
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

  /* The header strip is a sibling of the rows and not the first of them: it
     names the columns under it, and a listbox option is not what a column
     head is. Its count is the count of what is SHOWN, so it tracks the
     filter as it is typed. */
  const head = shown => {
    const broken = shown.filter(d => d.issues.length).length;
    return `<div class="dgl-head">
      <span></span>
      <span>${t('dgl.count', { n: fmtInt(shown.length) })}${
        broken ? ` · <span class="dgl-broken">${t('dgl.subIssues', { n: fmtInt(broken) })}</span>` : ''}</span>
      <span>${t('dgl.colUpdated')}</span>
    </div>`;
  };

  /* A sound flow's row says how big it is; a broken one says what is wrong
     with it instead -- the counts are in the pane, the fault is the reason
     this view exists. The names alone here: their explanation and the steps
     they name are the chips in the pane. */
  const row = d => {
    const issues = sortIssues(d);
    const sub = issues.length
      ? `${issues.map(i => esc(t(`dg.issue.${i.kind}`))).join(' · ')} · ${explained(d)}`
      : `${t('dgl.steps', { n: fmtInt(d.nodes) })} · ${t('dgl.conns', { n: fmtInt(d.edges) })} · ${explained(d)}`;
    return `<div class="dgl-row" role="option" aria-selected="false" tabindex="-1"
                 data-uid="${esc(d.uid)}">
      <span class="dgl-state${issues.length ? ' is-broken' : ''}">${
        icon(issues.length ? 'unverified' : 'confirmed')}${
        issues.length ? '' : `<span class="sr-only">${t('dgl.sound')}</span>`}</span>
      <span class="dgl-main">
        <span class="dgl-name" title="${esc(d.title || '')}">${esc(d.title || '—')}</span>
        <span class="dgl-sub${issues.length ? ' is-broken' : ''}">${sub}</span>
      </span>
      <span class="dgl-right">${statusTag(d.status)}<span class="dgl-when"
            title="${esc(d.updated_at)}">${fmtAgo(d.updated_at)}</span></span>
    </div>`;
  };

  const ins = $('#dglIns');
  const paintIns = uid => {
    const d = byUid.get(uid);
    ins.classList.toggle('is-empty', !d);
    if (!d) {
      ins.innerHTML = `<div class="dgl-ins-empty">${t('dgl.pickHint')}</div>`;
      return;
    }
    const issues = sortIssues(d);
    ins.innerHTML = `
      <div class="dgl-ins-head">
        <span class="dgl-ins-title">${esc(d.title || '—')}</span>
        <div class="dgl-ins-marks">
          ${issues.length ? issues.map(issueChip).join('')
            : `<span class="dgl-sound">${icon('confirmed')}${t('dgl.sound')}</span>`}
          ${uidChip(d.uid)}
        </div>
      </div>
      <div class="dgl-ins-body">
        <div class="dgl-ins-stats">
          ${stat('dgl.stat.steps', d.nodes)}
          ${stat('dgl.stat.conns', d.edges)}
          ${stat('dgl.stat.linked', d.links)}
          <!-- counted from both ends: a flow nothing leaves but three arrive
               into is as tied into the set as the one that made those jumps -->
          ${stat('dgl.stat.jumps', d.jumps, t('dgl.jumpsWhy'))}
        </div>
        <div class="dgl-ins-field">
          <span class="mg-label">${t('dgl.summary')}</span>
          ${d.summary ? `<p class="dgl-ins-text">${esc(d.summary)}</p>`
            : `<span class="hint">${t('dgl.noSummary')}</span>`}
        </div>
        <div class="dgl-ins-field">
          <span class="mg-label">${t('dgl.filed')}</span>
          <span class="dgl-path">${d.domain ? esc(d.domain) : t('dgl.noDomain')}</span>
        </div>
      </div>
      <div class="dgl-ins-foot">
        <button class="btn btn-sm" data-record="${esc(d.uid)}">${t('dg.record')}</button>
        <button class="btn btn-sm btn-solid" data-edit="${esc(d.uid)}">${t('dr.openEditor')}</button>
      </div>`;
    wireCopyChips(ins);
    ins.querySelector('[data-record]').addEventListener('click', () => openRecord(d.uid));
    ins.querySelector('[data-edit]').addEventListener('click', () => go('diagram', { uid: d.uid }));
  };

  const list = $('#dglList');
  const draw = () => {
    const q = $('#dglFilter').value.trim().toLowerCase();
    const shown = q
      ? data.items.filter(d => `${d.title} ${d.summary} ${d.domain} ${(d.also || []).join(' ')} ${d.tags}`
          .toLowerCase().includes(q))
      : data.items;
    list.innerHTML = !shown.length
      ? `<div class="empty">${data.total ? t('dgl.noMatch')
          : `${t('dgl.empty')}<div class="dg-empty" style="margin-top:8px">${t('dgl.emptyHint')}</div>`}</div>`
      : `${head(shown)}
         <div id="dglRows" role="listbox" aria-label="${t('dgl.title')}">${
           shown.map(row).join('')}</div>`;

    const rows = [...list.querySelectorAll('.dgl-row')];
    /* Roving tabindex: the list is ONE tab stop and the arrows move inside
       it. Selection follows the caret -- there is one flow in the pane and
       landing on a row is how it is chosen -- so the row the caret is on and
       the row the pane is showing are the same fact, written once. */
    let cursor = -1;
    const setCursor = (i, { focus = false } = {}) => {
      if (i < 0 || i >= rows.length) return;
      rows.forEach((r, n) => {
        r.tabIndex = n === i ? 0 : -1;
        r.setAttribute('aria-selected', n === i ? 'true' : 'false');
      });
      if (focus) rows[i].focus();
      if (i === cursor) return;
      cursor = i;
      paintIns(rows[i].dataset.uid);
    };
    /* The first row is picked on arrival: the pane's job is to show a flow,
       and an empty pane beside a full list says nothing. */
    setCursor(0);

    rows.forEach((r, i) => {
      r.addEventListener('click', () => setCursor(i));
      r.addEventListener('dblclick', () => go('diagram', { uid: r.dataset.uid }));
    });
    const rowBox = $('#dglRows');
    if (rowBox) rowBox.addEventListener('keydown', e => {
      const r = e.target.closest('.dgl-row');
      if (!r) return;
      const i = rows.indexOf(r);
      const step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
      if (step !== undefined) {
        e.preventDefault();
        setCursor(Math.min(rows.length - 1, Math.max(0, i + step)), { focus: true });
        return;
      }
      if (e.key === 'Home' || e.key === 'End') {
        e.preventDefault();
        setCursor(e.key === 'Home' ? 0 : rows.length - 1, { focus: true });
        return;
      }
      /* Enter opens the editor and not the record: the flow's shape is what
         this view is about, and the record is the pane's other button. */
      if (e.key === 'Enter') { e.preventDefault(); go('diagram', { uid: r.dataset.uid }); }
    });
    if (!rows.length) paintIns('');
  };

  /* Down out of the filter lands in the list, so finding a flow and opening
     it is one uninterrupted keyboard path. */
  $('#dglFilter').addEventListener('keydown', e => {
    if (e.key !== 'ArrowDown') return;
    const first = list.querySelector('.dgl-row[tabindex="0"]') || list.querySelector('.dgl-row');
    if (!first) return;
    e.preventDefault();
    first.focus();
  });
  $('#dglFilter').addEventListener('input', draw);
  draw();
}
