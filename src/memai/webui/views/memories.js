/* The memory list: filters, paging, and the bulk selection bar.

   The bar is parented to document.body so it can float over the list, so
   it does NOT go away with the view's innerHTML. It is registered for
   teardown instead -- without that, selecting rows and then clicking
   another section in the rail left the bar on screen, still wired to a
   selection whose list had been replaced. */

import { $, esc, fmtInt, fmtDate, debounce } from '../core/dom.js';
import { api, query } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, promptModal } from '../core/ui.js';
import { typeTag, uidChip, statusTag, confPill, wireCopyChips, getDomains,
         TYPE_ORDER, TYPE_LABEL, CONF } from '../core/shared.js';
import { go, refreshBehind } from '../core/router.js';
import { onTeardown } from '../core/lifecycle.js';
import { openRecord } from './record.js';
import { t } from '../i18n.js';

const PAGE = 50;
const selection = new Set();

function dropBulkbar() {
  document.querySelector('.bulkbar')?.remove();
  selection.clear();
}

export async function renderMemories(view, params, ctx) {
  const state = {
    q: params.get('q') || '',
    domain: params.get('domain') || '',
    type: params.get('type') || '',
    status: params.has('status') ? params.get('status') : 'active',
    confidence: params.get('confidence') || '',
    session: params.get('session') || '',
    sort: params.get('sort') || 'created_at',
    dir: params.get('dir') || 'desc',
    page: parseInt(params.get('page') || '0', 10) || 0,
  };
  dropBulkbar();
  onTeardown(dropBulkbar);

  const domains = await getDomains().catch(() => []);
  const qs = query({
    q: state.q, domain: state.domain, type: state.type, status: state.status,
    confidence: state.confidence, session: state.session, sort: state.sort, dir: state.dir,
    limit: PAGE, offset: state.page * PAGE,
  });
  const data = await api(`/api/memories?${qs}`);
  if (ctx.stale()) return;

  const domainOpts = [`<option value="">${t('common.allDomains')}</option>`,
    ...domains.map(d => `<option value="${esc(d.domain)}" ${d.domain === state.domain ? 'selected' : ''}>${esc(d.domain)}</option>`)];
  if (state.domain && !domains.some(d => d.domain === state.domain))
    domainOpts.push(`<option value="${esc(state.domain)}" selected>${esc(state.domain)}</option>`);
  const typeOpts = [`<option value="">${t('common.allTypes')}</option>`,
    ...TYPE_ORDER.map(tp => `<option value="${tp}" ${tp === state.type ? 'selected' : ''}>${TYPE_LABEL[tp]}</option>`)];

  view.innerHTML = `<div class="anim">
    <div class="view-head"><h2 class="view-title">${t('mem.title')}</h2>
      <div class="view-sub">${t('mem.sub')}</div></div>

    <div class="list-toolbar">
      <input id="fQ" type="search" placeholder="${t('mem.search.placeholder')}" value="${esc(state.q)}" spellcheck="false">
      <select id="fType">${typeOpts.join('')}</select>
      <select id="fDomain">${domainOpts.join('')}</select>
      <div class="seg" id="fStatus">
        <button data-v="active" class="${state.status === 'active' ? 'active' : ''}">${t('common.active')}</button>
        <button data-v="archived" class="${state.status === 'archived' ? 'active' : ''}">${t('common.archived')}</button>
        <button data-v="" class="${state.status === '' ? 'active' : ''}">${t('common.all')}</button>
      </div>
      <select id="fConf">
        <option value="">${t('mem.conf.all')}</option>
        ${Object.keys(CONF).map(c => `<option value="${c}" ${c === state.confidence ? 'selected' : ''}>${CONF[c].label}</option>`).join('')}
      </select>
      ${data.searched ? '' : `<select id="fSort">
        <option value="created_at:desc" ${state.sort === 'created_at' && state.dir === 'desc' ? 'selected' : ''}>${t('mem.sort.newest')}</option>
        <option value="created_at:asc" ${state.sort === 'created_at' && state.dir === 'asc' ? 'selected' : ''}>${t('mem.sort.oldest')}</option>
        <option value="updated_at:desc" ${state.sort === 'updated_at' ? 'selected' : ''}>${t('mem.sort.updated')}</option>
      </select>`}
      ${state.session ? `<span class="chip clickable" id="fSession" title="${t('mem.session.title')}">${t('mem.session.chip', { s: esc(state.session.slice(0, 18)) })}${icon('close')}</span>` : ''}
    </div>

    <div class="mem-list" id="memList">${renderRows(data.items)}</div>

    <div class="list-foot">
      <span>${data.searched
        ? t('mem.results', { n: fmtInt(data.total), q: esc(state.q) })
        : t('mem.range', { a: fmtInt(state.page * PAGE + Math.min(1, data.items.length)), b: fmtInt(state.page * PAGE + data.items.length), c: fmtInt(data.total) })}</span>
      <span class="pager">
        <button class="btn btn-sm" id="pgPrev" ${state.page === 0 ? 'disabled' : ''}>${icon('chevron-left')}${t('mem.prev')}</button>
        <button class="btn btn-sm" id="pgNext" ${(state.page + 1) * PAGE >= data.total ? 'disabled' : ''}>${t('mem.next')}${icon('chevron-right')}</button>
      </span>
    </div>
  </div>`;

  /* The URL carries only what differs from the defaults: status=active is
     the default so it stays out, status= (empty -- "all") has to be
     written explicitly to override that default, and page 0 is implied. */
  const navigate = patch => {
    const p = { ...state, ...patch };
    const out = {};
    for (const [k, v] of Object.entries(p)) if (v !== '' && v != null) out[k] = v;
    delete out.page;
    if (p.page) out.page = p.page;
    if (p.status === 'active') delete out.status;
    else out.status = p.status || '';
    go('memories', out);
  };

  $('#fQ').addEventListener('keydown', e => { if (e.key === 'Enter') navigate({ q: e.target.value.trim(), page: 0 }); });
  $('#fQ').addEventListener('input', debounce(e => {
    if (e.target.value.trim() === '' && state.q) navigate({ q: '', page: 0 });
  }, 500));
  const fType = $('#fType');
  if (fType) fType.addEventListener('change', e => navigate({ type: e.target.value, page: 0 }));
  $('#fDomain').addEventListener('change', e => navigate({ domain: e.target.value, page: 0 }));
  $('#fConf').addEventListener('change', e => navigate({ confidence: e.target.value, page: 0 }));
  const fSort = $('#fSort');
  if (fSort) fSort.addEventListener('change', e => {
    const [sort, dir] = e.target.value.split(':');
    navigate({ sort, dir, page: 0 });
  });
  $('#fStatus').querySelectorAll('button').forEach(b =>
    b.addEventListener('click', () => navigate({ status: b.dataset.v, page: 0 })));
  const fSession = $('#fSession');
  if (fSession) fSession.addEventListener('click', () => navigate({ session: '', page: 0 }));
  $('#pgPrev').addEventListener('click', () => navigate({ page: state.page - 1 }));
  $('#pgNext').addEventListener('click', () => navigate({ page: state.page + 1 }));

  const list = $('#memList');
  wireCopyChips(list);
  list.querySelectorAll('.mem-row').forEach(row => {
    row.addEventListener('click', () => openRecord(row.dataset.uid));
    const cb = row.querySelector('input[type=checkbox]');
    cb.addEventListener('click', e => {
      e.stopPropagation();
      cb.checked ? selection.add(row.dataset.uid) : selection.delete(row.dataset.uid);
      row.classList.toggle('selected', cb.checked);
      renderBulkbar();
    });
  });
  renderBulkbar();
}

