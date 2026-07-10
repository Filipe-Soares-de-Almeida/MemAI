/* MemAI admin SPA (vanilla JS ES module, no build step).
   Talks to the JSON API in memai/admin.py. Hash-routed views; the
   memory record lives in a right-hand drawer; the graph is a small
   canvas force-layout (O(n²) repulsion is fine at memory-store scale). */

import { I18N, t } from './i18n.js';

/* ─── constants ─────────────────────────────────────────────────────── */

const cssVar = name =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const TYPE_ORDER = ['note', 'checkpoint', 'anti_pattern', 'reasoning', 'handoff'];
/* type colors live in admin.css (--t-*) — single source of truth */
const TYPES = Object.fromEntries(
  TYPE_ORDER.map(t => [t, { color: cssVar(`--t-${t}`) || '#9e9e9e' }]));
/* display labels for selects/legends — the raw enum stays lower_snake for CSS classes & payloads.
   t() comes from i18n.js (loaded first); labels bake once per page load — a language switch reloads. */
const TYPE_LABEL = Object.fromEntries(TYPE_ORDER.map(tp => [tp, t(`type.${tp}`)]));

const CONF = {
  unverified:   { i: '◌', label: t('conf.unverified') },
  confirmed:    { i: '✓', label: t('conf.confirmed') },
  contradicted: { i: '✕', label: t('conf.contradicted') },
};
const REL_SUGGEST = ['relates_to', 'supersedes', 'contradicts', 'duplicates', 'links_to'];
/* datalist options: canonical value stays in the payload; the translated label is display-only */
const relOptions = () => REL_SUGGEST.map(r => `<option value="${r}">${t(`rel.${r}`)}</option>`).join('');

const typeColor = t => (TYPES[t] || {}).color || '#9e9e9e';
const typeClass = t => TYPES[t] ? `t-${t}` : '';

/* ─── tiny helpers ──────────────────────────────────────────────────── */

const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtInt = n => Number(n || 0).toLocaleString(I18N.numberLocale);
const fmtBytes = b => {
  b = Number(b || 0);
  if (b < 1024) return `${b} B`;
  if (b < 1048576) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1048576).toFixed(1)} MB`;
};
const MONTHS = I18N.months;
function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return iso.slice(0, 16);
  return `${String(d.getDate()).padStart(2, '0')} ${MONTHS[d.getMonth()]} · ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
function fmtAgo(iso) {
  const ms = Date.now() - new Date(iso).getTime();
  if (isNaN(ms)) return '';
  const m = Math.floor(ms / 60000);
  if (m < 1) return t('ago.now');
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h`;
  return `${Math.floor(h / 24)} d`;
}
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

async function api(path, opts = {}) {
  if (opts.body !== undefined) {
    opts.method = opts.method || 'POST';
    opts.headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch { /* no body */ }
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
  return data;
}

function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 350); }, 3600);
}

const tip = $('#tip');
function tipShow(html, x, y) {
  tip.innerHTML = html;
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  tip.style.left = `${Math.min(x + 14, innerWidth - r.width - 10)}px`;
  tip.style.top = `${Math.min(y + 14, innerHeight - r.height - 10)}px`;
}
function tipHide() { tip.hidden = true; }

function copyUid(uid) {
  navigator.clipboard?.writeText(uid).then(() => toast(t('toast.uidCopied', { uid }), 'ok'));
}

/* ─── modal machinery ───────────────────────────────────────────────── */

function openModal({ title, bodyHTML, footHTML }) {
  closeModal();
  const scrim = document.createElement('div');
  scrim.className = 'modal-scrim';
  scrim.innerHTML = `<div class="modal" role="dialog" aria-label="${esc(title)}">
    <div class="modal-head">${title}</div>
    <div class="modal-body">${bodyHTML}</div>
    <div class="modal-foot">${footHTML || ''}</div>
  </div>`;
  scrim.addEventListener('mousedown', e => { if (e.target === scrim) closeModal(); });
  $('#modalRoot').appendChild(scrim);
  const first = scrim.querySelector('input, textarea, select, button');
  first && first.focus();
  return scrim;
}
function closeModal() { $('#modalRoot').innerHTML = ''; }

function confirmModal({ title, body, okLabel = t('common.confirm'), danger = false }) {
  return new Promise(resolve => {
    const m = openModal({
      title,
      bodyHTML: `<div>${body}</div>`,
      footHTML: `<button class="btn" data-x>${t('common.cancel')}</button>
                 <button class="btn ${danger ? 'btn-danger' : 'btn-solid'}" data-ok>${esc(okLabel)}</button>`,
    });
    m.querySelector('[data-x]').onclick = () => { closeModal(); resolve(false); };
    m.querySelector('[data-ok]').onclick = () => { closeModal(); resolve(true); };
  });
}

function promptModal({ title, body = '', label, placeholder = '', okLabel = t('common.confirm'), danger = false }) {
  return new Promise(resolve => {
    const m = openModal({
      title,
      bodyHTML: `${body ? `<div>${body}</div>` : ''}
        <div class="field"><label>${esc(label)}</label>
        <input type="text" data-in placeholder="${esc(placeholder)}"></div>`,
      footHTML: `<button class="btn" data-x>${t('common.cancel')}</button>
                 <button class="btn ${danger ? 'btn-danger' : 'btn-solid'}" data-ok>${esc(okLabel)}</button>`,
    });
    const input = m.querySelector('[data-in]');
    input.focus();
    input.addEventListener('keydown', e => { if (e.key === 'Enter') m.querySelector('[data-ok]').click(); });
    m.querySelector('[data-x]').onclick = () => { closeModal(); resolve(null); };
    m.querySelector('[data-ok]').onclick = () => { const v = input.value; closeModal(); resolve(v); };
  });
}

/* ─── shared render bits ────────────────────────────────────────────── */

const typeTag = t => `<span class="type-tag ${typeClass(t)}"><span class="dot"></span>${esc(t)}</span>`;
const confPill = c => {
  const meta = CONF[c] || { i: '·', label: c };
  return `<span class="conf-pill c-${esc(c)}"><i>${meta.i}</i>${esc(meta.label)}</span>`;
};
const uidChip = uid => `<span class="uid-chip" data-copy="${esc(uid)}" title="${t('uid.copyTitle')}">${esc(uid)}</span>`;
const statusTag = s => s === 'archived' ? `<span class="status-tag archived">${t('status.archived')}</span>` : '';

function wireCopyChips(root) {
  root.querySelectorAll('[data-copy]').forEach(el =>
    el.addEventListener('click', e => { e.stopPropagation(); copyUid(el.dataset.copy); }));
}

/* domains cache (datalists, selects) */
let _domainsCache = null, _domainsAt = 0;
async function getDomains(force = false) {
  if (!force && _domainsCache && Date.now() - _domainsAt < 60000) return _domainsCache;
  const data = await api('/api/domains');
  _domainsCache = data.domains;
  _domainsAt = Date.now();
  return _domainsCache;
}

/* ─── router ────────────────────────────────────────────────────────── */

const VIEWS = { overview: renderOverview, memories: renderMemories,
                graph: renderGraph, domains: renderDomains, maintenance: renderMaintenance,
                optimization: renderOptimization };
let currentView = '';

function parseHash() {
  const h = location.hash.replace(/^#\/?/, '');
  const [name, qs] = h.split('?');
  return { name: VIEWS[name] ? name : 'overview', params: new URLSearchParams(qs || '') };
}
function go(view, params = {}) {
  const qs = new URLSearchParams(params).toString();
  location.hash = `#/${view}${qs ? '?' + qs : ''}`;
}
async function route() {
  const { name, params } = parseHash();
  currentView = name;
  document.querySelectorAll('.nav a').forEach(a =>
    a.classList.toggle('active', a.dataset.view === name));
  const view = $('#view');
  view.innerHTML = '<div class="loading"><span class="spin"></span></div>';
  try {
    await VIEWS[name](view, params);
  } catch (err) {
    view.innerHTML = `<div class="empty">${t('error.loadFailed', { msg: esc(err.message) })}</div>`;
  }
  view.scrollTop = 0;
  /* deep link: #/any-view?record=<uid> opens the record drawer on top */
  if (params.get('record')) openRecord(params.get('record'));
}
function refreshBehind() { route().catch(() => {}); }

/* ═══ VIEW · overview ═══════════════════════════════════════════════ */

