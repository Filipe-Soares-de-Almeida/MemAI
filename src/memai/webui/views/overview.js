/* Overview: the numbers that say what the store looks like right now. */

import { esc, fmtInt, fmtBytes, fmtAgo } from '../core/dom.js';
import { api } from '../core/api.js';
import { tipShow, tipHide } from '../core/ui.js';
import { typeClass, CONF, TYPE_ORDER, updateRail } from '../core/shared.js';
import { go } from '../core/router.js';
import { openRecord } from './record.js';
import { t } from '../i18n.js';

const CONF_ORDER = ['confirmed', 'unverified', 'contradicted'];
const confColor = c =>
  c === 'confirmed' ? 'var(--ok)' : c === 'contradicted' ? 'var(--bad)' : 'var(--warn)';

export async function renderOverview(view, params, ctx) {
  const o = await api('/api/overview');
  if (ctx.stale()) return;
  updateRail(o);

  const tot = o.totals;
  const confSeg = CONF_ORDER.map(c => {
    const n = o.by_confidence[c] || 0;
    return n ? `<div class="meter-seg" style="flex:${n};background:${confColor(c)}" title="${esc(CONF[c].label)}: ${fmtInt(n)}"></div>` : '';
  }).join('');
  const confLegend = CONF_ORDER.map(c =>
    `<span class="legend-item"><i style="color:${confColor(c)}">${CONF[c].i}</i>${esc(CONF[c].label)} <b>${fmtInt(o.by_confidence[c] || 0)}</b></span>`).join('');

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
