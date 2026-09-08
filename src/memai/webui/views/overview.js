/* Health: what state the store is in, and what to do about it.

   The landing view is a diagnosis rather than an inventory. Four axes over
   the active memories make one index; a ring says how much of the store a
   human has actually vetted; and under both, the countable defects that
   pull the index down -- each with the number, its share, and the button
   that opens exactly those rows.

   Every number here is served by /api/overview. The axes and the symptom
   predicates live in memai/admin.py and memai/db.py (health_axes,
   _SYMPTOMS); this module draws them and does no arithmetic the server has
   not already done, apart from the calendar, which is a reshaping of the
   activity rows and not a second count. */

import { esc, fmtInt, fmtDay } from '../core/dom.js';
import { api } from '../core/api.js';
import { tipShow, tipHide } from '../core/ui.js';
import { typeTag, CONF, TYPE_ORDER } from '../core/shared.js';
import { go } from '../core/router.js';
import { I18N, t } from '../i18n.js';

const CONF_ORDER = ['confirmed', 'unverified', 'contradicted'];
const confColor = c =>
  c === 'confirmed' ? 'var(--ok)' : c === 'contradicted' ? 'var(--bad)' : 'var(--warn)';

/* The four axes, in the order they are drawn. Their meaning is not restated
   here -- db.health_axes is where each one is defined, and a second wording
   of it in the view is the copy that goes stale. The title on the row is the
   catalog's, so it says the same thing in both languages. */
const AXES = ['curation', 'connectivity', 'freshness', 'organization'];

/* How a reading out of 100 is coloured, and the same three steps for the
   index and for each of its axes -- the index IS their mean, so a scale
   that changed between them would make the figure disagree with the bars
   under it. Green is fine, amber wants work, red is the thing to go and
   fix. */
const band = v => (v >= 75 ? 'var(--ok)' : v >= 50 ? 'var(--warn)' : 'var(--bad)');

/* Where a symptom's button goes. Everything that names a set of memories
   hands its own filter over, so the list that opens is the set that was
   counted; the two that do not are a view of their own. */
const SYMPTOM_ROUTE = {
  contradicted: 'memories', stale: 'memories', due: 'memories',
  unlinked: 'memories', untitled: 'memories', untagged: 'memories',
  diagrams: 'diagrams',
  /* The only one that counts something other than memories AND has no list
     of its own: both endpoints of a broken edge are the defect, so there is
     nothing to open. The button goes to the operation that clears them,
     which is why this entry carries its own params instead of the server's. */
  orphans: ['maintenance', { tab: 'storage' }],
};

export async function renderOverview(view, params, ctx) {
  const o = await api('/api/overview');
  if (ctx.stale()) return;

  const tot = o.totals;
  const h = o.health;
  const days = calendar(o.activity);
  const weeks = intoWeeks(days);
  const maxDay = Math.max(1, ...days.map(d => d.count));
  const total30 = days.reduce((a, d) => a + d.count, 0);
  const activeDays = days.filter(d => d.count > 0).length;

  view.innerHTML = `<div class="anim">
    <h2 class="sr-only">${t('ov.title')}</h2>
    <!-- The store-wide counts that are not one of the four axes. The file's
         path and size were here too and are on Maintenance, which is the
         view about the file. -->
    <div class="view-note">${t('ov.sub.store', {
      domains: fmtInt(tot.domains), relations: fmtInt(tot.relations),
      edits: fmtInt(tot.edits), sessions: fmtInt(tot.sessions) })}</div>

    <div class="hx-top">
      ${ringsPanel(h, o)}
      <div class="hx-right">
        ${symptomsPanel(o.symptoms, h.active)}
      </div>
    </div>

    <div class="grid grid-3232">
      <div class="panel">
        <h3 class="panel-title">${t('ov.activity.title')}
          <span class="panel-aside">${t('ov.activity.aside', {
            n: fmtInt(total30), days: days.length })}</span></h3>
        <div class="hx-cal">
          ${heatmap(weeks, maxDay, { total: total30, active: activeDays })}
          ${weekdayBars(days)}
          <div class="hx-cal-stats">
            <div><div class="mg-label">${t('ov.activity.avg')}</div>
              <div class="spark-stat">${(total30 / days.length).toFixed(1)}</div></div>
            <div><div class="mg-label">${t('ov.activity.peak')}</div>
              <div class="spark-stat">${fmtInt(maxDay)}</div></div>
            <div><div class="mg-label">${t('ov.activity.daysWith')}</div>
              <div class="spark-stat">${t('ov.activity.ofDays', { n: activeDays, all: days.length })}</div></div>
          </div>
        </div>
      </div>
      ${byTypePanel(o)}
    </div>
  </div>`;

  wire(view, o);
}