async function renderOverview(view) {
  const o = await api('/api/overview');
  updateRail(o);

  const tot = o.totals;
  const confTotal = Object.values(o.by_confidence).reduce((a, b) => a + b, 0) || 1;
  const confSeg = ['confirmed', 'unverified', 'contradicted'].map(c => {
    const n = o.by_confidence[c] || 0;
    const color = c === 'confirmed' ? 'var(--ok)' : c === 'contradicted' ? 'var(--bad)' : 'var(--warn)';
    return n ? `<div class="meter-seg" style="flex:${n};background:${color}" title="${esc(CONF[c].label)}: ${fmtInt(n)}"></div>` : '';
  }).join('');
  const confLegend = ['confirmed', 'unverified', 'contradicted'].map(c => {
    const color = c === 'confirmed' ? 'var(--ok)' : c === 'contradicted' ? 'var(--bad)' : 'var(--warn)';
    return `<span class="legend-item"><i style="color:${color}">${CONF[c].i}</i>${esc(CONF[c].label)} <b>${fmtInt(o.by_confidence[c] || 0)}</b></span>`;
  }).join('');

  const typesPresent = [...TYPE_ORDER.filter(x => x in o.by_type),
                        ...Object.keys(o.by_type).filter(x => !TYPE_ORDER.includes(x))];
  const maxType = Math.max(1, ...Object.values(o.by_type));
  const typeBars = typesPresent.map(tp => `
    <div class="bar-row">
      <span class="type-tag ${typeClass(tp)}"><span class="dot"></span>${esc(tp)}</span>
      <div class="bar-track"><div class="bar-fill ${typeClass(tp)}" style="width:${(o.by_type[tp] / maxType * 100).toFixed(1)}%"></div></div>
      <span class="bar-val">${fmtInt(o.by_type[tp])}</span>
    </div>`).join('') || `<div class="empty">${t('ov.types.empty')}</div>`;

  /* activity: fill a continuous 30-day calendar from the sparse rows */
  const byDay = Object.fromEntries(o.activity.map(a => [a.day, a.count]));
  const days = [];
  for (let i = 29; i >= 0; i--) {
    const d = new Date(); d.setDate(d.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    days.push({ key, count: byDay[key] || 0, today: i === 0 });
  }
  const maxDay = Math.max(1, ...days.map(d => d.count));
  const total30 = days.reduce((a, d) => a + d.count, 0);
  const sparkBars = days.map(d => `
    <div class="spark-bar${d.today ? ' today' : ''}" data-day="${d.key}" data-n="${d.count}"
         style="height:${Math.max(3, d.count / maxDay * 100)}%"
         aria-label="${d.key}: ${d.count}"></div>`).join('');

  const vecCov = tot.active + tot.archived > 0 && o.db.vec_ready
    ? Math.round(o.db.vec_rows / tot.memories * 100) : 0;

  const domRows = o.domains.map(d => `
    <tr class="clickable" data-domain="${esc(d.domain)}">
      <td>${esc(d.domain)}</td>
      <td class="num">${fmtInt(d.count)}</td>
      <td class="num" style="color:var(--ink-4)">${fmtAgo(d.latest_at)}</td>
    </tr>`).join('');

  const recentRows = o.recent.map(m => `
    <div class="rel-row" style="cursor:pointer" data-uid="${esc(m.uid)}">
      <span class="type-tag ${typeClass(m.type)}" style="flex:none;width:118px"><span class="dot"></span>${esc(m.type)}</span>
      <span class="rel-peer" style="pointer-events:none"><span class="snippet">${esc(m.content)}</span></span>
      <span style="font-size:10.5px;color:var(--ink-4);flex:none">${fmtAgo(m.created_at)}</span>
    </div>`).join('') || `<div class="empty">${t('ov.recent.empty')}</div>`;

  view.innerHTML = `<div class="anim">
    <div class="view-head">
      <h2 class="view-title">${t('ov.title')}</h2>
      <div class="view-sub">${esc(o.db.path)} · ${fmtBytes(o.db.size)}${o.db.wal_size ? ` (+${fmtBytes(o.db.wal_size)} WAL)` : ''} · ${t('ov.model')} ${esc(o.db.embed_model.split(/[\\/]/).pop() || '—')} · ${esc(o.db.embed_dim || '?')}d</div>
    </div>

    <div class="tiles">
      <div class="tile tile-hero"><div class="tile-label">${t('ov.tile.active')}</div>
        <div class="tile-value">${fmtInt(tot.active)}</div>
        <div class="tile-sub">${t('ov.tile.archivedSub', { n: fmtInt(tot.archived) })}</div></div>
      <div class="tile"><div class="tile-label">${t('ov.tile.domains')}</div><div class="tile-value">${fmtInt(tot.domains)}</div></div>
      <div class="tile"><div class="tile-label">${t('ov.tile.relations')}</div><div class="tile-value">${fmtInt(tot.relations)}</div></div>
      <div class="tile"><div class="tile-label">${t('ov.tile.edits')}</div><div class="tile-value">${fmtInt(tot.edits)}</div></div>
      <div class="tile"><div class="tile-label">${t('ov.tile.sessions')}</div><div class="tile-value">${fmtInt(tot.sessions)}</div></div>
      <div class="tile"><div class="tile-label">${t('ov.tile.vectors')}</div><div class="tile-value">${vecCov}%</div>
        <div class="tile-sub">${t('ov.tile.ofTotal', { a: fmtInt(o.db.vec_rows), b: fmtInt(tot.memories) })}</div></div>
    </div>

    <div class="grid grid-2" style="margin-bottom:14px">
      <div class="panel">
        <h3 class="panel-title">${t('ov.conf.title')} <span class="panel-aside">${t('ov.aside.active')}</span></h3>
        <div class="meter">${confSeg || '<div class="meter-seg" style="flex:1;background:var(--inset)"></div>'}</div>
        <div class="legend">${confLegend}</div>
      </div>
      <div class="panel">
        <h3 class="panel-title">${t('ov.types.title')} <span class="panel-aside">${t('ov.aside.active')}</span></h3>
        <div class="bars">${typeBars}</div>
      </div>
    </div>

    <div class="grid grid-3232" style="margin-bottom:14px">
      <div class="panel">
        <h3 class="panel-title">${t('ov.activity.title')} <span class="panel-aside">${t('ov.activity.aside', { n: fmtInt(total30) })}</span></h3>
        <div class="spark">${sparkBars}</div>
        <div class="spark-foot"><span>${days[0].key.slice(5, 7)}/${days[0].key.slice(8)}</span><span>${t('ov.activity.today')}</span></div>
        <div class="spark-stats">
          <div><div class="mg-label">${t('ov.activity.avg')}</div><div class="spark-stat">${(total30 / 30).toFixed(1)}</div></div>
          <div><div class="mg-label">${t('ov.activity.peak')}</div><div class="spark-stat">${fmtInt(maxDay)}</div></div>
          <div><div class="mg-label">${t('ov.activity.daysWith')}</div><div class="spark-stat">${t('ov.activity.ofDays', { n: days.filter(d => d.count > 0).length })}</div></div>
        </div>
      </div>
      <div class="panel">
        <h3 class="panel-title">${t('ov.recent.title')}</h3>
        ${recentRows}
      </div>
    </div>

    <div class="panel">
      <h3 class="panel-title">${t('ov.domains.title')} <span class="panel-aside">${t('ov.domains.aside')}</span></h3>
      <table class="table"><thead><tr><th>${t('common.domain')}</th><th class="num">${t('common.memories')}</th><th class="num">${t('common.lastActivity')}</th></tr></thead>
      <tbody>${domRows || ''}</tbody></table>
      ${domRows ? '' : `<div class="empty">${t('ov.domains.empty')}</div>`}
    </div>
  </div>`;

  view.querySelectorAll('.spark-bar').forEach(b => {
    b.addEventListener('mousemove', e => tipShow(t('ov.tip.onDay', { n: b.dataset.n, day: b.dataset.day }), e.clientX, e.clientY));
    b.addEventListener('mouseleave', tipHide);
  });
  view.querySelectorAll('tr.clickable').forEach(tr => {
    tr.style.cursor = 'pointer';
    tr.addEventListener('click', () => go('memories', { domain: tr.dataset.domain }));
  });
  view.querySelectorAll('[data-uid]').forEach(el =>
    el.addEventListener('click', () => openRecord(el.dataset.uid)));
}

function updateRail(o) {
  const cov = o.db.vec_ready && o.totals.memories
    ? Math.round(o.db.vec_rows / o.totals.memories * 100) : 0;
  $('#railHealth').innerHTML = `
    <div class="rh-row"><span>${t('rail.db')}</span><b>${fmtBytes(o.db.size)}</b></div>
    <div class="rh-row"><span>${t('rail.vectors')}</span><b>${cov}%</b></div>
    <div class="rh-meter"><div class="rh-fill" style="width:${cov}%"></div></div>
    <div class="rh-row"><span>${t('rail.active')}</span><b>${fmtInt(o.totals.active)}</b></div>`;
  $('#dbBadge').textContent = t('badge.active', { n: fmtInt(o.totals.active) });
  $('#dbBadge').title = o.db.path;
}

/* ═══ VIEW · memories ═══════════════════════════════════════════════ */

const selection = new Set();

async function renderMemories(view, params) {
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
  selection.clear();

  const domains = await getDomains().catch(() => []);
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries({ q: state.q, domain: state.domain, type: state.type,
    status: state.status, confidence: state.confidence, session: state.session,
    sort: state.sort, dir: state.dir })) if (v) qs.set(k, v);
  qs.set('limit', '50');
  qs.set('offset', String(state.page * 50));
  const data = await api(`/api/memories?${qs}`);

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
      ${state.session ? `<span class="chip clickable" id="fSession" title="${t('mem.session.title')}">${t('mem.session.chip', { s: esc(state.session.slice(0, 18)) })}</span>` : ''}
    </div>

    <div class="mem-list" id="memList">${renderRows(data.items)}</div>

    <div class="list-foot">
      <span>${data.searched
        ? t('mem.results', { n: fmtInt(data.total), q: esc(state.q) })
        : t('mem.range', { a: fmtInt(state.page * 50 + Math.min(1, data.items.length)), b: fmtInt(state.page * 50 + data.items.length), c: fmtInt(data.total) })}</span>
      <span class="pager">
        <button class="btn btn-sm" id="pgPrev" ${state.page === 0 ? 'disabled' : ''}>${t('mem.prev')}</button>
        <button class="btn btn-sm" id="pgNext" ${(state.page + 1) * 50 >= data.total ? 'disabled' : ''}>${t('mem.next')}</button>
      </span>
    </div>
  </div>`;

  const navigate = patch => {
    const p = { ...state, ...patch };
    const out = {};
    for (const [k, v] of Object.entries(p)) if (v !== '' && v !== null && k !== 'page' || (k === 'page' && v)) out[k] = v;
    if (!('status' in patch ? patch.status : state.status)) out.status = '';
    if (p.status === 'active') delete out.status;         /* default */
    if (!p.page) delete out.page;
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
      ? `<span class="match-badge" title="${m.fts_rank !== undefined ? `bm25 ${Number(m.fts_rank).toFixed(2)} ` : ''}${m.vec_distance !== undefined ? `cos ${Number(m.vec_distance).toFixed(3)}` : ''}">${m.match_source}</span>` : '';
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
    <button class="icon-btn" id="bulkClear" title="${t('bulk.clear.title')}">✕</button>`;
  document.body.appendChild(bar);

  $('#bulkConf').addEventListener('change', async e => {
    if (!e.target.value) return;
    await runBulk({ action: 'confidence', value: e.target.value });
  });
  $('#bulkArch').addEventListener('click', async () => {
    const reason = await promptModal({
      title: t('bulk.archive.title'),
      body: t('bulk.archive.body', { n: selection.size }),
      label: t('bulk.reason.label'), okLabel: t('common.archive'), danger: true });
    if (reason === null) return;
    await runBulk({ action: 'archive', reason });
  });
  $('#bulkRest').addEventListener('click', () => runBulk({ action: 'restore' }));
  $('#bulkClear').addEventListener('click', () => { selection.clear(); renderBulkbar(); refreshBehind(); });
}

async function runBulk(body) {
  try {
    const r = await api('/api/bulk', { body: { ...body, uids: [...selection] } });
    toast(t('bulk.updated', { n: r.affected }), 'ok');
    selection.clear();
    refreshBehind();
  } catch (err) { toast(err.message, 'bad'); }
}

/* ═══ drawer · memory record ════════════════════════════════════════ */

const drawer = $('#drawer'), scrim = $('#scrim');

function closeDrawer() {
  drawer.classList.remove('open');
  scrim.classList.remove('show');
  setTimeout(() => { drawer.hidden = true; scrim.hidden = true; drawer.innerHTML = ''; }, 300);
}
scrim.addEventListener('click', closeDrawer);

