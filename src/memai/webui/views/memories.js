/* The memory list, and the inspector beside it.

   The list is the same one it has always been -- the filters, the paging,
   the roving-tabindex keyboard model. What changed is where the actions
   live: they used to be a strip that floated in over the rows once
   something was ticked, so the controls appeared on top of the thing they
   acted on and said only how many rows they had. The pane on the right is
   always there, it shows the memory when exactly one is ticked, and it
   turns into a batch editor when more are -- with what the change will do
   written out before the button that does it.

   Confidence, tags and the filed domain are STAGED: they are chosen here
   and written by Apply, in one /api/bulk call each. Archive, restore and
   send-to-project are not -- each is its own act with its own
   confirmation, and each runs when it is pressed. */

import { $, esc, fmtInt, fmtDate, fmtAgo, debounce } from '../core/dom.js';
import { api, query } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, failed, promptModal } from '../core/ui.js';
import { typeTag, statusTag, confPill, CONF, getDomains, inDomainPath,
         typeItems, confItems, invalidateDomains, uidChip, wireCopyChips } from '../core/shared.js';
import { pickerFor, wirePicker, fixedItems } from '../core/pick.js';
import { domainPickerHTML, wireDomainPicker } from '../core/domain-picker.js';
import { moveToProjectModal } from '../core/projects.js';
import { go, refreshBehind, parseHash } from '../core/router.js';
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

/* What the inspector is holding but has not written. Reset on every render,
   because a page it was never applied to is not a page it should still be
   staged over. */
let staged = { confidence: '', tags: [], domain: '' };
const clearStaged = () => { staged = { confidence: '', tags: [], domain: '' }; };

/* The domain tree, as the last render fetched it -- the inspector's re-home
   field draws from the same list the filter above it does. */
let domainTree = [];

/* The rows on screen, by uid: the inspector's whole source of truth. A
   selection cannot outlive the page it was made on (navigating clears it),
   so everything the pane says about the picked rows is already here and
   nothing it shows costs a request. */
let rowData = new Map();