/* ─── the ring ────────────────────────────────────────────────────────────
   Segments are dash patterns on ONE circle rather than separate arcs, so
   they cannot drift apart, and the centre carries the figure in plain
   white: the arc is already saying how the reading went. */

const R = 90;
const C = 2 * Math.PI * R;

const track = () =>
  `<circle cx="100" cy="100" r="${R}" fill="none" stroke="var(--inset)" stroke-width="20"></circle>`;

/* `parts` is [{ value, color }] in drawing order, summing to `total`. */
function ringHTML(parts, total, midHTML, { label = '' } = {}) {
  let at = 0;
  const arcs = parts.map(({ value, color }) => {
    if (!value || !total) return '';
    const len = (value / total) * C;
    const arc = `<circle cx="100" cy="100" r="${R}" fill="none" stroke="${color}"
      stroke-width="20" stroke-dasharray="${len.toFixed(1)} ${(C - len).toFixed(1)}"
      stroke-dashoffset="${(-at).toFixed(1)}"></circle>`;
    at += len;
    return arc;
  }).join('');
  /* the graphic is decorative: what a screen reader gets is the figure in
     the middle and the legend beside it, both of them real text */
  return `<div class="hx-ring"${label ? ` title="${esc(label)}"` : ''}>
    <svg viewBox="0 0 200 200" aria-hidden="true">${track()}${arcs}</svg>
    <div class="hx-ring-mid">${midHTML}</div>
  </div>`;
}

/* ─── the card the ring and the index share ───────────────────────────────
   The ring is the confidence split: three segments whose proportions are
   the point. The index below it is one figure over four bars.

   The ring's percentage and the `curation` axis (db._HEALTH_AXES) are the
   same reading -- confirmed over active -- so the two always agree. */

function ringsPanel(h, o) {
  const total = CONF_ORDER.reduce((a, c) => a + (o.by_confidence[c] || 0), 0);
  const pct = total ? Math.round((o.by_confidence.confirmed || 0) * 100 / total) : 0;

  const ring = ringHTML(
    CONF_ORDER.map(c => ({ value: o.by_confidence[c] || 0, color: confColor(c) })),
    total,
    `<div class="hx-ring-pct">${pct}%</div>
     <div class="hx-ring-cap">${esc(CONF.confirmed.label)}</div>`,
    { label: t('ov.conf.ringLabel', { pct, label: CONF.confirmed.label }) });

  const legend = CONF_ORDER.map(c => `
    <button type="button" class="hx-legend-row" data-conf="${c}"
            title="${esc(t('ov.conf.open', { label: CONF[c].label }))}">
      <span class="hx-swatch" style="background:${confColor(c)}"></span>
      <span>${esc(CONF[c].label)}</span>
      <b class="${c === 'contradicted' ? 'hx-bad' : ''}">${fmtInt(o.by_confidence[c] || 0)}</b>
    </button>`).join('');

  return `<div class="panel hx-ring-panel">
    <h3 class="panel-title">${t('ov.conf.title')}
      <span class="panel-aside">${t('ov.aside.activeN', { n: fmtInt(total) })}</span></h3>
    ${ring}
    <div class="hx-legend">${legend}</div>
    ${indexHTML(h)}
  </div>`;
}

/* ─── the index, at the foot of the same card ─────────────────────────── */

function indexHTML(h) {
  /* The delta is shown only once a snapshot that old exists
     (db.health_since). A store the dashboard has not been opened on for a
     month has no earlier reading to compare against, and a delta against a
     younger one would misdate the window it claims to cover. */
  const delta = h.delta == null ? '' : `<span class="hx-delta ${h.delta < 0 ? 'down' : ''}">${
    h.delta > 0 ? '+' : ''}${h.delta} ${t('ov.hx.inDays', { n: h.delta_days })}</span>`;

  /* The axes are to the index what the legend is to the ring above: the
     four readings the one figure is the mean of. */
  const axes = AXES.map(a => `
    <div class="hx-axis">
      <span class="hx-axis-name" title="${esc(t(`ov.axis.${a}.why`))}">${t(`ov.axis.${a}`)}</span>
      <div class="bar-track"><div class="bar-fill"
           style="--v:${(h.axes[a] / 100).toFixed(4)};background:${band(h.axes[a])}"></div></div>
      <span class="hx-axis-val">${h.axes[a]}</span>
    </div>`).join('');

  return `<div class="hx-second">
    <div class="hx-second-head">
      <span class="hx-second-name">
        <span class="mg-label">${t('ov.hx.title')}</span>
        <span class="hx-score" style="color:${band(h.score)}">${
          h.score}<span class="hx-score-of">&nbsp;/ 100</span></span>
      </span>
      ${delta}
    </div>
    <div class="hx-axes">${axes}</div>
  </div>`;
}

