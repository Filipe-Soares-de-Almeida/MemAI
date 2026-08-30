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
import { typeTag, statusTag, confPill, getDomains, inDomainPath,
         typeItems, confItems } from '../core/shared.js';
import { pickerHTML, pickerFor, wirePicker, fixedItems } from '../core/pick.js';
import { domainPickerHTML, wireDomainPicker } from '../core/domain-picker.js';
import { go, refreshBehind } from '../core/router.js';
import { onTeardown } from '../core/lifecycle.js';
import { openRecord, setRecordSequence } from './record.js';
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

/* The one place that writes a row's selected-ness. The tick, the row's own
   wash, the state a screen reader reads off the row, and the set the bulk bar
   acts on are four faces of one fact, and four call sites used to each set
   the ones they happened to remember. */
function selectRow(row, on) {
  row.querySelector('input[type=checkbox]').checked = on;
  row.classList.toggle('selected', on);
  row.setAttribute('aria-selected', on ? 'true' : 'false');
  if (on) selection.add(row.dataset.uid); else selection.delete(row.dataset.uid);
}

/* The header box reports as much as it commands: ticked when the whole page
   is in the selection, dashed when only part of it is -- so it never claims
   to have selected rows that a range or a stray click left out. */
function syncSelectAll() {
  const box = document.getElementById('memAll');
  if (!box) return;
  const rows = [...document.querySelectorAll('#memList .mem-row')];
  const n = rows.filter(r => selection.has(r.dataset.uid)).length;
  box.checked = Boolean(n) && n === rows.length;
  box.indeterminate = Boolean(n) && n < rows.length;
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

  const kids = domains.find(d => d.domain === state.domain)?.children;
  const types = typeItems({ any: t('common.allTypes') });
  const confs = confItems({ any: t('mem.conf.all') });
  const sorts = [
    { value: 'created_at:desc', label: t('mem.sort.newest') },
    { value: 'created_at:asc', label: t('mem.sort.oldest') },
    { value: 'updated_at:desc', label: t('mem.sort.updated') },
    /* What the store is actually living on, and what it is carrying. Least
       recalled puts the never-recalled rows first, which is where a
       curation pass starts. */
    { value: 'recalls:desc', label: t('mem.sort.used') },
    { value: 'recalls:asc', label: t('mem.sort.unused') },
  ];
  /* sort and dir arrive from the URL and can name a pair no option offers
     (created_at:desc is the only combination with both directions). Fall
     back rather than render a picker with nothing selected. */
  const sortPair = `${state.sort}:${state.dir}`;
  const activeSort = sorts.some(s => s.value === sortPair) ? sortPair : sorts[0].value;

  view.innerHTML = `<div class="anim">
    <div class="view-head"><h2 class="view-title">${t('mem.title')}</h2>
      <div class="view-sub">${t('mem.sub')}</div></div>

    <div class="list-toolbar">
      <input id="fQ" type="search" placeholder="${t('mem.search.placeholder')}" value="${esc(state.q)}" spellcheck="false">
      <!-- the app's one remaining accelerator, taught where it lands -->
      <kbd class="toolbar-kbd" aria-hidden="true">/</kbd>
      <!-- Pickers, not selects (core/pick.js): a type keeps its colour and a
           confidence its ring in the list where you choose one, and a domain
           keeps the tree it is. -->
      ${pickerFor({ id: 'fType', value: state.type, items: types, ariaLabel: t('common.allTypes') })}
      ${domainPickerHTML({ id: 'fDomain', value: state.domain, ariaLabel: t('common.allDomains') })}
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
      ${pickerFor({ id: 'fConf', value: state.confidence, items: confs, ariaLabel: t('mem.conf.all') })}
      ${data.searched ? '' : pickerFor({ id: 'fSort', items: sorts, ariaLabel: t('mem.sort.aria'),
        value: activeSort })}
      ${state.session ? `<button type="button" class="chip clickable" id="fSession" title="${t('mem.session.title')}">${t('mem.session.chip', { s: esc(state.session.slice(0, 18)) })}${icon('close')}</button>` : ''}
    </div>

    <!-- The header strip is a sibling of the rows and not the first of them:
         a select-all is a control OVER the list, and putting it inside the
         grid would have made it a row you can arrow onto and try to open. -->
    <div class="mem-list">
      ${data.items.length ? `<div class="mem-head">
        <div class="mem-check"><input type="checkbox" id="memAll" aria-label="${t('mem.selectAll.aria')}"></div>
        <label class="mem-head-label" for="memAll">${t('mem.selectAll', { n: data.items.length })}</label>
        <div class="mem-head-keys" aria-hidden="true">${t('mem.keys.hint')}</div>
      </div>` : ''}
      <!-- role="grid" and not listbox: a row owns a checkbox and an open
           button, which an option is not allowed to contain. The grid is the
           role that expects widgets in its cells, and it is what licenses the
           roving tabindex the wiring below installs. -->
      <div id="memList"${data.items.length ? ` role="grid" aria-multiselectable="true" aria-label="${t('mem.title')}"` : ''}>${renderRows(data.items, state.domain)}</div>
    </div>

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
  wirePicker(view, { id: 'fType', items: fixedItems(types),
                     onPick: type => navigate({ type, page: 0 }) });
  /* a new scope starts inclusive: 'exact' was about the domain just left */
  wireDomainPicker(view, {
    id: 'fDomain', domains,
    onPick: domain => navigate({ domain, exact: '', page: 0 }),
  });
  const fExact = $('#fExact');
  if (fExact) fExact.addEventListener('click', () =>
    navigate({ exact: state.exact ? '' : '1', page: 0 }));
  wirePicker(view, { id: 'fConf', items: fixedItems(confs),
                     onPick: confidence => navigate({ confidence, page: 0 }) });
  wirePicker(view, { id: 'fSort', items: fixedItems(sorts), onPick: v => {
    const [sort, dir] = v.split(':');
    navigate({ sort, dir, page: 0 });
  } });
  $('#fStatus').querySelectorAll('button').forEach(b =>
    b.addEventListener('click', () => navigate({ status: b.dataset.v, page: 0 })));
  const fSession = $('#fSession');
  if (fSession) fSession.addEventListener('click', () => navigate({ session: '', page: 0 }));
  $('#pgPrev').addEventListener('click', () => navigate({ page: state.page - 1 }));
  $('#pgNext').addEventListener('click', () => navigate({ page: state.page + 1 }));

  const list = $('#memList');
  const rows = [...list.querySelectorAll('.mem-row')];

  /* What the record steps through when it is opened from here: this page, in
     the order it is shown. Cleared on the way out, so a record opened from
     somewhere else does not inherit a list that is no longer on screen. */
  setRecordSequence(rows.map(r => r.dataset.uid));
  onTeardown(() => setRecordSequence([]));

  /* Roving tabindex: the list is ONE tab stop and the arrows move inside it.
     `cursor` is which row currently holds that stop. */
  let cursor = 0;
  const setCursor = i => {
    if (i < 0 || i >= rows.length || i === cursor) return;
    rows[cursor].tabIndex = -1;
    cursor = i;
    rows[i].tabIndex = 0;
  };
  const moveTo = i => {
    if (i < 0 || i >= rows.length) return;
    setCursor(i);
    rows[i].focus();
  };
  rows.forEach((row, i) => { row.tabIndex = i ? -1 : 0; });
  /* a click lands the caret on the row (or on a control inside it), and the
     tab stop follows it -- otherwise Tab would come back to row 1 */
  list.addEventListener('focusin', e => {
    const row = e.target.closest('.mem-row');
    if (row) setCursor(rows.indexOf(row));
  });

  /* Where a range starts: the last row ticked deliberately. Shift runs from
     there to wherever it lands, and only ever writes the run it covers -- a
     range that also cleared what was already ticked would silently throw away
     picks made before it. */
  let anchor = 0;
  const toggle = (i, on = !selection.has(rows[i].dataset.uid)) => {
    selectRow(rows[i], on);
    anchor = i;
    syncBulkbar();
  };
  const range = (to, on) => {
    const [a, b] = anchor <= to ? [anchor, to] : [to, anchor];
    for (let i = a; i <= b; i++) selectRow(rows[i], on);
    syncBulkbar();
  };
  const setAll = on => {
    rows.forEach(row => selectRow(row, on));
    anchor = 0;
    syncBulkbar();
  };

  const memAll = $('#memAll');
  if (memAll) memAll.addEventListener('change', () => setAll(memAll.checked));

  rows.forEach((row, i) => {
    row.addEventListener('click', () => openRecord(row.dataset.uid));
    const cb = row.querySelector('input[type=checkbox]');
    cb.addEventListener('click', e => {
      e.stopPropagation();
      /* the box has already flipped, so its state is the one being applied --
         shift-clicking an untick clears the run the same way ticking sets it */
      if (e.shiftKey) range(i, cb.checked); else toggle(i, cb.checked);
    });
  });

  list.addEventListener('keydown', e => {
    const row = e.target.closest('.mem-row');
    if (!row) return;
    const i = rows.indexOf(row);
    const step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
    if (step !== undefined) {
      const to = Math.min(rows.length - 1, Math.max(0, i + step));
      e.preventDefault();
      if (e.shiftKey) { selectRow(rows[i], true); range(to, true); }
      moveTo(to);
      return;
    }
    if (e.key === 'Home' || e.key === 'End') {
      e.preventDefault();
      moveTo(e.key === 'Home' ? 0 : rows.length - 1);
      return;
    }
    if (e.key === ' ') {
      /* the checkbox has its own Space when the caret is on the box itself */
      if (e.target.tagName === 'INPUT') return;
      e.preventDefault();
      if (e.shiftKey) range(i, true); else toggle(i);
      return;
    }
    if (e.key === 'Enter') { e.preventDefault(); openRecord(row.dataset.uid); return; }
    if ((e.ctrlKey || e.metaKey) && (e.key === 'a' || e.key === 'A')) {
      e.preventDefault();          /* selecting the page's text is not what this list is for */
      setAll(true);
      return;
    }
    /* Escape with nothing selected is left alone: it is the app's key for
       closing what is layered over the view, and this list is not that. */
    if (e.key === 'Escape' && selection.size) setAll(false);
  });

  /* Down out of the search field lands in the list, so finding rows and
     acting on them is one uninterrupted keyboard path. */
  $('#fQ').addEventListener('keydown', e => {
    if (e.key !== 'ArrowDown' || !rows.length) return;
    e.preventDefault();
    rows[cursor].focus();
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
      ? `<span class="match-badge" title="${m.fts_rank !== undefined ? `bm25 ${Number(m.fts_rank).toFixed(2)}` : ''}">${esc(m.match_source)}</span>` : '';
    /* The row keeps its click for the mouse, but the thing that OPENS the
       record is a real button around the snippet -- the row itself cannot be
       one, because it already contains a checkbox and a copy button and a
       control inside a control is a control neither the keyboard nor a
       screen reader can make sense of. Enter on the button bubbles a click
       to the row, so there is still exactly one handler. */
    return `<div class="mem-row" role="row" aria-selected="false" tabindex="-1" data-uid="${esc(m.uid)}">
      <!-- The two controls in the row are reachable by pointer and by the
           row's own keys (Space ticks, Enter opens), and they are OUT of the
           tab order: fifty rows of them is a hundred stops to cross one page,
           and it took two tabs to reach the second checkbox. -->
      <div class="mem-check" role="gridcell"><input type="checkbox" tabindex="-1" aria-label="${t('mem.select.aria', { uid: esc(m.uid) })}"></div>
      <!-- Confidence leads this column. It used to be the second of four
           whispers stacked in .mem-right, at 60% white, quieter than the uid
           beside it -- in a store whose whole point is that a human vets what
           an agent wrote, the vetting was the faintest thing in the row. -->
      <div class="mem-col-type" role="gridcell">${confPill(m.confidence, true)}${typeTag(m.type)}</div>
      <div class="mem-main" role="gridcell">
        <button type="button" class="row-open mem-snippet" tabindex="-1"
                aria-label="${esc(t('a11y.openRecord', { uid: m.uid }))}">${esc(m.content)}</button>
      </div>
      <div class="mem-right" role="gridcell">
        ${match}
        ${statusTag(m.status)}
        ${away ? `<span class="chip" title="${esc(t('mem.alsoWhy', { domain: m.domain }))}">${t('mem.also')}</span>` : ''}
        ${m.domain ? `<span class="chip">${esc(m.domain)}</span>` : ''}
        <!-- Only when it has been read back. A store where nothing has been
             recalled yet would otherwise wear a "0" on every row, and the
             rows that matter are found by sorting, not by reading zeros. -->
        ${m.recalls ? `<span class="chip" title="${esc(t('mem.recallsWhy',
            { n: m.recalls, when: m.last_recall || '' }))}">${t('mem.recalls', { n: m.recalls })}</span>` : ''}
        <span title="${esc(m.created_at)}">${fmtDate(m.created_at)}</span>
      </div>
    </div>`;
  }).join('');
}

/* Built once, then only its count is written. It used to be removed and
   recreated on every checkbox, which replayed its entrance animation each
   time and threw away the focus of whoever was tabbing through it. */
function syncBulkbar() {
  /* every path that changes the selection ends here, so the header box is
     brought along from one place rather than from each of them */
  syncSelectAll();
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
    ${pickerHTML({ id: 'bulkConf', label: t('bulk.setConf'), ariaLabel: t('bulk.setConf') })}
    <button type="button" class="btn btn-sm" id="bulkArch">${t('common.archive')}</button>
    <button type="button" class="btn btn-sm" id="bulkRest">${t('common.restore')}</button>
    <button type="button" class="icon-btn" id="bulkClear"
            title="${t('bulk.clear.title')}" aria-label="${t('bulk.clear.title')}">${icon('close')}</button>`;
  document.body.appendChild(bar);
  publishBulkHeight(bar);
  bulkSize = new ResizeObserver(() => publishBulkHeight(bar));
  bulkSize.observe(bar);

  /* keepLabel: this one is an ACTION and not a state -- it says what will
     happen to the selection, so it goes on saying it after it has happened */
  wirePicker(bar, {
    id: 'bulkConf', items: fixedItems(confItems()), keepLabel: true,
    onPick: value => runBulk({ action: 'confidence', value }),
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
    document.querySelectorAll('#memList .mem-row').forEach(row => selectRow(row, false));
    selection.clear();          /* rows from a page that has since been left */
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