async function openRecord(uid) {
  drawer.hidden = false; scrim.hidden = false;
  requestAnimationFrame(() => { drawer.classList.add('open'); scrim.classList.add('show'); });
  drawer.innerHTML = '<div class="loading"><span class="spin"></span></div>';
  let m;
  try { m = await api(`/api/memories/${uid}`); }
  catch (err) { drawer.innerHTML = `<div class="empty">${esc(err.message)}</div>`; return; }

  const rels = m.relations.map(r => `
    <div class="rel-row">
      <span class="rel-dir" title="${r.direction === 'out' ? t('dr.rel.out.title') : t('dr.rel.in.title')}">${r.direction === 'out' ? '→' : '←'}</span>
      <span class="rel-type-chip">${esc(r.relation_type)}</span>
      ${r.peer.missing
        ? `<span class="rel-peer"><span class="snippet" style="color:var(--bad)">${t('dr.rel.missing', { uid: esc(r.peer.uid) })}</span></span>`
        : `<span class="rel-peer" data-open="${esc(r.peer.uid)}">
             <span class="type-tag ${typeClass(r.peer.type)}" style="flex:none"><span class="dot"></span>${esc(r.peer.type)}</span>
             <span class="snippet">${esc(r.peer.snippet)}</span>
             ${r.peer.status === 'archived' ? statusTag('archived') : ''}
           </span>`}
      ${r.note ? `<span class="icon-btn" title="${esc(r.note)}" style="cursor:help">𝒊</span>` : ''}
      <button class="icon-btn danger" data-delrel="${r.id}" title="${t('dr.rel.remove.title')}">✕</button>
    </div>`).join('') || `<div class="empty" style="padding:18px">${t('dr.rel.empty')}</div>`;

  const hist = m.edit_history.slice().reverse().map((e, i) => `
    <div class="hist-item">
      <div class="hist-when">${fmtDate(e.edited_at)} <span style="opacity:.6">· ${esc(e.edited_at)}</span></div>
      <div class="hist-note">${esc(e.note || '') || (e.prev_content !== e.new_content ? t('dr.hist.contentEdited') : t('dr.hist.entry'))}</div>
      ${e.prev_content !== e.new_content
        ? `<button class="btn btn-sm" data-diff="${i}" style="margin-top:6px">${t('dr.hist.viewBtn')}</button>
           <div class="hist-diff" data-diffbody="${i}" hidden></div>` : ''}
    </div>`).join('') || `<div class="empty" style="padding:18px">${t('dr.hist.empty')}</div>`;

  drawer.innerHTML = `
    <div class="drawer-head">
      ${typeTag(m.type)}
      ${uidChip(m.uid)}
      ${statusTag(m.status)}
      ${confPill(m.confidence)}
      <span class="spacer"></span>
      <button class="icon-btn" id="dClose" title="${t('dr.close.title')}" style="font-size:19px">✕</button>
    </div>
    <div class="drawer-body">

      <div class="section">
        <div class="section-label">${t('dr.content')}
          <button class="btn btn-sm" id="dEdit">${t('common.edit')}</button>
        </div>
        <pre class="content-pre" id="dContent">${esc(m.content)}</pre>
        <div id="dEditBox" hidden style="display:grid;gap:9px;margin-top:10px">
          <textarea id="dEditText" rows="10"></textarea>
          <input type="text" id="dEditNote" placeholder="${t('dr.editNote.placeholder')}">
          <div class="act-row">
            <button class="btn btn-solid" id="dEditSave">${t('dr.saveVersion')}</button>
            <button class="btn" id="dEditCancel">${t('common.cancel')}</button>
            <span style="font-size:11px;color:var(--ink-4)">${t('dr.prevKept')}</span>
          </div>
        </div>
      </div>

      <div class="section">
        <div class="section-label">${t('dr.metadata')}
          <button class="btn btn-sm" id="dMeta">${t('common.edit')}</button>
        </div>
        <div class="meta-grid">
          <div><div class="mg-label">${t('dr.meta.domain')}</div><div class="mg-val">${m.domain ? `<span class="chip clickable" data-fdomain="${esc(m.domain)}">${esc(m.domain)}</span>` : '—'}</div></div>
          <div><div class="mg-label">${t('dr.meta.session')}</div><div class="mg-val">${m.session ? `<span class="chip clickable" data-fsession="${esc(m.session)}" title="${esc(m.session)}">${esc(m.session.length > 24 ? m.session.slice(0, 24) + '…' : m.session)}</span>` : '—'}</div></div>
          <div><div class="mg-label">${t('dr.meta.tags')}</div><div class="mg-val">${esc(m.tags || '—')}</div></div>
          <div><div class="mg-label">${t('dr.meta.created')}</div><div class="mg-val" title="${esc(m.created_at)}">${fmtDate(m.created_at)}</div></div>
          <div><div class="mg-label">${t('dr.meta.updated')}</div><div class="mg-val" title="${esc(m.updated_at)}">${fmtDate(m.updated_at)}</div></div>
          <div><div class="mg-label">${t('dr.meta.size')}</div><div class="mg-val">${fmtInt(m.content.length)} ${t('common.chars')}</div></div>
          ${m.superseded_by ? `<div><div class="mg-label">${t('dr.meta.supersededBy')}</div><div class="mg-val"><span class="uid-chip" data-open="${esc(m.superseded_by)}" style="cursor:pointer">${esc(m.superseded_by)}</span></div></div>` : ''}
        </div>
      </div>

      <div class="section">
        <div class="section-label">${t('dr.curation')}</div>
        <div class="act-row">
          <div class="seg" id="dConf">
            ${Object.keys(CONF).map(c => `<button data-c="${c}" class="${m.confidence === c ? 'active' : ''}"><i class="conf-pill c-${c}" style="font-style:normal"><i>${CONF[c].i}</i></i>${CONF[c].label}</button>`).join('')}
          </div>
          ${m.status === 'active'
            ? `<button class="btn" id="dArchive">${t('dr.archiveSoft')}</button>`
            : `<button class="btn" id="dRestore">${t('common.restore')}</button>`}
        </div>
      </div>

      <div class="section">
        <div class="section-label">${t('dr.relations')} <span style="letter-spacing:0;text-transform:none">(${m.relations.length})</span></div>
        ${rels}
        <div class="rel-add">
          <div class="act-row" style="align-items:stretch">
            <div class="picker" style="flex:2;min-width:200px">
              <input type="text" id="relTarget" placeholder="${t('dr.rel.target.placeholder')}" autocomplete="off">
              <div class="picker-results" id="relResults" hidden></div>
            </div>
            <input type="text" id="relType" list="relTypesDL" placeholder="${t('dr.rel.type.placeholder')}" style="flex:1;min-width:130px">
            <button class="btn" id="relCreate">${t('dr.rel.link')}</button>
          </div>
          <input type="text" id="relNote" placeholder="${t('dr.rel.note.placeholder')}">
          <datalist id="relTypesDL">${relOptions()}</datalist>
        </div>
      </div>

      <div class="section">
        <div class="section-label">${t('dr.history')} <span style="letter-spacing:0;text-transform:none">(${m.edit_history.length})</span></div>
        ${hist}
      </div>

      <div class="section">
        <details class="danger-zone">
          <summary>${t('dz.summary')}</summary>
          <div class="dz-body">
            <div class="dz-hint">${t('dz.hint', { uid: esc(m.uid) })}</div>
            <div class="act-row">
              <input type="text" id="dzPhrase" placeholder="DELETE ${esc(m.uid)}" style="flex:1" autocomplete="off">
              <button class="btn btn-danger" id="dzGo" disabled>${t('dz.button')}</button>
            </div>
          </div>
        </details>
      </div>
    </div>`;

  wireCopyChips(drawer);
  $('#dClose').addEventListener('click', closeDrawer);
  drawer.querySelectorAll('[data-open]').forEach(el =>
    el.addEventListener('click', () => openRecord(el.dataset.open)));
  drawer.querySelectorAll('[data-fdomain]').forEach(el =>
    el.addEventListener('click', () => { closeDrawer(); go('memories', { domain: el.dataset.fdomain }); }));
  drawer.querySelectorAll('[data-fsession]').forEach(el =>
    el.addEventListener('click', () => { closeDrawer(); go('memories', { session: el.dataset.fsession, status: '' }); }));

  /* edit content */
  $('#dEdit').addEventListener('click', () => {
    const box = $('#dEditBox');
    box.hidden = !box.hidden;
    if (!box.hidden) { $('#dEditText').value = m.content; $('#dEditText').focus(); }
  });
  $('#dEditCancel').addEventListener('click', () => { $('#dEditBox').hidden = true; });
  $('#dEditSave').addEventListener('click', async () => {
    try {
      await api(`/api/memories/${uid}/content`, { body: { content: $('#dEditText').value, note: $('#dEditNote').value } });
      toast(t('dr.contentUpdated'), 'ok');
      openRecord(uid); refreshBehind();
    } catch (err) { toast(err.message, 'bad'); }
  });

  /* edit metadata */
  $('#dMeta').addEventListener('click', () => openMetaModal(m));

  /* confidence */
  $('#dConf').querySelectorAll('button').forEach(b => b.addEventListener('click', async () => {
    if (b.dataset.c === m.confidence) return;
    try {
      await api(`/api/memories/${uid}/confidence`, { body: { confidence: b.dataset.c } });
      toast(t('dr.confSet', { label: CONF[b.dataset.c].label }), 'ok');
      openRecord(uid); refreshBehind();
    } catch (err) { toast(err.message, 'bad'); }
  }));

  /* archive / restore */
  const dArch = $('#dArchive');
  if (dArch) dArch.addEventListener('click', async () => {
    const reason = await promptModal({
      title: t('dr.archiveModal.title'),
      body: t('dr.archiveModal.body'),
      label: t('dr.archiveModal.label'), okLabel: t('common.archive'), danger: true });
    if (reason === null) return;
    try {
      await api(`/api/memories/${uid}/status`, { body: { status: 'archived', reason } });
      toast(t('dr.archived'), 'ok'); openRecord(uid); refreshBehind();
    } catch (err) { toast(err.message, 'bad'); }
  });
  const dRest = $('#dRestore');
  if (dRest) dRest.addEventListener('click', async () => {
    try {
      await api(`/api/memories/${uid}/status`, { body: { status: 'active' } });
      toast(t('dr.restored'), 'ok'); openRecord(uid); refreshBehind();
    } catch (err) { toast(err.message, 'bad'); }
  });

  /* relations */
  drawer.querySelectorAll('[data-delrel]').forEach(b => b.addEventListener('click', async () => {
    const ok = await confirmModal({
      title: t('dr.rel.removeModal.title'),
      body: t('dr.rel.removeModal.body'),
      okLabel: t('dr.rel.removeModal.ok'), danger: true });
    if (!ok) return;
    try {
      await api(`/api/relations/${b.dataset.delrel}`, { method: 'DELETE' });
      toast(t('dr.rel.removed'), 'ok'); openRecord(uid);
    } catch (err) { toast(err.message, 'bad'); }
  }));

  let relPick = null;
  const relInput = $('#relTarget'), relResults = $('#relResults');
  const doLookup = debounce(async () => {
    const q = relInput.value.trim();
    try {
      const r = await api(`/api/lookup?q=${encodeURIComponent(q)}&exclude=${uid}`);
      relResults.innerHTML = r.items.map(it => `
        <div class="picker-item" data-pick="${esc(it.uid)}">
          <span class="dot" style="--c:${typeColor(it.type)}"></span>
          <span class="uid-chip" style="cursor:inherit">${esc(it.uid)}</span>
          <span class="snippet">${esc(it.snippet)}</span>
        </div>`).join('') || `<div class="picker-item">${t('lookup.empty')}</div>`;
      relResults.hidden = false;
      relResults.querySelectorAll('[data-pick]').forEach(it => it.addEventListener('mousedown', () => {
        relPick = it.dataset.pick;
        relInput.value = it.dataset.pick;
        relResults.hidden = true;
      }));
    } catch { /* lookup is best-effort */ }
  }, 280);
  relInput.addEventListener('input', () => { relPick = null; doLookup(); });
  relInput.addEventListener('focus', doLookup);
  relInput.addEventListener('blur', () => setTimeout(() => { relResults.hidden = true; }, 180));

  $('#relCreate').addEventListener('click', async () => {
    const target = relPick || relInput.value.trim();
    const relType = $('#relType').value.trim();
    if (!target || !relType) { toast(t('dr.rel.pickBoth'), 'bad'); return; }
    try {
      await api('/api/relations', { body: { from_uid: uid, to_uid: target, relation_type: relType, note: $('#relNote').value } });
      toast(t('dr.rel.created'), 'ok'); openRecord(uid);
    } catch (err) { toast(err.message, 'bad'); }
  });

  /* history diffs (lazy) */
  const histRev = m.edit_history.slice().reverse();
  drawer.querySelectorAll('[data-diff]').forEach(b => b.addEventListener('click', () => {
    const i = b.dataset.diff;
    const body = drawer.querySelector(`[data-diffbody="${i}"]`);
    if (body.hidden && !body.innerHTML)
      body.innerHTML = renderDiff(histRev[i].prev_content, histRev[i].new_content);
    body.hidden = !body.hidden;
    b.textContent = body.hidden ? t('dr.hist.show') : t('dr.hist.hide');
  }));

  /* purge */
  const dzPhrase = $('#dzPhrase'), dzGo = $('#dzGo');
  dzPhrase.addEventListener('input', () => { dzGo.disabled = dzPhrase.value !== `DELETE ${uid}`; });
  dzGo.addEventListener('click', async () => {
    try {
      await api(`/api/memories/${uid}/purge`, { body: { confirm: dzPhrase.value } });
      toast(t('dz.purged'), 'ok');
      closeDrawer(); refreshBehind();
    } catch (err) { toast(err.message, 'bad'); }
  });
}