/* ─── the symptoms ────────────────────────────────────────────────────── */

/* A symptom that counts something other than memories carries its own
   denominator (`of`) and no share of the store, so the share column names
   what that denominator IS -- flows, or relation rows. */
const OF_LABEL = { diagrams: 'ov.sym.ofFlows', orphans: 'ov.sym.ofRelations' };

function symptomRow(s, active) {
  const share = OF_LABEL[s.key]
    ? t(OF_LABEL[s.key], { n: fmtInt(s.count), all: fmtInt(s.of) })
    : active ? `${(s.share * 100).toFixed(1)}%` : '';
  return `<div class="hx-sym" data-sym="${esc(s.key)}">
    <span class="hx-sev sev-${esc(s.severity)}"></span>
    <span class="hx-sym-name">${t(`ov.sym.${s.key}`)}</span>
    <span class="hx-sym-n">${fmtInt(s.count)}</span>
    <span class="hx-sym-share">${share}</span>
    <button type="button" class="btn btn-sm hx-sym-go"${s.count ? '' : ' disabled'}
            >${t(`ov.sym.${s.key}.act`)}</button>
  </div>`;
}

function symptomsPanel(symptoms, active) {
  /* Worst first, then by how much of the store each covers -- and a symptom
     with a count of zero drops to the end rather than out of the list: that
     it is clean is worth reading, and a list whose length changes with the
     store's condition is one nobody learns the shape of. */
  const rank = { bad: 0, warn: 1, info: 2 };
  const rows = symptoms.slice().sort((a, b) =>
    Boolean(a.count) === Boolean(b.count)
      ? (rank[a.severity] - rank[b.severity]) || (b.count - a.count)
      : (a.count ? -1 : 1));
  return `<div class="panel hx-symptoms">
    <h3 class="panel-title">${t('ov.sym.title')}
      <span class="panel-aside">${t('ov.sym.aside')}</span></h3>
    <div class="hx-syms">
      ${rows.map(s => symptomRow(s, active)).join('')}
      <!-- Not counted with the rest: finding likely duplicates is an O(n^2)
           text comparison (db.dedup_candidates), which is a scan somebody
           asks for and not something a landing page runs on every paint. -->
      <div class="hx-sym" data-sym="dupes">
        <span class="hx-sev sev-info"></span>
        <span class="hx-sym-name">${t('ov.sym.dupes')}</span>
        <span class="hx-sym-n" data-dupes>—</span>
        <span class="hx-sym-share" data-dupes-share>${t('ov.sym.notScanned')}</span>
        <button type="button" class="btn btn-sm" data-scan>${t('ov.sym.scan')}</button>
      </div>
      <!-- Two checks over the FILE rather than counts over the memories:
           whether SQLite can still read it, and whether the keyword index
           still matches the rows it indexes. Asked for, like the scan
           above, because together they cost around 400ms on a store of a
           few tens of megabytes and this page paints on every landing. -->
      <div class="hx-sym" data-sym="file">
        <span class="hx-sev" data-file-sev></span>
        <span class="hx-sym-name">${t('ov.sym.file')}</span>
        <span class="hx-sym-n" data-file>—</span>
        <span class="hx-sym-share" data-file-share>${t('ov.sym.file.notChecked')}</span>
        <button type="button" class="btn btn-sm" data-check>${t('ov.sym.file.act')}</button>
      </div>
    </div>
  </div>`;
}

/* ─── the calendar ────────────────────────────────────────────────────────
   The API sends one row per day that has anything on it. A calendar has to
   show the days that have nothing, so the sparse rows are filled into a
   continuous run ending today, and then cut into Monday-first weeks. */