/* The one place that writes a row's selected-ness. The tick, the row's own
   wash, the state a screen reader reads off the row, and the set the
   inspector acts on are four faces of one fact, and four call sites used to
   each set the ones they happened to remember. */
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
    /* the defect filters a Health symptom hands over -- see _defect_clauses
       in admin.py. They are carried, not offered: the button that sets one
       is on Health, and the chip below is how you take it off again. */
    linked: params.get('linked') || '',
    due: params.get('due') || '',
    stale: params.get('stale') || '',
    untitled: params.get('untitled') || '',
    sort: params.get('sort') || 'created_at',
    dir: params.get('dir') || 'desc',
    page: parseInt(params.get('page') || '0', 10) || 0,
  };
  selection.clear();
  clearStaged();

  const domains = await getDomains().catch(() => []);
  domainTree = domains;
  const qs = query({
    q: state.q, domain: state.domain, type: state.type, status: state.status,
    confidence: state.confidence, session: state.session, sort: state.sort, dir: state.dir,
    linked: state.linked, due: state.due, stale: state.stale, untitled: state.untitled,
    subtree: state.exact ? '0' : '',
    limit: PAGE, offset: state.page * PAGE,
  });
  const data = await api(`/api/memories?${qs}`);
  if (ctx.stale()) return;

  rowData = new Map(data.items.map(m => [m.uid, m]));

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

  /* one chip per defect filter in the URL, each one its own way off */
  const defects = ['linked', 'due', 'stale', 'untitled']
    .filter(k => state[k])
    .map(k => `<button type="button" class="chip clickable" data-undefect="${k}"
         title="${esc(t('mem.defect.off'))}">${t(`mem.defect.${k}`)}${icon('close')}</button>`)
    .join('');

  view.innerHTML = `<div class="mem-shell">
    <h2 class="sr-only">${t('mem.title')}</h2>

    <div class="mem-work">
      <div class="mem-pane">
        <div class="list-toolbar">
          <!-- how the search behaves, on the field it behaves on. It was a
               line of prose under the view's title, three inches away. -->
          <input id="fQ" type="search" placeholder="${t('mem.search.placeholder')}"
                 title="${esc(t('mem.sub'))}" value="${esc(state.q)}" spellcheck="false">
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
          ${defects}
          ${state.session ? `<button type="button" class="chip clickable" id="fSession" title="${t('mem.session.title')}">${t('mem.session.chip', { s: esc(state.session.slice(0, 18)) })}${icon('close')}</button>` : ''}
        </div>

        <!-- The header strip is a sibling of the rows and not the first of them:
             a select-all is a control OVER the list, and putting it inside the
             grid would have made it a row you can arrow onto and try to open. -->
        <div class="mem-list">
          ${data.items.length ? `<div class="mem-head">
            <div class="mem-check"><input type="checkbox" id="memAll" aria-label="${t('mem.selectAll.aria')}"></div>
            <label class="mem-head-label" for="memAll" data-selcount>${t('mem.selectAll', { n: data.items.length })}</label>
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
      </div>

      <aside class="mem-inspect" id="memInspect" aria-live="polite"></aside>
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
  view.querySelectorAll('[data-undefect]').forEach(b =>
    b.addEventListener('click', () => navigate({ [b.dataset.undefect]: '', page: 0 })));
  const fSession = $('#fSession');
  if (fSession) fSession.addEventListener('click', () => navigate({ session: '', page: 0 }));
  $('#pgPrev').addEventListener('click', () => navigate({ page: state.page - 1 }));
  $('#pgNext').addEventListener('click', () => navigate({ page: state.page + 1 }));

  const list = $('#memList');
  const rows = [...list.querySelectorAll('.mem-row')];

  /* What the record steps through when it is opened from here: this page, in
     the order it is shown.

     Cleared on the way out so a record opened from somewhere else does not
     inherit a list that is no longer on screen -- EXCEPT on the way into the
     record itself, which is a navigation now and tears this view down as it
     goes. Clearing there would hand the record an empty list every single
     time it was opened from one. */
  setRecordSequence(rows.map(r => r.dataset.uid));
  onTeardown(() => { if (parseHash().name !== 'memory') setRecordSequence([]); });

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
    paintInspector();
  };
  const range = (to, on) => {
    const [a, b] = anchor <= to ? [anchor, to] : [to, anchor];
    for (let i = a; i <= b; i++) selectRow(rows[i], on);
    paintInspector();
  };
  const setAll = on => {
    rows.forEach(row => selectRow(row, on));
    anchor = 0;
    paintInspector();
  };

  const memAll = $('#memAll');
  if (memAll) memAll.addEventListener('change', () => setAll(memAll.checked));

  rows.forEach((row, i) => {
    /* A click PICKS the row. `e.detail` is the click count, so the second
       click of a double click does not undo what the first one ticked --
       the row stays picked and dblclick opens it. */
    row.addEventListener('click', e => {
      if (e.detail > 1 || e.target.closest('input[type=checkbox]')) return;
      if (e.shiftKey) { selectRow(rows[i], true); range(i, true); } else toggle(i);
    });
    row.addEventListener('dblclick', () => openRecord(row.dataset.uid));
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

  paintInspector();
}

/* ─── the rows ────────────────────────────────────────────────────────────
   Quiet on purpose. The row used to carry the domain, the tags, the recall
   count and the full date beside the snippet -- four annotations per row,
   fifty rows down the page, and the one you were looking for was whichever
   the eye happened to land on. What a row says now is what tells you
   whether to open it: how far it has been vetted, what kind of memory it
   is, what it is called, and how old. Everything else is a tick away, in
   the pane that has room to lay it out. */

function renderRows(items, scope = '') {
  if (!items.length) return `<div class="empty">${t('mem.empty')}</div>`;
  return items.map(m => {
    /* `scope` is the active domain filter, needed to tell a row that LIVES
       in it from one that is only cross-listed into it. A list that showed
       both the same way would claim the second is filed where it is not. */
    const away = Boolean(scope) && !inDomainPath(m.domain, scope)
      && (m.also || []).some(p => inDomainPath(p, scope));
    /* A badge only for the row a pasted uid pinned: every other row is a
       BM25 hit, and a label all of them carry says nothing. */
    const match = m.match_source === 'uid'
      ? `<span class="match-badge" title="${esc(t('badge.uidMatchWhy'))}">${t('badge.uidMatch')}</span>` : '';
    /* bm25 on the row, not in a column of its own: a per-row diagnostic,
       read on hover when a result looks out of place. */
    const rank = m.fts_rank != null ? ` title="bm25 ${Number(m.fts_rank).toFixed(2)}"` : '';
    /* The row keeps its click for the mouse, but the thing that OPENS the
       record is a real button around the snippet -- the row itself cannot be
       one, because it already contains a checkbox and a control inside a
       control is a control neither the keyboard nor a screen reader can make
       sense of. Enter on the button bubbles a click to the row, so there is
       still exactly one handler. */
    return `<div class="mem-row" role="row" aria-selected="false" tabindex="-1" data-uid="${esc(m.uid)}"${rank}>
      <!-- The controls in the row are reachable by pointer and by the row's
           own keys (Space ticks, Enter opens), and they are OUT of the tab
           order: fifty rows of them is a hundred stops to cross one page. -->
      <div class="mem-check" role="gridcell"><input type="checkbox" tabindex="-1" aria-label="${t('mem.select.aria', { uid: esc(m.uid) })}"></div>
      <!-- Confidence leads this column. It used to be the second of four
           whispers stacked on the right, at 60% white, quieter than the uid
           beside it -- in a store whose whole point is that a human vets what
           an agent wrote, the vetting was the faintest thing in the row. -->
      <div class="mem-col-type" role="gridcell">${confPill(m.confidence, true)}${typeTag(m.type)}</div>
      <div class="mem-main" role="gridcell">
        <!-- A titled row shows its title alone, with the body on hover.
             A row with no title is the body: it is what names the memory
             when nothing else does.

             Plain text and not a button any more: a click on the row PICKS
             the memory now, and the two ways to open one -- a double click,
             or Enter -- both belong to the row rather than to one cell of
             it. The pane on the right carries the visible Open control. -->
        <span class="mem-snippet${m.title ? ' mem-named' : ''}"${
          m.title ? ` title="${esc(m.content)}"` : ''}>${esc(m.title || m.content)}</span>
      </div>
      <div class="mem-right" role="gridcell">
        ${match}
        ${statusTag(m.status)}
        ${away ? `<span class="chip" title="${esc(t('mem.alsoWhy', { domain: m.domain }))}">${t('mem.also')}</span>` : ''}
        <!-- Only on a window wide enough to have room for it (see the media
             query in admin.css). Uncapping the page left a run of empty
             pixels between a short title and its age, and where a memory is
             filed is the one thing worth putting there -- it is what tells
             two rows with similar titles apart. -->
        ${m.domain ? `<span class="mem-domain">${esc(m.domain)}</span>` : ''}
        <span title="${esc(m.created_at)}">${fmtAgo(m.created_at)}</span>
      </div>
    </div>`;
  }).join('');
}

/* ─── the inspector ───────────────────────────────────────────────────── */

function paintInspector() {
  syncSelectAll();
  const host = document.getElementById('memInspect');
  if (!host) return;
  const picked = [...selection].map(uid => rowData.get(uid)).filter(Boolean);
  const label = document.querySelector('[data-selcount]');
  /* innerHTML: both strings mark their number up, and textContent printed
     the <b> tags as text on every toggle */
  if (label) {
    label.innerHTML = picked.length
      ? t('mem.selectedOf', { n: picked.length, all: rowData.size })
      : t('mem.selectAll', { n: rowData.size });
  }
  host.innerHTML = picked.length ? editorHTML(picked) : emptyHTML();
  host.classList.toggle('is-empty', !picked.length);
  if (picked.length) wireEditor(host, picked);
}

function emptyHTML() {
  return `<div class="mi-empty">
    <div class="mi-empty-title">${t('mem.mi.emptyTitle')}</div>
    <p class="hint">${t('mem.mi.emptyBody')}</p>
    <ul class="mi-keys">
      <li><kbd>Space</kbd> ${t('mem.mi.keySpace')}</li>
      <li><kbd>Shift</kbd> ${t('mem.mi.keyShift')}</li>
      <li><kbd>Enter</kbd> ${t('mem.mi.keyEnter')}</li>
    </ul>
  </div>`;
}

/* The head says WHAT is being edited. One memory is named; several are
   described by the ways they differ, because that is what decides whether a
   single change is safe to make over all of them. */
function headHTML(picked) {
  if (picked.length === 1) {
    const m = picked[0];
    return `<div class="mi-head">
      <div class="mi-head-row">${typeTag(m.type)}${uidChip(m.uid)}${statusTag(m.status)}</div>
      <div class="mi-title">${esc(m.title || m.content.split('\n', 1)[0])}</div>
      <div class="mi-facts">
        <span>${m.domain ? esc(m.domain) : t('mem.mi.noDomain')}</span>
        <span>${t('mem.mi.written', { when: fmtDate(m.created_at) })}</span>
      </div>
      <button type="button" class="btn btn-solid btn-sm" data-open>${t('mem.mi.open')}</button>
    </div>`;
  }
  const domains = new Set(picked.map(m => m.domain || ''));
  const types = new Set(picked.map(m => m.type));
  const confs = new Set(picked.map(m => m.confidence));
  const only = confs.size === 1 ? CONF[[...confs][0]]?.label : '';
  return `<div class="mi-head">
    <div class="mg-label">${t('mem.mi.bulkTitle')}</div>
    <div class="mi-count">${t('mem.mi.nMemories', { n: picked.length })}</div>
    <div class="mi-facts">
      <span>${t('mem.mi.nDomains', { n: domains.size })}</span>
      <span>${t('mem.mi.nTypes', { n: types.size })}</span>
      <span>${only ? t('mem.mi.allConf', { label: esc(only) }) : t('mem.mi.mixedConf')}</span>
    </div>
  </div>`;
}

function editorHTML(picked) {
  const n = picked.length;
  const confRows = Object.keys(CONF).map(c => {
    const changing = picked.filter(m => m.confidence !== c).length;
    const on = staged.confidence === c;
    return `<button type="button" class="mi-conf c-${c}${on ? ' on' : ''}" data-conf="${c}"
              aria-pressed="${on}">
      <span class="mi-radio"></span>
      ${confPill(c)}
      <span class="mi-conf-n">${changing
        ? t('mem.mi.nChange', { n: changing })
        : t('mem.mi.allAlready')}</span>
    </button>`;
  }).join('');

  const tagChips = staged.tags.map(tag =>
    `<button type="button" class="chip clickable" data-untag="${esc(tag)}"
       title="${esc(t('mem.mi.tagOff'))}">${esc(tag)}${icon('close')}</button>`).join('');

  return `${headHTML(picked)}
    <div class="mi-body">
      <div class="mi-field">
        <div class="mg-label">${t('mem.mi.confidence')}</div>
        <div class="mi-confs">${confRows}</div>
      </div>

      <div class="mi-field">
        <label class="mg-label" for="miTag">${t('mem.mi.addTags')}</label>
        <div class="mi-tagbox">
          ${tagChips}
          <input type="text" id="miTag" autocomplete="off" spellcheck="false"
                 placeholder="${t('mem.mi.tagPlaceholder')}">
        </div>
      </div>

      <div class="mi-field">
        <div class="mg-label">${t('mem.mi.rehome')}</div>
        ${domainPickerHTML({ id: 'miDomain', value: staged.domain,
                             ariaLabel: t('mem.mi.rehome'),
                             anyLabel: t('mem.mi.rehomeNone') })}
      </div>

      <div class="mi-field">
        <div class="mg-label">${t('mem.mi.otherActions')}</div>
        <div class="mi-actions">
          <button type="button" class="btn btn-sm" data-act="archive">
            ${t('mem.mi.archiveN', { n })}<span class="hint-sm">${t('mem.mi.reversible')}</span></button>
          <button type="button" class="btn btn-sm" data-act="restore">${t('common.restore')}</button>
          <button type="button" class="btn btn-sm" data-act="project">${t('bulk.move')}</button>
        </div>
      </div>

      ${plannedHTML(picked)}
    </div>
    <div class="mi-foot">
      <button type="button" class="btn btn-solid" data-apply${
        stagedCount(picked) ? '' : ' disabled'}>${t('mem.mi.apply', { n })}</button>
      <button type="button" class="btn" data-clear>${t('mem.mi.clear')}</button>
    </div>`;
}

/* How many WRITES the staged edits amount to. Zero is what keeps Apply
   disabled -- a button that runs three no-ops is a button that lies. */
function stagedCount(picked) {
  let n = 0;
  if (staged.confidence) n += picked.filter(m => m.confidence !== staged.confidence).length;
  if (staged.tags.length) n += picked.length;
  if (staged.domain) n += picked.filter(m => m.domain !== staged.domain).length;
  return n;
}

/* Said before it happens, in rows rather than in verbs. The point is not to
   confirm the action -- Apply does that -- but to show how much of the
   selection it actually touches, which is the thing a count of ticked rows
   never tells you. */
function plannedHTML(picked) {
  const lines = [];
  if (staged.confidence) {
    const n = picked.filter(m => m.confidence !== staged.confidence).length;
    lines.push(t('mem.mi.planConf', { n, label: esc(CONF[staged.confidence].label) }));
  }
  if (staged.tags.length) {
    lines.push(t('mem.mi.planTags', { n: picked.length, list: esc(staged.tags.join(', ')) }));
  }
  if (staged.domain) {
    const n = picked.filter(m => m.domain !== staged.domain).length;
    lines.push(t('mem.mi.planDomain', { n, domain: esc(staged.domain) }));
  }
  if (!lines.length) return '';
  lines.push(t('mem.mi.planEdits', { n: stagedCount(picked) }));
  return `<div class="mi-plan">
    <div class="mg-label">${t('mem.mi.planTitle')}</div>
    ${lines.map(l => `<span>${l}</span>`).join('')}
  </div>`;
}

function wireEditor(host, picked) {
  wireCopyChips(host);
  host.querySelector('[data-open]')?.addEventListener('click',
    () => openRecord(picked[0].uid));

  host.querySelectorAll('[data-conf]').forEach(b => b.addEventListener('click', () => {
    /* pressing the chosen one again unstages it -- there is no "leave it
       alone" row to go back to, and adding one would be a fourth state in a
       three-state scale */
    staged.confidence = staged.confidence === b.dataset.conf ? '' : b.dataset.conf;
    paintInspector();
  }));

  const tagField = host.querySelector('#miTag');
  tagField?.addEventListener('keydown', e => {
    if (e.key !== 'Enter' && e.key !== ',') return;
    e.preventDefault();
    const tag = tagField.value.trim().replace(/,+$/, '');
    if (!tag || staged.tags.includes(tag)) { tagField.value = ''; return; }
    staged.tags.push(tag);
    paintInspector();
    document.getElementById('miTag')?.focus();
  });
  host.querySelectorAll('[data-untag]').forEach(b => b.addEventListener('click', () => {
    staged.tags = staged.tags.filter(x => x !== b.dataset.untag);
    paintInspector();
  }));

  wireDomainPicker(host, {
    id: 'miDomain', domains: domainTree, anyLabel: t('mem.mi.rehomeNone'),
    onPick: domain => { staged.domain = domain; paintInspector(); },
  });

  host.querySelector('[data-clear]').addEventListener('click', () => {
    clearStaged();
    document.querySelectorAll('#memList .mem-row').forEach(row => selectRow(row, false));
    selection.clear();
    paintInspector();
  });

  host.querySelector('[data-apply]').addEventListener('click', () => applyStaged(picked));

  host.querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', () =>
    runAction(b.dataset.act, picked)));
}