function openMetaModal(m) {
  const dl = (_domainsCache || []).map(d => `<option value="${esc(d.domain)}">`).join('');
  const modal = openModal({
    title: t('mm.title'),
    bodyHTML: `
      <div class="field"><label>${t('mm.type')}</label>
        <select id="mmType">${TYPE_ORDER.map(tp => `<option ${tp === m.type ? 'selected' : ''}>${tp}</option>`).join('')}
        ${TYPE_ORDER.includes(m.type) ? '' : `<option selected>${esc(m.type)}</option>`}</select></div>
      <div class="field"><label>${t('dr.meta.domain')}</label>
        <input type="text" id="mmDomain" value="${esc(m.domain)}" list="mmDomainsDL"><datalist id="mmDomainsDL">${dl}</datalist></div>
      <div class="field"><label>${t('mm.tags.label')}</label>
        <input type="text" id="mmTags" value="${esc(m.tags)}"></div>
      <div class="field"><label>${t('dr.meta.session')}</label>
        <input type="text" id="mmSession" value="${esc(m.session)}"></div>
      <div style="font-size:11px;color:var(--ink-4)">${t('mm.hint')}</div>`,
    footHTML: `<button class="btn" data-x>${t('common.cancel')}</button><button class="btn btn-solid" data-ok>${t('common.save')}</button>`,
  });
  modal.querySelector('[data-x]').onclick = closeModal;
  modal.querySelector('[data-ok]').onclick = async () => {
    try {
      const r = await api(`/api/memories/${m.uid}/meta`, { body: {
        type: $('#mmType').value, domain: $('#mmDomain').value,
        tags: $('#mmTags').value, session: $('#mmSession').value } });
      closeModal();
      toast(r.changed.length ? t('mm.updated', { list: r.changed.join(', ') }) : t('mm.nothing'), 'ok');
      _domainsCache = null;
      openRecord(m.uid); refreshBehind();
    } catch (err) { toast(err.message, 'bad'); }
  };
}

/* line diff — plain LCS, plenty for memory-sized content */
function renderDiff(a, b) {
  const A = a.split('\n'), B = b.split('\n');
  if (A.length * B.length > 250000)
    return `<span class="diff-del">− ${esc(a.slice(0, 800))}…</span><span class="diff-add">+ ${esc(b.slice(0, 800))}…</span>`;
  const dp = Array.from({ length: A.length + 1 }, () => new Uint16Array(B.length + 1));
  for (let i = A.length - 1; i >= 0; i--)
    for (let j = B.length - 1; j >= 0; j--)
      dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < A.length && j < B.length) {
    if (A[i] === B[j]) { out.push(`<span class="diff-ctx">  ${esc(A[i])}</span>`); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push(`<span class="diff-del">− ${esc(A[i])}</span>`); i++; }
    else { out.push(`<span class="diff-add">+ ${esc(B[j])}</span>`); j++; }
  }
  while (i < A.length) out.push(`<span class="diff-del">− ${esc(A[i++])}</span>`);
  while (j < B.length) out.push(`<span class="diff-add">+ ${esc(B[j++])}</span>`);
  return out.join('');
}

/* ═══ VIEW · graph ══════════════════════════════════════════════════ */

let graphEngine = null;

async function renderGraph(view, params) {
  const state = {
    status: params.has('status') ? params.get('status') : 'active',
    domain: params.get('domain') || '',
    type: params.get('type') || '',
  };
  const [domains, data] = await Promise.all([
    getDomains().catch(() => []),
    api(`/api/graph?${new URLSearchParams(Object.fromEntries(Object.entries(state).filter(([, v]) => v)))}`),
  ]);

  const counts = {};
  data.nodes.forEach(n => { counts[n.type] = (counts[n.type] || 0) + 1; });

  view.innerHTML = `<div class="anim">
    <div class="view-head">
      <h2 class="view-title">${t('g.title')}</h2>
      <div class="view-sub">${t('g.sub', { n: fmtInt(data.nodes.length), m: fmtInt(data.edges.length) })}</div>
    </div>
    <div class="graph-wrap" id="gWrap">
      <canvas id="gCanvas"></canvas>
      <div class="graph-controls">
        <select id="gDomain">
          <option value="">${t('common.allDomains')}</option>
          ${domains.map(d => `<option value="${esc(d.domain)}" ${d.domain === state.domain ? 'selected' : ''}>${esc(d.domain)}</option>`).join('')}
        </select>
        <select id="gType">
          <option value="">${t('common.allTypes')}</option>
          ${TYPE_ORDER.map(tp => `<option value="${tp}" ${tp === state.type ? 'selected' : ''}>${TYPE_LABEL[tp]}</option>`).join('')}
        </select>
        <div class="seg">
          <button data-v="active" class="${state.status === 'active' ? 'active' : ''}">${t('common.active')}</button>
          <button data-v="" class="${state.status === '' ? 'active' : ''}">${t('common.all')}</button>
        </div>
        <button class="btn btn-sm" id="gLink">${t('g.linkMode')}</button>
        <button class="btn btn-sm" id="gFit">${t('g.center')}</button>
      </div>
      <div class="graph-legend">
        ${TYPE_ORDER.filter(tp => counts[tp]).map(tp =>
          `<span class="legend-item"><span class="dot" style="--c:${typeColor(tp)}"></span>${TYPE_LABEL[tp]} <b>${counts[tp]}</b></span>`).join('') || t('g.emptyLegend')}
      </div>
      <div id="gBanner" class="link-banner" hidden></div>
      <div id="gCard" hidden></div>
    </div>
  </div>`;

  const nav = patch => {
    const p = { ...state, ...patch };
    const out = {};
    for (const [k, v] of Object.entries(p)) if (v) out[k] = v;
    if (p.status === '') out.status = '';
    if (p.status === 'active') delete out.status;
    go('graph', out);
  };
  $('#gDomain').addEventListener('change', e => nav({ domain: e.target.value }));
  $('#gType').addEventListener('change', e => nav({ type: e.target.value }));
  view.querySelectorAll('.graph-controls .seg button').forEach(b =>
    b.addEventListener('click', () => nav({ status: b.dataset.v })));

  graphEngine?.destroy();
  try {
    graphEngine = new ForceGraph($('#gCanvas'), data.nodes, data.edges);
  } catch (err) {
    toast(t('g.err', { msg: err.message }), 'bad');
    return;
  }
  $('#gFit').addEventListener('click', () => graphEngine.fit());
  $('#gLink').addEventListener('click', () => graphEngine.toggleLinkMode());
}