function renderRows(items) {
  if (!items.length) return `<div class="empty">${t('mem.empty')}</div>`;
  return items.map(m => {
    const tags = (m.tags || '').split(',').map(s => s.trim()).filter(Boolean).slice(0, 5);
    const match = m.match_source
      ? `<span class="match-badge" title="${m.fts_rank !== undefined ? `bm25 ${Number(m.fts_rank).toFixed(2)} ` : ''}${m.vec_distance !== undefined ? `cos ${Number(m.vec_distance).toFixed(3)}` : ''}">${esc(m.match_source)}</span>` : '';
    return `<div class="mem-row" data-uid="${esc(m.uid)}">
      <div class="mem-check"><input type="checkbox" aria-label="${t('mem.select.aria', { uid: esc(m.uid) })}"></div>
      <div class="mem-col-type">${typeTag(m.type)}${uidChip(m.uid)}${statusTag(m.status)}</div>
      <div class="mem-main">
        <div class="mem-snippet">${esc(m.content)}</div>
        ${tags.length || m.domain ? `<div class="mem-tags">
          ${m.domain ? `<span class="chip">${esc(m.domain)}</span>` : ''}
          ${tags.map(tg => `<span class="chip" style="color:var(--ink-3)">#${esc(tg)}</span>`).join('')}
        </div>` : ''}
      </div>
      <div class="mem-right">
        ${match}
        ${confPill(m.confidence)}
        <span title="${esc(m.created_at)}">${fmtDate(m.created_at)}</span>
        ${m.content_len > 300 ? `<span style="color:var(--ink-4)">${fmtInt(m.content_len)} ${t('common.chars')}</span>` : ''}
      </div>
    </div>`;
  }).join('');
}

function renderBulkbar() {
  document.querySelector('.bulkbar')?.remove();
  if (!selection.size) return;
  const bar = document.createElement('div');
  bar.className = 'bulkbar';
  bar.innerHTML = `
    <span>${t('bulk.selected', { n: selection.size })}</span>
    <select id="bulkConf">
      <option value="">${t('bulk.setConf')}</option>
      ${Object.keys(CONF).map(c => `<option value="${c}">${CONF[c].label}</option>`).join('')}
    </select>
    <button class="btn btn-sm" id="bulkArch">${t('common.archive')}</button>
    <button class="btn btn-sm" id="bulkRest">${t('common.restore')}</button>
    <button class="icon-btn" id="bulkClear" title="${t('bulk.clear.title')}">${icon('close')}</button>`;
  document.body.appendChild(bar);

  bar.querySelector('#bulkConf').addEventListener('change', async e => {
    if (!e.target.value) return;
    await runBulk({ action: 'confidence', value: e.target.value });
  });
  bar.querySelector('#bulkArch').addEventListener('click', async () => {
    const reason = await promptModal({
      title: t('bulk.archive.title'),
      body: t('bulk.archive.body', { n: selection.size }),
      label: t('bulk.reason.label'), okLabel: t('common.archive'), danger: true });
    if (reason === null) return;
    await runBulk({ action: 'archive', reason });
  });
  bar.querySelector('#bulkRest').addEventListener('click', () => runBulk({ action: 'restore' }));
  bar.querySelector('#bulkClear').addEventListener('click', () => {
    selection.clear(); renderBulkbar(); refreshBehind();
  });
}

async function runBulk(body) {
  try {
    const r = await api('/api/bulk', { body: { ...body, uids: [...selection] } });
    toast(t('bulk.updated', { n: r.affected }), 'ok');
    selection.clear();
    refreshBehind();
  } catch (err) { toast(err.message, 'bad'); }
}
