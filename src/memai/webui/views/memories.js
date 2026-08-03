/* The memory list: filters, paging, and the bulk selection bar.

   The bar is parented to document.body so it can float over the list, so
   it does NOT go away with the view's innerHTML. It is registered for
   teardown instead -- without that, selecting rows and then clicking
   another section in the rail left the bar on screen, still wired to a
   selection whose list had been replaced. */

import { $, esc, fmtInt, fmtDate, debounce } from '../core/dom.js';
import { api, query } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, failed, promptModal } from '../core/ui.js';
import { typeTag, statusTag, confPill, getDomains, domainOptions,
         inDomainPath, TYPE_ORDER, TYPE_LABEL, CONF } from '../core/shared.js';
import { go, refreshBehind } from '../core/router.js';
import { onTeardown } from '../core/lifecycle.js';
import { openRecord } from './record.js';
import { t } from '../i18n.js';

const PAGE = 50;
const selection = new Set();

/* `/` from anywhere in the app asks for this view's search field. When the
   view is not up yet there is nothing to focus, so the request is held and
   the next render honours it -- which is why this returns whether it could
   act: app.js navigates here only when it could not. */
let wantsCaret = false;

export function focusMemorySearch() {
  const el = document.getElementById('fQ');
  if (!el) { wantsCaret = true; return false; }
  el.focus();
  el.select();
  return true;
}

/* The toast stack has to clear this bar, so the bar publishes its own height
   instead of the stylesheet guessing at it -- at 375px it wraps to two rows,
   and a toast used to land squarely on top of Archive / Restore / clear. */
let bulkSize = null;

function publishBulkHeight(bar) {
  document.documentElement.style.setProperty('--bulk-h', bar ? `${bar.offsetHeight + 10}px` : '0px');
}

/* Called three ways: with the bar, with nothing, and as a teardown callback
   (which may hand it an argument of its own), hence the instanceof rather than
   a default parameter. */