class ForceGraph {
  constructor(canvas, nodes, edges) {
    this.cv = canvas;
    this.cx = canvas.getContext('2d');
    const R = Math.sqrt(nodes.length + 1) * 60;
    this.nodes = nodes.map((n, i) => ({
      ...n,
      x: Math.cos(i * 2.399963) * R * Math.sqrt((i + 1) / (nodes.length + 1)),
      y: Math.sin(i * 2.399963) * R * Math.sqrt((i + 1) / (nodes.length + 1)),
      vx: 0, vy: 0,
      r: 5.5 + Math.min(8, n.degree * 1.5),
    }));
    this.byUid = Object.fromEntries(this.nodes.map(n => [n.uid, n]));
    this.edges = edges.filter(e => this.byUid[e.from_uid] && this.byUid[e.to_uid]);
    this.tx = 0; this.ty = 0; this.scale = 1;
    this.alpha = 1;
    this.hover = null; this.selected = null;
    this.linkMode = false; this.linkFrom = null;
    this.drag = null; this.pan = null;
    /* theme colors resolved once from CSS custom properties */
    this.colAccent = cssVar('--accent') || '#bb86fc';
    this.colRing = cssVar('--bg') || '#121212';
    this.colEdge = 'rgba(255,255,255,.25)';
    this.colArrow = 'rgba(255,255,255,.38)';
    this.colLabel = 'rgba(255,255,255,.87)';

    this._resize = this.resize.bind(this);
    addEventListener('resize', this._resize);
    this.resize();
    /* settle synchronously so the graph is born calm — and painted at
       least once even where rAF is throttled (headless, hidden tabs) */
    for (let k = 0; k < 900 && this.alpha > .05; k++) this.physics();
    this.fit();
    this.draw();

    canvas.addEventListener('mousedown', e => this.onDown(e));
    canvas.addEventListener('mousemove', e => this.onMove(e));
    addEventListener('mouseup', this._up = () => this.onUp());
    canvas.addEventListener('wheel', e => this.onWheel(e), { passive: false });
    canvas.addEventListener('click', e => this.onClick(e));

    this.running = true;
    this.loop = this.loop.bind(this);
    requestAnimationFrame(this.loop);
  }
  destroy() {
    this.running = false;
    removeEventListener('resize', this._resize);
    removeEventListener('mouseup', this._up);
  }
  resize() {
    const r = this.cv.parentElement.getBoundingClientRect();
    const dpr = devicePixelRatio || 1;
    this.w = r.width; this.h = r.height;
    this.cv.width = r.width * dpr; this.cv.height = r.height * dpr;
    this.cx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.alpha = Math.max(this.alpha, .3);
  }
  fit() {
    if (!this.nodes.length) return;
    const xs = this.nodes.map(n => n.x), ys = this.nodes.map(n => n.y);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const spanX = Math.max(80, maxX - minX), spanY = Math.max(80, maxY - minY);
    this.scale = Math.min(2, Math.min(this.w / (spanX + 140), this.h / (spanY + 140)));
    this.tx = this.w / 2 - (minX + maxX) / 2 * this.scale;
    this.ty = this.h / 2 - (minY + maxY) / 2 * this.scale;
  }
  toWorld(e) {
    const r = this.cv.getBoundingClientRect();
    return { x: (e.clientX - r.left - this.tx) / this.scale, y: (e.clientY - r.top - this.ty) / this.scale };
  }
  nodeAt(p) {
    for (let i = this.nodes.length - 1; i >= 0; i--) {
      const n = this.nodes[i];
      const d2 = (n.x - p.x) ** 2 + (n.y - p.y) ** 2;
      if (d2 < (n.r + 4) ** 2) return n;
    }
    return null;
  }
  onDown(e) {
    const n = this.nodeAt(this.toWorld(e));
    if (n) { this.drag = n; this.alpha = Math.max(this.alpha, .35); }
    else { this.pan = { x: e.clientX - this.tx, y: e.clientY - this.ty }; this.cv.classList.add('dragging'); }
  }
  onMove(e) {
    if (this.drag) {
      const p = this.toWorld(e);
      this.drag.x = p.x; this.drag.y = p.y;
      this.drag.vx = this.drag.vy = 0;
      this.alpha = Math.max(this.alpha, .25);
      this.moved = true;
      tipHide();
      return;
    }
    if (this.pan) {
      this.tx = e.clientX - this.pan.x; this.ty = e.clientY - this.pan.y;
      this.moved = true;
      tipHide();
      return;
    }
    const n = this.nodeAt(this.toWorld(e));
    this.hover = n;
    this.cv.style.cursor = this.linkMode ? 'crosshair' : n ? 'pointer' : 'grab';
    if (n) tipShow(
      `<b>${esc(n.type)}</b> · ${esc(n.uid)}<br>${esc(n.label)}${n.domain ? `<br><span style="color:var(--ink-3)">${esc(n.domain)}</span>` : ''}`,
      e.clientX, e.clientY);
    else tipHide();
  }
  onUp() { this.drag = null; this.pan = null; this.cv.classList.remove('dragging'); }
  onWheel(e) {
    e.preventDefault();
    const r = this.cv.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const f = e.deltaY < 0 ? 1.13 : 1 / 1.13;
    const ns = Math.min(4, Math.max(.12, this.scale * f));
    this.tx = mx - (mx - this.tx) * (ns / this.scale);
    this.ty = my - (my - this.ty) * (ns / this.scale);
    this.scale = ns;
  }
  onClick(e) {
    if (this.moved) { this.moved = false; return; }
    const n = this.nodeAt(this.toWorld(e));
    if (this.linkMode && n) {
      if (!this.linkFrom) {
        this.linkFrom = n;
        $('#gBanner').textContent = t('g.banner.target', { uid: n.uid });
      } else if (n !== this.linkFrom) {
        this.promptLink(this.linkFrom, n);
      }
      return;
    }
    this.selected = n;
    this.renderCard();
  }
  toggleLinkMode() {
    this.linkMode = !this.linkMode;
    this.linkFrom = null;
    this.cv.classList.toggle('linkmode', this.linkMode);
    const b = $('#gBanner');
    b.hidden = !this.linkMode;
    if (this.linkMode) b.textContent = t('g.banner.source');
    $('#gLink').classList.toggle('btn-solid', this.linkMode);
  }
  promptLink(a, b) {
    const modal = openModal({
      title: t('g.modal.title'),
      bodyHTML: `
        <div style="display:grid;gap:6px;font-size:11.5px">
          <div><span class="dot" style="--c:${typeColor(a.type)};display:inline-block;margin-right:6px"></span>${esc(a.uid)} · ${esc(a.label)}</div>
          <div style="color:var(--accent);padding-left:2px">↓</div>
          <div><span class="dot" style="--c:${typeColor(b.type)};display:inline-block;margin-right:6px"></span>${esc(b.uid)} · ${esc(b.label)}</div>
        </div>
        <div class="field"><label>${t('g.modal.relType')}</label>
          <input type="text" id="glType" list="glTypesDL" value="relates_to">
          <datalist id="glTypesDL">${relOptions()}</datalist></div>
        <div class="field"><label>${t('g.modal.note')}</label><input type="text" id="glNote"></div>`,
      footHTML: `<button class="btn" data-x>${t('common.cancel')}</button><button class="btn btn-solid" data-ok>${t('g.modal.create')}</button>`,
    });
    modal.querySelector('[data-x]').onclick = () => { closeModal(); this.linkFrom = null; };
    modal.querySelector('[data-ok]').onclick = async () => {
      try {
        await api('/api/relations', { body: {
          from_uid: a.uid, to_uid: b.uid,
          relation_type: $('#glType').value.trim() || 'relates_to',
          note: $('#glNote').value } });
        closeModal();
        toast(t('dr.rel.created'), 'ok');
        this.toggleLinkMode();
        refreshBehind();
      } catch (err) { toast(err.message, 'bad'); }
    };
  }
  renderCard() {
    const card = $('#gCard');
    if (!this.selected) { card.hidden = true; card.innerHTML = ''; return; }
    const n = this.selected;
    card.className = 'graph-card';
    card.hidden = false;
    card.innerHTML = `
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        ${typeTag(n.type)} ${uidChip(n.uid)} ${statusTag(n.status)} ${confPill(n.confidence)}
      </div>
      <div class="snippet">${esc(n.label)}</div>
      <div class="act-row">
        <button class="btn btn-sm btn-solid" data-openrec>${t('common.openRecord')}</button>
        ${n.domain ? `<span class="chip">${esc(n.domain)}</span>` : ''}
        <span class="chip">${t('g.links', { n: n.degree })}</span>
      </div>`;
    wireCopyChips(card);
    card.querySelector('[data-openrec]').addEventListener('click', () => openRecord(n.uid));
  }
  physics() {
    if (this.alpha < .02) return;
    const N = this.nodes;
    for (let i = 0; i < N.length; i++) {
      const a = N[i];
      for (let j = i + 1; j < N.length; j++) {
        const b = N[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = (Math.random() - .5); dy = (Math.random() - .5); d2 = 1; }
        if (d2 > 160000) continue;
        const f = 900 / d2 * this.alpha;
        const d = Math.sqrt(d2);
        dx /= d; dy /= d;
        a.vx += dx * f; a.vy += dy * f;
        b.vx -= dx * f; b.vy -= dy * f;
      }
    }
    for (const e of this.edges) {
      const a = this.byUid[e.from_uid], b = this.byUid[e.to_uid];
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.max(1, Math.hypot(dx, dy));
      const f = (d - 85) * .012 * this.alpha;
      a.vx += dx / d * f; a.vy += dy / d * f;
      b.vx -= dx / d * f; b.vy -= dy / d * f;
    }
    for (const n of N) {
      n.vx -= n.x * .0016 * this.alpha;
      n.vy -= n.y * .0016 * this.alpha;
      if (n === this.drag) continue;
      n.vx *= .86; n.vy *= .86;
      n.x += n.vx; n.y += n.vy;
    }
    this.alpha *= .996;
  }
  draw() {
    const { cx } = this;
    cx.clearRect(0, 0, this.w, this.h);
    cx.save();
    cx.translate(this.tx, this.ty);
    cx.scale(this.scale, this.scale);

    cx.strokeStyle = this.colEdge;
    cx.lineWidth = 1 / this.scale;
    for (const e of this.edges) {
      const a = this.byUid[e.from_uid], b = this.byUid[e.to_uid];
      cx.beginPath(); cx.moveTo(a.x, a.y); cx.lineTo(b.x, b.y); cx.stroke();
      const d = Math.hypot(b.x - a.x, b.y - a.y) || 1;
      const ux = (b.x - a.x) / d, uy = (b.y - a.y) / d;
      const px = b.x - ux * (b.r + 4), py = b.y - uy * (b.r + 4);
      const s = 4 / Math.sqrt(this.scale);
      cx.beginPath();
      cx.moveTo(px, py);
      cx.lineTo(px - ux * s - uy * s * .6, py - uy * s + ux * s * .6);
      cx.lineTo(px - ux * s + uy * s * .6, py - uy * s - ux * s * .6);
      cx.closePath();
      cx.fillStyle = this.colArrow;
      cx.fill();
    }

    for (const n of this.nodes) {
      const color = typeColor(n.type);
      cx.beginPath();
      if (n.type === 'anti_pattern') {          /* diamond: secondary encoding for the red↔green CVD pair */
        cx.moveTo(n.x, n.y - n.r); cx.lineTo(n.x + n.r, n.y);
        cx.lineTo(n.x, n.y + n.r); cx.lineTo(n.x - n.r, n.y);
        cx.closePath();
      } else {
        cx.arc(n.x, n.y, n.r, 0, 7);
      }
      if (n.status === 'archived') {
        cx.fillStyle = this.colRing; cx.fill();
        cx.strokeStyle = color; cx.lineWidth = 1.4 / this.scale; cx.stroke();
      } else {
        cx.fillStyle = color; cx.fill();
        cx.strokeStyle = this.colRing; cx.lineWidth = 2 / this.scale; cx.stroke();  /* surface ring */
      }
      if (n === this.selected || n === this.linkFrom) {
        cx.beginPath();
        cx.arc(n.x, n.y, n.r + 5, 0, 7);
        cx.strokeStyle = this.colAccent;
        cx.lineWidth = 1.6 / this.scale;
        if (n === this.linkFrom) cx.setLineDash([4 / this.scale, 3 / this.scale]);
        cx.stroke();
        cx.setLineDash([]);
      }
    }

    if (this.hover && this.scale > .35) {
      const n = this.hover;
      cx.font = `${11 / this.scale}px 'Roboto Mono', monospace`;
      cx.fillStyle = this.colLabel;
      cx.fillText(n.label.slice(0, 46), n.x + n.r + 7 / this.scale, n.y + 4 / this.scale);
    }
    cx.restore();
  }
  loop() {
    if (!this.running) return;
    this.physics();
    this.draw();
    requestAnimationFrame(this.loop);
  }
}

/* ═══ VIEW · domains ════════════════════════════════════════════════ */