const WEEKS = 5;
const key = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${
  String(d.getDate()).padStart(2, '0')}`;

function calendar(activity) {
  const byDay = Object.fromEntries(activity.map(a => [a.day, a.count]));
  /* back to the Monday of the week WEEKS-1 weeks ago, so the grid is whole
     weeks and today sits in the last row wherever the weekday falls */
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const monday = new Date(today);
  monday.setDate(monday.getDate() - ((today.getDay() + 6) % 7) - (WEEKS - 1) * 7);
  const out = [];
  for (let d = new Date(monday); d <= today; d.setDate(d.getDate() + 1)) {
    const k = key(d);
    out.push({ key: k, count: byDay[k] || 0, today: k === key(today) });
  }
  return out;
}

/* Whole rows of seven, the last one padded with the days that have not
   happened yet -- an empty cell and a future cell are not the same fact. */
function intoWeeks(days) {
  const weeks = [];
  for (let i = 0; i < days.length; i += 7) {
    const row = days.slice(i, i + 7);
    while (row.length < 7) row.push(null);
    weeks.push(row);
  }
  return weeks;
}

/* Five steps, and the first of them is "nothing". Quartiles of the peak
   rather than of the mean: one busy day should not flatten the rest. */
function level(n, max) {
  if (!n) return 0;
  return Math.min(4, Math.ceil((n / max) * 4));
}

/* '3-9 Aug' inside one month, '31 Aug-6 Sep' across two. The leading zero
   goes: the column is 96px of monospace and 'Aug' has to fit on the same
   line as the range, or the label runs out over the first cell. */
function weekLabel(row) {
  const days = row.filter(Boolean);
  const [d1, m1] = fmtDay(days[0].key).replace(/^0/, '').split(' ');
  const [d2, m2] = fmtDay(days[days.length - 1].key).replace(/^0/, '').split(' ');
  return m1 === m2 ? `${d1}–${d2} ${m2}` : `${d1} ${m1}–${d2} ${m2}`;
}

function heatmap(weeks, max, { total, active }) {
  const head = I18N.weekdays.map(w =>
    `<span class="hx-cal-dow">${esc(w)}</span>`).join('');
  const body = weeks.map(row => `<span class="hx-cal-week">${weekLabel(row)}</span>${
    row.map(d => d
      ? `<span class="hx-cell l${level(d.count, max)}${d.today ? ' today' : ''}"
               data-day="${d.key}" data-n="${d.count}"></span>`
      : '<span class="hx-cell future"></span>').join('')}`).join('');
  return `<div>
    <div class="hx-grid" role="img"
         aria-label="${esc(t('ov.spark.aria', { n: fmtInt(total), peak: fmtInt(max),
           days: weeks.flat().filter(Boolean).length, active }))}">
      <span></span>${head}${body}
    </div>
    <div class="hx-scale">
      <span>0</span>
      ${[0, 1, 2, 3, 4].map(l => `<span class="hx-cell l${l}"></span>`).join('')}
      <span>${fmtInt(max)}</span>
    </div>
  </div>`;
}

function weekdayBars(days) {
  const sum = new Array(7).fill(0);
  days.forEach(d => {
    /* Monday-first, like the grid: getDay() is Sunday-first */
    const [y, m, day] = d.key.split('-').map(Number);
    sum[(new Date(y, m - 1, day).getDay() + 6) % 7] += d.count;
  });
  const max = Math.max(1, ...sum);
  return `<div class="hx-dow">
    <div class="mg-label">${t('ov.activity.byWeekday')}</div>
    ${I18N.weekdays.map((w, i) => `
      <div class="hx-dow-row">
        <span>${esc(w)}</span>
        <div class="bar-track"><div class="bar-fill"
             style="--v:${(sum[i] / max).toFixed(4)};background:var(--accent)"></div></div>
        <span class="hx-dow-n">${fmtInt(sum[i])}</span>
      </div>`).join('')}
  </div>`;
}

/* ─── confidence, by type ─────────────────────────────────────────────── */

function byTypePanel(o) {
  const present = [...TYPE_ORDER.filter(x => x in o.by_type),
                   ...Object.keys(o.by_type).filter(x => !TYPE_ORDER.includes(x))];
  const rows = present.map(tp => {
    const split = o.by_type_confidence[tp] || {};
    const segs = CONF_ORDER.map(c => split[c]
      ? `<div style="flex:${split[c]};background:${confColor(c)}"
              title="${esc(CONF[c].label)}: ${fmtInt(split[c])}"></div>` : '').join('');
    return `<button type="button" class="hx-type" data-type="${esc(tp)}"
              title="${esc(t('ov.byType.open', { type: tp }))}">
      ${typeTag(tp)}
      <span class="hx-type-bar">${segs}</span>
      <span class="hx-type-n">${fmtInt(o.by_type[tp])}</span>
    </button>`;
  }).join('') || `<div class="empty">${t('ov.types.empty')}</div>`;

  return `<div class="panel">
    <h3 class="panel-title">${t('ov.byType.title')}
      <span class="panel-aside">${t('ov.aside.active')}</span></h3>
    <div class="hx-types">${rows}</div>
  </div>`;
}

/* ─── wiring ──────────────────────────────────────────────────────────── */

function wire(view, o) {
  const bySymptom = Object.fromEntries(o.symptoms.map(s => [s.key, s]));

  view.querySelectorAll('.hx-cell[data-day]').forEach(cell => {
    cell.addEventListener('mousemove', e => tipShow(
      t('ov.tip.onDay', { n: cell.dataset.n, day: cell.dataset.day }), e.clientX, e.clientY));
    cell.addEventListener('mouseleave', tipHide);
  });

  view.querySelectorAll('[data-conf]').forEach(el => el.addEventListener('click', () =>
    go('memories', { confidence: el.dataset.conf, status: 'active' })));
  view.querySelectorAll('[data-type]').forEach(el => el.addEventListener('click', () =>
    go('memories', { type: el.dataset.type, status: 'active' })));

  view.querySelectorAll('.hx-sym-go').forEach(btn => btn.addEventListener('click', () => {
    const s = bySymptom[btn.closest('.hx-sym').dataset.sym];
    if (!s) return;
    /* a route with its own params overrides the server's filter -- see
       SYMPTOM_ROUTE, where only the one with no list of its own has them */
    const dest = SYMPTOM_ROUTE[s.key] || 'memories';
    const [name, own] = Array.isArray(dest) ? dest : [dest, null];
    go(name, own || s.params || {});
  }));

  /* The scan the count is worth waiting for. It replaces its own row rather
     than the panel: everything else on screen is already true. */
  const scan = view.querySelector('[data-scan]');
  scan?.addEventListener('click', async () => {
    scan.disabled = true;
    scan.textContent = t('ov.sym.scanning');
    try {
      const r = await api('/api/maintenance/dedup?limit=60');
      view.querySelector('[data-dupes]').textContent = fmtInt(r.pairs.length);
      view.querySelector('[data-dupes-share]').textContent =
        t('ov.sym.atOverlap', { p: Math.round(r.threshold * 100) });
      scan.textContent = t('ov.sym.dupes.act');
      scan.disabled = !r.pairs.length;
      scan.onclick = () => go('maintenance');
    } catch {
      scan.disabled = false;
      scan.textContent = t('ov.sym.scan');
    }
  });

  /* The file's own two checks. The index can pass its integrity check and
     still not hold the rows it indexes, so the count comparison is part of
     the same verdict rather than a third line -- what a reader wants to
     know is whether the index can be trusted, not which half failed. The
     detail that says which half is on the row. */
  const check = view.querySelector('[data-check]');
  check?.addEventListener('click', async () => {
    check.disabled = true;
    check.textContent = t('ov.sym.file.checking');
    try {
      const h = await api('/api/maintenance/health');
      const indexOk = h.fts.ok && h.fts.rows === h.fts.expected;
      const passed = Number(h.integrity.ok) + Number(indexOk);
      const detail = [
        h.integrity.ok ? '' : (h.integrity.detail || t('ov.sym.file.dbBad')),
        indexOk ? '' : (h.fts.detail || t('ov.sym.file.rows',
          { a: fmtInt(h.fts.rows), b: fmtInt(h.fts.expected) })),
      ].filter(Boolean).join(' · ');

      const row = view.querySelector('[data-sym="file"]');
      row.querySelector('[data-file]').textContent = `${passed}/2`;
      row.querySelector('[data-file-share]').textContent =
        passed === 2 ? t('ov.sym.file.ok') : t('ov.sym.file.bad', { n: 2 - passed });
      /* Every other mark in this panel carries a severity even at a count of
         zero, so a clean verdict is coloured too. Grey is reserved for the
         state before the check has been run, which is neither. */
      row.querySelector('[data-file-sev]').className =
        `hx-sev ${passed === 2 ? 'sev-info' : h.integrity.ok ? 'sev-warn' : 'sev-bad'}`;
      /* the whole verdict, including SQLite's own message, where a 74px
         column cannot carry it */
      row.title = detail || t('ov.sym.file.okLong');

      check.disabled = false;
      /* The label stays put on a pass: pressing Check again is what it does,
         and a second wording for the same action ran to two lines in a 92px
         column and made this row taller than the eight above it. */
      check.textContent = t('ov.sym.file.act');
      if (passed < 2) {
        /* the repairs are operations on the file, and they live where the
           other operations on the file do */
        check.textContent = t('ov.sym.file.repair');
        check.onclick = () => go('maintenance', { tab: 'storage' });
      }
    } catch {
      check.disabled = false;
      check.textContent = t('ov.sym.file.act');
    }
  });
}