/* ─── writing ─────────────────────────────────────────────────────────── */

async function applyStaged(picked) {
  const uids = picked.map(m => m.uid);
  const calls = [];
  if (staged.confidence) calls.push({ action: 'confidence', value: staged.confidence });
  if (staged.tags.length) calls.push({ action: 'tag', value: staged.tags.join(', ') });
  if (staged.domain) calls.push({ action: 'rehome', value: staged.domain });
  try {
    let affected = 0;
    /* In order, and not in parallel: a re-home re-runs the cross-listing
       policy against the domain the memory ends up with, so it has to see
       the row after the other edits rather than beside them. */
    for (const body of calls) {
      affected += (await api('/api/bulk', { body: { ...body, uids } })).affected;
    }
    if (staged.domain) invalidateDomains();
    toast(t('bulk.updated', { n: affected }), 'ok');
    clearStaged();
    refreshBehind();
  } catch (err) { failed('err.bulk', err); }
}

async function runAction(action, picked) {
  const uids = picked.map(m => m.uid);
  if (action === 'project') {
    const moved = await moveToProjectModal({ uids });
    if (!moved) return;
    selection.clear();
    refreshBehind();
    return;
  }
  let reason = '';
  if (action === 'archive') {
    reason = await promptModal({
      title: t('bulk.archive.title'),
      body: t('bulk.archive.body', { n: uids.length }),
      label: t('bulk.reason.label'), okLabel: t('common.archive'), danger: true });
    if (reason === null) return;
  }
  try {
    const r = await api('/api/bulk', { body: { action, reason, uids } });
    /* Archiving fifty rows behind a single confirm was a one-way door.
       Restore over the same set is the exact inverse, so it is offered
       rather than leaving you to find those fifty rows again. The reverse
       direction gets no Undo -- see the note on the record's Restore. */
    toast(t('bulk.updated', { n: r.affected }), 'ok', action === 'archive' ? {
      action: {
        label: t('common.undo'),
        run: () => api('/api/bulk', { body: { action: 'restore', uids } })
          .then(() => { toast(t('bulk.undone', { n: uids.length }), 'ok'); refreshBehind(); })
          .catch(err => failed('err.bulk', err)),
      },
    } : {});
    selection.clear();
    refreshBehind();
  } catch (err) { failed('err.bulk', err); }
}