async function renderDomains(view) {
  const domains = await getDomains(true);
  const rows = domains.map(d => {
    const total = d.active + d.archived;
    const dots = TYPE_ORDER.filter(t => d.types[t]).map(t =>
      `<span class="dot" style="--c:${typeColor(t)}" title="${t}: ${d.types[t]}"></span>`).join('');
    const collide = d.collides_with
      ? `<span class="collide-chip" data-merge="${esc(d.collides_with[0])}" data-into="${esc(d.domain)}"
           title="${t('do.collide.title', { list: esc(d.collides_with.join(', ')) })}">≈ ${esc(d.collides_with[0])}</span>` : '';
    return `<tr>
      <td style="color:var(--ink)">${esc(d.domain)} ${collide}</td>
      <td class="num">${fmtInt(d.active)}</td>
      <td class="num" style="color:var(--ink-4)">${fmtInt(d.archived)}</td>
      <td><span class="type-dots">${dots}</span></td>
      <td class="num" style="color:var(--ink-4)" title="${esc(d.latest_at)}">${fmtAgo(d.latest_at)}</td>
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
    <div class="panel">
      <table class="table">
        <thead><tr><th>${t('common.domain')}</th><th class="num">${t('do.th.active')}</th><th class="num">${t('do.th.archived')}</th><th>${t('do.th.types')}</th><th class="num">${t('common.lastActivity')}</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      ${rows ? '' : `<div class="empty">${t('do.empty')}</div>`}
    </div>
  </div>`;

  view.querySelectorAll('[data-see]').forEach(b =>
    b.addEventListener('click', () => go('memories', { domain: b.dataset.see, status: '' })));
  view.querySelectorAll('[data-ren]').forEach(b =>
    b.addEventListener('click', () => openRenameModal(b.dataset.ren, domains)));
  view.querySelectorAll('[data-merge]').forEach(chip =>
    chip.addEventListener('click', () => openRenameModal(chip.dataset.merge, domains, chip.dataset.into)));
}

function openRenameModal(from, domains, presetTo = '') {
  const dl = domains.map(d => `<option value="${esc(d.domain)}">`).join('');
  const modal = openModal({
    title: presetTo ? t('do.rn.merge') : t('do.rn.rename'),
    bodyHTML: `
      <div class="field"><label>${t('do.rn.from')}</label><input type="text" value="${esc(from)}" disabled></div>
      <div class="field"><label>${t('do.rn.to')}</label>
        <input type="text" id="rnTo" value="${esc(presetTo)}" list="rnDL" placeholder="${t('do.rn.placeholder')}">
        <datalist id="rnDL">${dl}</datalist></div>
      <div id="rnWarn" style="font-size:11.5px;color:var(--warn)" hidden>${t('do.rn.warn')}</div>
      <div style="font-size:11px;color:var(--ink-4)">${t('do.rn.hint')}</div>`,
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
      _domainsCache = null;
      refreshBehind();
    } catch (err) { toast(err.message, 'bad'); }
  };
}

/* ═══ VIEW · maintenance ════════════════════════════════════════════ */

async function renderMaintenance(view) {
  view.innerHTML = `<div class="anim">
    <div class="view-head">
      <h2 class="view-title">${t('mn.title')}</h2>
      <div class="view-sub">${t('mn.sub')}</div>
    </div>
    <div class="grid grid-2" style="margin-bottom:14px">
      <div class="panel">
        <h3 class="panel-title">${t('mn.health')} <button class="btn btn-sm" id="hRefresh">${t('mn.rerun')}</button></h3>
        <div id="healthBody"><div class="loading"><span class="spin"></span></div></div>
      </div>
      <div class="panel">
        <h3 class="panel-title">${t('mn.ops')}</h3>
        <div class="mnt-actions">
          <button class="btn" data-op="fts">${t('mn.op.fts')}</button>
          <button class="btn" data-op="reembed-missing">${t('mn.op.reembedMissing')}</button>
          <button class="btn" data-op="reembed-all">${t('mn.op.reembedAll')}</button>
          <button class="btn" data-op="orphans">${t('mn.op.orphans')}</button>
          <button class="btn" data-op="vacuum">${t('mn.op.vacuum')}</button>
          <button class="btn btn-solid" data-op="backup">${t('mn.op.backup')}</button>
        </div>
        <h3 class="panel-title" style="margin-top:20px">${t('mn.backups')}</h3>
        <div id="backupsBody" style="font-size:11.5px;color:var(--ink-3)">—</div>
      </div>
    </div>

    <div class="panel" style="margin-bottom:14px">
      <h3 class="panel-title">${t('mn.dd.title')}
        <span class="panel-aside">${t('mn.dd.aside')}</span></h3>
      <div class="list-toolbar" style="margin-bottom:6px">
        <label style="display:flex;align-items:center;gap:8px;font-size:11.5px;color:var(--ink-3)">
          ${t('mn.dd.threshold')} <input type="range" id="ddThr" min="0.45" max="0.95" step="0.05" value="0.60" style="width:130px">
          <b id="ddThrVal" style="color:var(--ink)">0.60</b></label>
        <select id="ddType"><option value="">${t('common.allTypes')}</option>
          ${TYPE_ORDER.map(tp => `<option value="${tp}">${TYPE_LABEL[tp]}</option>`).join('')}</select>
        <input type="text" id="ddDomain" placeholder="${t('mn.dd.domainPh')}" list="ddDomainsDL" style="max-width:200px">
        <datalist id="ddDomainsDL"></datalist>
        <button class="btn btn-solid btn-sm" id="ddRun">${t('mn.dd.run')}</button>
      </div>
      <div id="ddBody"><div class="empty">${t('mn.dd.hint')}</div></div>
    </div>

    <div class="panel">
      <h3 class="panel-title">${t('mn.au.title')} <span class="panel-aside">${t('mn.au.aside')}</span>
        <button class="btn btn-sm" id="auRefresh">${t('common.refresh')}</button></h3>
      <div id="auditBody"><div class="loading"><span class="spin"></span></div></div>
    </div>
  </div>`;

  getDomains().then(ds => {
    $('#ddDomainsDL').innerHTML = ds.map(d => `<option value="${esc(d.domain)}">`).join('');
  }).catch(() => {});

  const loadHealth = async () => {
    const h = await api('/api/maintenance/health');
    const rows = [];
    const push = (level, name, detail) =>
      rows.push(`<div class="check-row"><span class="check-dot ${level}"></span>
        <span class="check-name">${name}</span><span class="check-detail">${detail}</span></div>`);
    push(h.integrity.ok ? 'ok' : 'bad', t('mn.h.integrity'), h.integrity.detail ? esc(h.integrity.detail) : t('mn.h.quickClean'));
    push(h.fts.ok ? 'ok' : 'bad', t('mn.h.fts'),
      `${esc(h.fts.detail)} · ${t('mn.h.rows', { a: fmtInt(h.fts.rows), b: fmtInt(h.fts.expected) })}`);
    if (!h.vectors.ready) push('warn', t('mn.h.vectors'), t('mn.h.vecUnavailable'));
    else push(h.vectors.missing === 0 && h.vectors.orphans === 0 ? 'ok' : 'warn', t('mn.h.vectors'),
      `${t('mn.h.vecDetail', { a: fmtInt(h.vectors.rows), b: fmtInt(h.vectors.expected), m: h.vectors.missing, o: h.vectors.orphans })} · ${esc((h.vectors.model || '').split(/[\\/]/).pop())} ${esc(h.vectors.dim)}d${h.vectors.model_available ? '' : t('mn.h.modelUnavailable')}`);
    push(h.relations.orphans === 0 ? 'ok' : 'warn', t('mn.h.relations'),
      h.relations.orphans === 0 ? t('mn.h.noOrphans') : t('mn.h.orphanEdges', { n: h.relations.orphans }));
    push(h.file.reclaimable > 262144 ? 'warn' : 'ok', t('mn.h.disk'),
      t('mn.h.diskDetail', { size: fmtBytes(h.file.size), wal: h.file.wal_size ? ` + ${fmtBytes(h.file.wal_size)} WAL` : '', rec: fmtBytes(h.file.reclaimable) }));
    $('#healthBody').innerHTML = rows.join('');
    $('#backupsBody').innerHTML = h.backups.length
      ? h.backups.map(b => `<div class="backup-row"><span>${esc(b.name)}</span><span>${fmtBytes(b.size)}</span></div>`).join('')
      : t('mn.backups.empty');
  };
  loadHealth().catch(err => { $('#healthBody').innerHTML = `<div class="empty">${esc(err.message)}</div>`; });
  $('#hRefresh').addEventListener('click', () => loadHealth().catch(e => toast(e.message, 'bad')));

  const OPS = {
    'fts': { path: '/api/maintenance/fts-rebuild', body: {}, msg: r => t('mn.msg.fts', { n: fmtInt(r.rows) }) },
    'reembed-missing': { path: '/api/maintenance/reembed', body: { mode: 'missing' }, msg: r => t('mn.msg.backfilled', { n: fmtInt(r.embedded), t: fmtInt(r.total) }) },
    'reembed-all': { path: '/api/maintenance/reembed', body: { mode: 'all' }, confirm: t('mn.confirm.reembedAll'), msg: r => t('mn.msg.recomputed', { n: fmtInt(r.total) }) },
    'orphans': { path: '/api/maintenance/clean-orphans', body: {}, msg: r => t('mn.msg.orphans', { r: r.relations_removed, v: r.vectors_removed }) },
    'vacuum': { path: '/api/maintenance/vacuum', body: {}, msg: r => t('mn.msg.vacuum', { a: fmtBytes(r.before), b: fmtBytes(r.after) }) },
    'backup': { path: '/api/maintenance/backup', body: {}, msg: r => t('mn.msg.backup', { name: r.path.split(/[\\/]/).pop(), size: fmtBytes(r.size) }) },
  };
  view.querySelectorAll('[data-op]').forEach(b => b.addEventListener('click', async () => {
    const op = OPS[b.dataset.op];
    if (op.confirm && !(await confirmModal({ title: t('mn.confirm.title'), body: op.confirm, okLabel: t('common.run') }))) return;
    b.disabled = true;
    const prev = b.textContent;
    b.innerHTML = '<span class="spin"></span>';
    try {
      const r = await api(op.path, { body: op.body });
      toast(op.msg(r), 'ok');
      loadHealth().catch(() => {});
    } catch (err) { toast(err.message, 'bad'); }
    b.disabled = false;
    b.textContent = prev;
  }));

  /* dedup */
  $('#ddThr').addEventListener('input', e => {
    $('#ddThrVal').textContent = Number(e.target.value).toFixed(2);
  });
  $('#ddRun').addEventListener('click', async () => {
    const body = $('#ddBody');
    body.innerHTML = '<div class="loading"><span class="spin"></span></div>';
    try {
      const qs = new URLSearchParams({ threshold: $('#ddThr').value });
      if ($('#ddType').value) qs.set('type', $('#ddType').value);
      if ($('#ddDomain').value.trim()) qs.set('domain', $('#ddDomain').value.trim());
      const r = await api(`/api/maintenance/dedup?${qs}`);
      if (!r.pairs.length) { body.innerHTML = `<div class="empty">${t('mn.dd.none')}</div>`; return; }
      body.innerHTML = r.pairs.map((p, i) => `
        <div class="dedup-pair">
          <div style="display:flex;justify-content:space-between;align-items:baseline">
            <span style="font-size:11px;color:var(--ink-3)">${t('mn.dd.overlap')} <b style="color:var(--ink)">${(p.ratio * 100).toFixed(0)}%</b></span>
            <button class="btn btn-sm" data-linkdup="${i}">${t('mn.dd.linkDup')}</button>
          </div>
          <div class="ratio-bar"><div class="ratio-fill" style="width:${(p.ratio * 100).toFixed(0)}%"></div></div>
          <div class="pair-cards">
            ${[p.a, p.b].map(mm => `
              <div class="pair-card">
                <div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap">
                  ${typeTag(mm.type)} ${uidChip(mm.uid)} ${statusTag(mm.status)}
                  <span style="color:var(--ink-4);font-size:10px">${fmtDate(mm.created_at)}</span>
                </div>
                ${mm.domain ? `<span class="chip">${esc(mm.domain)}</span>` : ''}
                <div class="snippet">${esc(mm.content)}</div>
                <div class="act-row">
                  <button class="btn btn-sm" data-openm="${esc(mm.uid)}">${t('common.openRecord')}</button>
                  <button class="btn btn-sm" data-archm="${esc(mm.uid)}">${t('mn.dd.archiveThis')}</button>
                </div>
              </div>`).join('')}
          </div>
        </div>`).join('');
      wireCopyChips(body);
      body.querySelectorAll('[data-openm]').forEach(b2 =>
        b2.addEventListener('click', () => openRecord(b2.dataset.openm)));
      body.querySelectorAll('[data-archm]').forEach(b2 =>
        b2.addEventListener('click', async () => {
          const reason = await promptModal({
            title: t('mn.dd.archTitle'), label: t('bulk.reason.label'),
            placeholder: t('mn.dd.archPh'), okLabel: t('common.archive'), danger: true });
          if (reason === null) return;
          try {
            await api(`/api/memories/${b2.dataset.archm}/status`, { body: { status: 'archived', reason: reason || t('mn.dd.dupReason') } });
            toast(t('dr.archived'), 'ok');
            b2.closest('.pair-card').style.opacity = .4;
          } catch (err) { toast(err.message, 'bad'); }
        }));
      body.querySelectorAll('[data-linkdup]').forEach(b2 =>
        b2.addEventListener('click', async () => {
          const p = r.pairs[b2.dataset.linkdup];
          try {
            await api('/api/relations', { body: { from_uid: p.a.uid, to_uid: p.b.uid, relation_type: 'duplicates', note: t('mn.dd.linkNote', { p: (p.ratio * 100).toFixed(0) }) } });
            toast(t('mn.dd.linked'), 'ok');
            b2.disabled = true;
          } catch (err) { toast(err.message, 'bad'); }
        }));
    } catch (err) { body.innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
  });

  /* audit */
  const loadAudit = async () => {
    const r = await api('/api/audit?limit=120');
    $('#auditBody').innerHTML = r.entries.length ? `
      <table class="table">
        <thead><tr><th>${t('mn.au.th.when')}</th><th>${t('mn.au.th.memory')}</th><th>${t('common.domain')}</th><th>${t('mn.au.th.event')}</th><th class="num">${t('mn.au.th.delta')}</th></tr></thead>
        <tbody>${r.entries.map(e => `
          <tr style="cursor:pointer" data-uid="${esc(e.memory_uid)}">
            <td style="white-space:nowrap" title="${esc(e.edited_at)}">${fmtDate(e.edited_at)}</td>
            <td><span class="type-tag ${typeClass(e.type)}"><span class="dot"></span>${esc(e.memory_uid)}</span></td>
            <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(e.domain || '—')}</td>
            <td style="max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(e.note)}">${esc(e.note || '') || t('mn.au.contentEdit')}</td>
            <td class="num">${e.content_changed ? `${e.prev_len} → ${e.new_len}` : '<span style="color:var(--ink-4)">—</span>'}</td>
          </tr>`).join('')}</tbody>
      </table>` : `<div class="empty">${t('mn.au.empty')}</div>`;
    $('#auditBody').querySelectorAll('[data-uid]').forEach(tr =>
      tr.addEventListener('click', () => openRecord(tr.dataset.uid)));
  };
  loadAudit().catch(err => { $('#auditBody').innerHTML = `<div class="empty">${esc(err.message)}</div>`; });
  $('#auRefresh').addEventListener('click', () => loadAudit().catch(e => toast(e.message, 'bad')));
}

/* ═══ VIEW · optimization ═══════════════════════════════════════════ */

function optBefore(s) {
  const tg = s.target || {};
  switch (s.kind) {
    case 'compact': case 'reword': return esc(tg.snippet || '');
    case 'retag': return esc(tg.tags || '—');
    case 'redomain': return esc(tg.domain || '—');
    case 'set_confidence': return esc(tg.confidence || '—');
    case 'archive': return esc(tg.status || 'active');
    default: return '';
  }
}
function optAfter(s) {
  const p = s.payload || {};
  switch (s.kind) {
    case 'compact': case 'reword': return esc(p.new_content || '');
    case 'retag': return esc(p.tags || '—');
    case 'redomain': return esc(p.domain || '—');
    case 'set_confidence': return esc(p.confidence || '—');
    case 'archive': return 'archived' + (p.reason ? ` · ${esc(p.reason)}` : '');
    default: return '';
  }
}
function optRelBody(s) {
  const p = s.payload || {}, peers = s.peers || {};
  const pair = s.kind === 'link'
    ? [[t('op.role.from'), peers.from_uid], [t('op.role.to'), peers.to_uid]]
    : [[t('op.role.keep'), peers.keep_uid], [t('op.role.drop'), peers.drop_uid]];
  const rel = s.kind === 'link' ? esc(p.relation_type || 'relates_to') : 'supersedes';
  const areas = ['l1', 'l2'], bodies = ['b1', 'b2'];
  return `<div class="opt-rel">
    ${pair.map(([role, m], i) => `
      <span class="opt-label" style="grid-area:${areas[i]}">${esc(role)}</span>
      <div class="opt-peer-body" style="grid-area:${bodies[i]}">
        ${m ? `<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">${typeTag(m.type)} ${uidChip(m.uid)} ${statusTag(m.status)}</div>
          <div class="snippet">${esc(m.snippet || '')}</div>` : `<div class="snippet">${t('op.missing')}</div>`}
      </div>`).join('')}
    <div class="opt-arrow" title="${t('op.relType.title')}">${rel} →</div>
  </div>`;
}
function optDistillBody(s) {
  const p = s.payload || {};
  const srcs = (s.sources || []).map(m => `
    <div class="opt-peer-body">
      ${m && !m.missing ? `<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">${typeTag(m.type)} ${uidChip(m.uid)} ${statusTag(m.status)}</div>
        <div class="snippet">${esc(m.snippet || '')}</div>` : `<div class="snippet">${t('op.missing')}</div>`}
    </div>`).join('');
  return `<div class="opt-distill">
    <span class="opt-label">${t('op.distill.sources', { n: (s.sources || []).length })}</span>
    ${srcs}
    <div class="opt-arrow" title="${t('op.relType.title')}">supersedes →</div>
    <span class="opt-label">${t('op.distill.new')}${p.new_type ? ` · ${esc(p.new_type)}` : ''}${p.domain ? ` · ${esc(p.domain)}` : ''}</span>
    <div class="opt-side snippet">${esc(p.new_content || '')}</div>
  </div>`;
}
function optButtons(s) {
  if (s.status === 'pending') return `
    <button class="btn btn-solid btn-sm" data-apply="${s.id}">${t('common.apply')}</button>
    <button class="btn btn-sm" data-reject="${s.id}">${t('common.reject')}</button>`;
  if (s.status === 'applied') return `<button class="btn btn-sm" data-revert="${s.id}">${t('common.undo')}</button>`;
  return '';
}
function optPreview(s) {
  if (!s.target) return '';
  return `<div class="opt-preview">
    <span class="opt-label">${t('op.underReview')}</span>
    <div class="snippet">${esc(s.target.snippet || '')}</div>
  </div>`;
}
function optCard(s) {
  const statusChip = s.status === 'applied' ? `<span class="status-tag opt-applied">${t('op.applied')}</span>`
    : s.status === 'rejected' ? `<span class="status-tag archived">${t('op.rejected')}</span>` : '';
  const verified = s.verified
    ? `<div class="opt-verified" title="${t('op.verifiedTitle')}">${t('op.verified', { v: esc(s.verified) })}</div>`
    : `<div class="opt-verified muted">${t('op.noVerified')}</div>`;
  const relKind = s.kind === 'link' || s.kind === 'merge' || s.kind === 'distill';
  const bodyHtml = s.kind === 'distill' ? optDistillBody(s)
    : relKind ? optRelBody(s) : `<div class="opt-diff">
      <span class="opt-label" style="grid-area:bl">${t('op.before')}</span>
      <span class="opt-label" style="grid-area:al">${t('op.after')}</span>
      <div class="opt-side snippet" style="grid-area:bs">${optBefore(s)}</div>
      <div class="opt-arrow" style="grid-area:arrow">→</div>
      <div class="opt-side snippet" style="grid-area:as">${optAfter(s)}</div>
    </div>`;
  const openUid = s.target_uid || s.new_uid;   /* distill: open the created memory once applied */
  return `<div class="pair-card opt-card ${s.status !== 'pending' ? 'decided' : ''}" data-sid="${s.id}">
    <div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap">
      <span class="type-tag t-note"><span class="dot"></span>${esc(s.kind)}</span>
      ${s.target_uid ? uidChip(s.target_uid) : ''}
      ${statusChip}
      <span style="flex:1"></span>
      ${openUid ? `<button class="btn btn-sm" data-openopt="${esc(openUid)}">${t('common.openRecord')}</button>` : ''}
      ${optButtons(s)}
    </div>
    ${s.rationale ? `<div class="opt-rationale">${esc(s.rationale)}</div>` : ''}
    ${relKind ? '' : optPreview(s)}
    ${bodyHtml}
    ${verified}
  </div>`;
}

async function renderOptimization(view, params) {
  const runs = (await api('/api/optimization/runs')).runs;
  const runId = +((params && params.get('run')) || 0);
  const meta = runId ? runs.find(r => r.id === runId) : null;
  if (meta) renderOptRun(view, meta);
  else renderOptRunList(view, runs);
}

/* level 1 — searchable grid of run cards */

function optRunCard(r) {
  const state = !r.total ? `<span class="opt-run-state s-empty">${t('op.card.empty')}</span>`
    : r.pending ? `<span class="opt-run-state s-pending">${t('op.card.pendingN', { n: r.pending })}</span>`
    : `<span class="opt-run-state s-done">${t('op.card.done')}</span>`;
  const seg = (n, color, label) => n
    ? `<div class="meter-seg" style="flex:${n};background:${color}" title="${esc(label)}: ${n}"></div>` : '';
  const meter = r.total ? `<div class="meter opt-run-meter">
      ${seg(r.applied, 'var(--ok)', t('op.applied'))}
      ${seg(r.pending, 'var(--warn)', t('op.pending'))}
      ${seg(r.rejected, 'var(--bad)', t('op.rejected'))}
    </div>` : '';
  const kinds = (r.kinds || []).map(k =>
    `<span class="opt-kind-chip${k.pending ? ' has-pending' : ''}">${esc(k.kind)}<b>${k.pending ? `${k.pending}/` : ''}${k.total}</b></span>`).join('');
  const backup = r.backup_path
    ? `<span class="opt-run-backup" title="${esc(t('op.backupNote', { name: r.backup_path.split(/[\\/]/).pop() }))}">${t('op.card.backup')}</span>` : '';
  return `<div class="opt-run-card" data-run="${r.id}" role="button" tabindex="0">
    <div class="opt-run-top">
      <span class="opt-run-id">#${r.id}</span>
      <span class="opt-run-date">${fmtDate(r.created_at)} · ${t('op.nSuggestions', { n: r.total })}</span>
      <span style="flex:1"></span>
      ${state}
    </div>
    ${r.note ? `<div class="opt-run-note" title="${esc(r.note)}">${esc(r.note)}</div>` : ''}
    ${meter}
    <div class="opt-run-foot">
      <span>${t('op.summary', { p: r.pending, a: r.applied, r: r.rejected })}</span>
      <span style="flex:1"></span>
      ${backup}
    </div>
    ${kinds ? `<div class="opt-run-kinds">${kinds}</div>` : ''}
  </div>`;
}

function renderOptRunList(view, runs) {
  view.innerHTML = `<div class="anim">
    <div class="view-head">
      <h2 class="view-title">${t('op.title')}</h2>
      <div class="view-sub">${t('op.sub')}</div>
    </div>
    ${runs.length ? `
    <div class="list-toolbar">
      <input type="search" id="optSearch" placeholder="${t('op.searchRuns')}">
      <button class="btn btn-sm" id="optOnlyPending">${t('op.onlyPending')}</button>
      <span class="panel-aside" id="optRunsCount"></span>
    </div>
    <div class="opt-run-grid" id="optRunGrid"></div>
    ` : `<div class="empty">${t('op.emptyRuns')}</div>`}
  </div>`;
  if (!runs.length) return;

  let q = '', onlyPending = false;
  const grid = $('#optRunGrid');
  const draw = () => {
    const needle = q.trim().toLowerCase();
    const shown = runs.filter(r => {
      if (onlyPending && !r.pending) return false;
      if (!needle) return true;
      const hay = `#${r.id} ${r.note || ''} ${fmtDate(r.created_at)} ${(r.kinds || []).map(k => k.kind).join(' ')}`.toLowerCase();
      return needle.split(/\s+/).every(w => hay.includes(w));
    });
    $('#optRunsCount').textContent = t('op.runsCount', { n: shown.length });
    grid.innerHTML = shown.length ? shown.map(optRunCard).join('')
      : `<div class="empty" style="grid-column:1/-1">${t('op.noRunsMatch')}</div>`;
    grid.querySelectorAll('.opt-run-card').forEach(card => {
      const open = () => go('optimization', { run: card.dataset.run });
      card.addEventListener('click', open);
      card.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
      });
    });
  };
  $('#optSearch').addEventListener('input', e => { q = e.target.value; draw(); });
  const pendBtn = $('#optOnlyPending');
  pendBtn.addEventListener('click', () => {
    onlyPending = !onlyPending;
    pendBtn.classList.toggle('btn-solid', onlyPending);
    draw();
  });
  draw();
}