function dropBulkbar(bar) {
  bulkSize?.disconnect();
  bulkSize = null;
  (bar instanceof HTMLElement ? bar : document.querySelector('.bulkbar'))?.remove();
  publishBulkHeight(null);
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
    /* a domain filter covers its subdomains; 'exact' is the opt-out, and it
       lives in the URL so the narrowed list is a linkable state */
    exact: params.get('exact') || '',
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
    subtree: state.exact ? '0' : '',
    limit: PAGE, offset: state.page * PAGE,
  });
  const data = await api(`/api/memories?${qs}`);
  if (ctx.stale()) return;

  const domainOpts = [`<option value="">${t('common.allDomains')}</option>`,
    domainOptions(domains, state.domain)];
  if (state.domain && !domains.some(d => d.domain === state.domain))
    domainOpts.push(`<option value="${esc(state.domain)}" selected>${esc(state.domain)}</option>`);
  const kids = domains.find(d => d.domain === state.domain)?.children;
  const typeOpts = [`<option value="">${t('common.allTypes')}</option>`,
    ...TYPE_ORDER.map(tp => `<option value="${tp}" ${tp === state.type ? 'selected' : ''}>${TYPE_LABEL[tp]}</option>`)];

  view.innerHTML = `<div class="anim">
    <div class="view-head"><h2 class="view-title">${t('mem.title')}</h2>
      <div class="view-sub">${t('mem.sub')}</div></div>

    <div class="list-toolbar">
      <input id="fQ" type="search" placeholder="${t('mem.search.placeholder')}" value="${esc(state.q)}" spellcheck="false">
      <!-- the app's one remaining accelerator, taught where it lands -->
      <kbd class="toolbar-kbd" aria-hidden="true">/</kbd>
      <select id="fType">${typeOpts.join('')}</select>
      <select id="fDomain">${domainOpts.join('')}</select>
      <!-- only where the choice exists: a domain with no subdomains reads
           the same either way, and an inert toggle is noise -->
      ${kids ? `<button type="button" class="chip clickable" id="fExact" aria-pressed="${Boolean(state.exact)}"
           title="${esc(t('mem.subtree.title'))}">${t(state.exact ? 'mem.subtree.exact' : 'mem.subtree.incl')}</button>` : ''}
      <!-- the filter resolved a name that was only the deep end of a path;
           showing the rows without saying so would claim a filter that was
           never run -->
      ${data.domain_scope ? `<span class="chip" title="${esc(t('mem.scope.title'))}">${
        esc(t('mem.scope.resolved', { list: data.domain_scope.join(', ') }))}</span>` : ''}
      <div class="seg" id="fStatus" role="group" aria-label="${t('mem.status.aria')}">
        <button type="button" data-v="active" aria-pressed="${state.status === 'active'}">${t('common.active')}</button>
        <button type="button" data-v="archived" aria-pressed="${state.status === 'archived'}">${t('common.archived')}</button>
        <button type="button" data-v="" aria-pressed="${state.status === ''}">${t('common.all')}</button>
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
      ${state.session ? `<button type="button" class="chip clickable" id="fSession" title="${t('mem.session.title')}">${t('mem.session.chip', { s: esc(state.session.slice(0, 18)) })}${icon('close')}</button>` : ''}
    </div>

    <div class="mem-list" id="memList">${renderRows(data.items, state.domain)}</div>

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

  if (wantsCaret) { wantsCaret = false; $('#fQ').focus(); $('#fQ').select(); }

  $('#fQ').addEventListener('keydown', e => { if (e.key === 'Enter') navigate({ q: e.target.value.trim(), page: 0 }); });
  $('#fQ').addEventListener('input', debounce(e => {
    if (e.target.value.trim() === '' && state.q) navigate({ q: '', page: 0 });
  }, 500));
  const fType = $('#fType');
  if (fType) fType.addEventListener('change', e => navigate({ type: e.target.value, page: 0 }));
  /* a new scope starts inclusive: 'exact' was about the domain just left */
  $('#fDomain').addEventListener('change', e =>
    navigate({ domain: e.target.value, exact: '', page: 0 }));
  const fExact = $('#fExact');
  if (fExact) fExact.addEventListener('click', () =>
    navigate({ exact: state.exact ? '' : '1', page: 0 }));
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
  list.querySelectorAll('.mem-row').forEach(row => {
    row.addEventListener('click', () => openRecord(row.dataset.uid));
    const cb = row.querySelector('input[type=checkbox]');
    cb.addEventListener('click', e => {
      e.stopPropagation();
      cb.checked ? selection.add(row.dataset.uid) : selection.delete(row.dataset.uid);
      row.classList.toggle('selected', cb.checked);
      syncBulkbar();
    });
  });
  syncBulkbar();
}

/* `scope` is the active domain filter, needed to tell a row that LIVES in it
   from one that is only cross-listed into it. A list that showed both the
   same way would be claiming the second is filed where it is not. */
function renderRows(items, scope = '') {
  if (!items.length) return `<div class="empty">${t('mem.empty')}</div>`;
  return items.map(m => {
    const away = Boolean(scope) && !inDomainPath(m.domain, scope)
      && (m.also || []).some(p => inDomainPath(p, scope));
    const match = m.match_source
      ? `<span class="match-badge" title="${m.fts_rank !== undefined ? `bm25 ${Number(m.fts_rank).toFixed(2)} ` : ''}${m.vec_distance !== undefined ? `cos ${Number(m.vec_distance).toFixed(3)}` : ''}">${esc(m.match_source)}</span>` : '';
    /* The row keeps its click for the mouse, but the thing that OPENS the
       record is a real button around the snippet -- the row itself cannot be
       one, because it already contains a checkbox and a copy button and a
       control inside a control is a control neither the keyboard nor a
       screen reader can make sense of. Enter on the button bubbles a click
       to the row, so there is still exactly one handler. */
    return `<div class="mem-row" data-uid="${esc(m.uid)}">
      <div class="mem-check"><input type="checkbox" aria-label="${t('mem.select.aria', { uid: esc(m.uid) })}"></div>
      <!-- Confidence leads this column. It used to be the second of four
           whispers stacked in .mem-right, at 60% white, quieter than the uid
           beside it -- in a store whose whole point is that a human vets what
           an agent wrote, the vetting was the faintest thing in the row. -->
      <div class="mem-col-type">${confPill(m.confidence, true)}${typeTag(m.type)}</div>
      <div class="mem-main">
        <button type="button" class="row-open mem-snippet"
                aria-label="${esc(t('a11y.openRecord', { uid: m.uid }))}">${esc(m.content)}</button>
      </div>
      <div class="mem-right">
        ${match}
        ${statusTag(m.status)}
        ${away ? `<span class="chip" title="${esc(t('mem.alsoWhy', { domain: m.domain }))}">${t('mem.also')}</span>` : ''}
        ${m.domain ? `<span class="chip">${esc(m.domain)}</span>` : ''}
        <span title="${esc(m.created_at)}">${fmtDate(m.created_at)}</span>
      </div>
    </div>`;
  }).join('');
}

/* Built once, then only its count is written. It used to be removed and
   recreated on every checkbox, which replayed its entrance animation each
   time and threw away the focus of whoever was tabbing through it. */
function syncBulkbar() {
  const existing = document.querySelector('.bulkbar');
  if (!selection.size) { dropBulkbar(existing); return; }
  if (existing) {
    /* innerHTML, because bulk.selected marks the number up -- textContent
       here printed the <b> tags as text on every toggle after the first */
    existing.querySelector('[data-count]').innerHTML = t('bulk.selected', { n: selection.size });
    return;
  }
  const bar = document.createElement('div');
  bar.className = 'bulkbar';
  bar.setAttribute('role', 'toolbar');
  bar.setAttribute('aria-label', t('bulk.aria'));
  bar.innerHTML = `
    <span data-count>${t('bulk.selected', { n: selection.size })}</span>
    <select id="bulkConf" aria-label="${t('bulk.setConf')}">
      <option value="">${t('bulk.setConf')}</option>
      ${Object.keys(CONF).map(c => `<option value="${c}">${CONF[c].label}</option>`).join('')}
    </select>
    <button type="button" class="btn btn-sm" id="bulkArch">${t('common.archive')}</button>
    <button type="button" class="btn btn-sm" id="bulkRest">${t('common.restore')}</button>
    <button type="button" class="icon-btn" id="bulkClear"
            title="${t('bulk.clear.title')}" aria-label="${t('bulk.clear.title')}">${icon('close')}</button>`;
  document.body.appendChild(bar);
  publishBulkHeight(bar);
  bulkSize = new ResizeObserver(() => publishBulkHeight(bar));
  bulkSize.observe(bar);

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
  /* Clearing a selection changes no data, so it unticks the boxes in place.
     It used to call refreshBehind(), which re-ran the whole route -- a fetch
     and a full repaint to undo three checkboxes. */
  bar.querySelector('#bulkClear').addEventListener('click', () => {
    selection.clear();
    document.querySelectorAll('#memList .mem-row').forEach(row => {
      row.querySelector('input[type=checkbox]').checked = false;
      row.classList.remove('selected');
    });
    syncBulkbar();
  });
}

async function runBulk(body) {
  /* captured before the selection is cleared, so the Undo below acts on exactly
     the set that was archived and not on whatever is ticked by then */
  const uids = [...selection];
  try {
    const r = await api('/api/bulk', { body: { ...body, uids } });
    /* Archiving fifty rows behind a single confirm was a one-way door. Restore
       over the same set is the exact inverse, so it is offered rather than
       leaving you to find those fifty rows again. The reverse direction gets no
       Undo -- see the note on the record's Restore in record.js. */
    toast(t('bulk.updated', { n: r.affected }), 'ok', body.action === 'archive' ? {
      action: {
        label: t('common.undo'),
        run: () => api('/api/bulk', { body: { action: 'restore', uids } })
          .then(() => { toast(t('bulk.undone', { n: uids.length }), 'ok'); refreshBehind(); })
          .catch(err => failed('err.bulk', err)),
      },
    } : {});
    selection.clear();
    refreshBehind();   /* the rows themselves changed, so the list is refetched */
  } catch (err) { failed('err.bulk', err); }
}