/* level 2 — one run, suggestions grouped by kind */

function renderOptRun(view, initialMeta) {
  const runId = initialMeta.id;
  let meta = initialMeta;
  view.innerHTML = `<div class="anim">
    <a class="opt-back" href="#/optimization">${t('op.backToRuns')}</a>
    <div class="view-head" style="margin-top:8px">
      <h2 class="view-title">${t('op.runTitle', { id: runId })} <em class="opt-run-when">· ${fmtDate(meta.created_at)}</em></h2>
      ${meta.note ? `<div class="view-sub">${esc(meta.note)}</div>` : ''}
    </div>
    <div class="panel" style="margin-bottom:14px">
      <div class="list-toolbar" style="align-items:center;margin-bottom:0">
        <span id="optSummary" class="panel-aside"></span>
        <span style="flex:1"></span>
        <button class="btn btn-sm" id="optHideApplied"></button>
        <button class="btn btn-sm" id="optApplyAll">${t('op.applyAll')}</button>
        <button class="btn btn-danger btn-sm" id="optDiscard">${t('op.discard')}</button>
      </div>
      <div id="optBackup" style="font-size:11px;color:var(--ink-4);margin-top:6px"></div>
    </div>
    <div id="optBody"><div class="loading"><span class="spin"></span></div></div>
  </div>`;

  const syncMeta = () => {
    $('#optSummary').textContent = t('op.summary', { p: meta.pending, a: meta.applied, r: meta.rejected });
    $('#optBackup').textContent = meta.backup_path
      ? t('op.backupNote', { name: meta.backup_path.split(/[\\/]/).pop() }) : '';
  };
  const refreshMeta = async () => {
    const rs = (await api('/api/optimization/runs')).runs;
    meta = rs.find(r => r.id === runId) || meta;
    syncMeta();
  };
  syncMeta();

  let hideApplied = false;
  const hideBtn = $('#optHideApplied');
  const syncHideBtn = () => {
    hideBtn.textContent = hideApplied ? t('op.showApplied') : t('op.hideApplied');
    hideBtn.classList.toggle('btn-solid', hideApplied);
  };
  syncHideBtn();

  const loadRun = async () => {
    const body = $('#optBody');
    body.innerHTML = '<div class="loading"><span class="spin"></span></div>';
    try {
      const r = await api(`/api/optimization/suggestions?run=${runId}`);
      if (!r.suggestions.length) { body.innerHTML = `<div class="empty">${t('op.emptyRun')}</div>`; return; }
      const shown = hideApplied ? r.suggestions.filter(s => s.status !== 'applied') : r.suggestions;
      if (!shown.length) { body.innerHTML = `<div class="empty">${t('op.allApplied')}</div>`; return; }

      const groups = new Map();
      shown.forEach(s => { if (!groups.has(s.kind)) groups.set(s.kind, []); groups.get(s.kind).push(s); });
      body.innerHTML = [...groups.entries()].map(([kind, list]) => {
        const pend = list.filter(s => s.status === 'pending').length;
        const count = pend ? t('op.group.countPending', { p: pend, t: list.length })
          : t('op.group.countAll', { t: list.length });
        return `<details class="opt-group" open>
          <summary>
            <span class="opt-group-caret" aria-hidden="true"></span>
            <span class="opt-group-kind">${esc(kind)}</span>
            <span class="opt-group-count">${count}</span>
            <span style="flex:1"></span>
            ${pend ? `<button class="btn btn-sm" data-applykind="${esc(kind)}" data-npend="${pend}">${t('op.group.apply', { n: pend })}</button>` : ''}
          </summary>
          <div class="opt-group-body">${list.map(optCard).join('')}</div>
        </details>`;
      }).join('');
      wireCopyChips(body);

      const act = (btn, path, bodyObj) => async () => {
        btn.disabled = true;
        try {
          const res = await api(path, { body: bodyObj });
          toast(res && res.backup ? t('op.toast.appliedBackup') : t('op.toast.done'), 'ok');
          await refreshMeta();
          await loadRun();
        } catch (err) { toast(err.message, 'bad'); btn.disabled = false; }
      };
      body.querySelectorAll('[data-apply]').forEach(b => b.addEventListener('click', act(b, '/api/optimization/apply', { id: +b.dataset.apply })));
      body.querySelectorAll('[data-reject]').forEach(b => b.addEventListener('click', act(b, '/api/optimization/reject', { id: +b.dataset.reject })));
      body.querySelectorAll('[data-revert]').forEach(b => b.addEventListener('click', act(b, '/api/optimization/revert', { id: +b.dataset.revert })));
      body.querySelectorAll('[data-openopt]').forEach(b => b.addEventListener('click', () => openRecord(b.dataset.openopt)));
      body.querySelectorAll('[data-applykind]').forEach(b => b.addEventListener('click', async e => {
        e.preventDefault();   /* keep the <details> from toggling */
        e.stopPropagation();
        const kind = b.dataset.applykind, n = +b.dataset.npend;
        if (!(await confirmModal({ title: t('op.group.applyConfirm.title'),
          body: t('op.group.applyConfirm.body', { n, kind, id: runId }),
          okLabel: t('op.group.applyConfirm.ok') }))) return;
        b.disabled = true;
        try {
          const res = await api('/api/optimization/apply-all', { body: { run: runId, kind } });
          toast(t('op.toast.appliedN', { n: res.applied }) + (res.failed.length ? t('op.toast.failedN', { m: res.failed.length }) : ''), res.failed.length ? 'bad' : 'ok');
          await refreshMeta();
          await loadRun();
        } catch (err) { toast(err.message, 'bad'); b.disabled = false; }
      }));
    } catch (err) { body.innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
  };

  hideBtn.addEventListener('click', () => {
    hideApplied = !hideApplied;
    syncHideBtn();
    loadRun();
  });

  $('#optApplyAll').addEventListener('click', async () => {
    if (!meta.pending) { toast(t('op.toast.nothingPending'), ''); return; }
    if (!(await confirmModal({ title: t('op.applyAllConfirm.title'),
      body: t('op.applyAllConfirm.body', { n: meta.pending, id: runId }),
      okLabel: t('op.applyAllConfirm.ok') }))) return;
    try {
      const r = await api('/api/optimization/apply-all', { body: { run: runId } });
      toast(t('op.toast.appliedN', { n: r.applied }) + (r.failed.length ? t('op.toast.failedN', { m: r.failed.length }) : ''), r.failed.length ? 'bad' : 'ok');
      await refreshMeta();
      await loadRun();
    } catch (err) { toast(err.message, 'bad'); }
  });

  $('#optDiscard').addEventListener('click', async () => {
    if (!(await confirmModal({ title: t('op.discardConfirm.title'),
      body: t('op.discardConfirm.body', { id: runId }),
      okLabel: t('op.discardConfirm.ok'), danger: true }))) return;
    try {
      await api(`/api/optimization/runs/${runId}`, { method: 'DELETE' });
      toast(t('op.toast.discarded'), 'ok');
      go('optimization');
    } catch (err) { toast(err.message, 'bad'); }
  });

  loadRun();
}

/* ═══ new memory ════════════════════════════════════════════════════ */

async function openNewMemory() {
  const domains = await getDomains().catch(() => []);
  const modal = openModal({
    title: t('nm.title'),
    bodyHTML: `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <div class="field"><label>${t('nm.type')}</label>
          <select id="nmType">${TYPE_ORDER.map(tp => `<option ${tp === 'note' ? 'selected' : ''}>${tp}</option>`).join('')}</select></div>
        <div class="field"><label>${t('nm.conf')}</label>
          <select id="nmConf">${Object.keys(CONF).map(c => `<option value="${c}">${CONF[c].label}</option>`).join('')}</select></div>
      </div>
      <div class="field"><label>${t('nm.domain')}</label>
        <input type="text" id="nmDomain" list="nmDomainsDL" placeholder="${t('nm.domainPh')}">
        <datalist id="nmDomainsDL">${domains.map(d => `<option value="${esc(d.domain)}">`).join('')}</datalist></div>
      <div class="field"><label>${t('nm.tags')}</label>
        <input type="text" id="nmTags" placeholder="${t('nm.tagsPh')}"></div>
      <div class="field"><label>${t('nm.content')}</label>
        <textarea id="nmContent" rows="7" placeholder="${t('nm.contentPh')}"></textarea></div>`,
    footHTML: `<button class="btn" data-x>${t('common.cancel')}</button><button class="btn btn-solid" data-ok>${t('nm.create')}</button>`,
  });
  modal.querySelector('[data-x]').onclick = closeModal;
  modal.querySelector('[data-ok]').onclick = async () => {
    try {
      const r = await api('/api/memories', { body: {
        type: $('#nmType').value, confidence: $('#nmConf').value,
        domain: $('#nmDomain').value, tags: $('#nmTags').value,
        content: $('#nmContent').value } });
      closeModal();
      toast(t('nm.created', { uid: r.uid }), 'ok');
      _domainsCache = null;
      refreshBehind();
      openRecord(r.uid);
    } catch (err) { toast(err.message, 'bad'); }
  };
}

/* ═══ boot ══════════════════════════════════════════════════════════ */

$('#btnNew').addEventListener('click', openNewMemory);

$('#globalSearch').addEventListener('keydown', e => {
  if (e.key === 'Enter' && e.target.value.trim()) {
    go('memories', { q: e.target.value.trim(), status: '' });
    e.target.value = '';
    e.target.blur();
  }
});

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if ($('#modalRoot').children.length) { closeModal(); return; }
    if (!drawer.hidden) closeDrawer();
    return;
  }
  if (e.key === '/' && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
    e.preventDefault();
    $('#globalSearch').focus();
  }
});

addEventListener('hashchange', route);
route();
/* rail health on first paint, independent of the landing view */
api('/api/overview').then(updateRail).catch(() => {});
