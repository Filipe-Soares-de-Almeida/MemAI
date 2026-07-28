/* Vocabulary every view shares: the memory types, the confidence scale,
   the small render fragments built from them, and the domains cache.

   Type colors live in admin.css (--t-*) and are read from there, so the
   canvas engines, the legends and the CSS classes cannot drift apart.
   Display labels bake once per page load -- t() is resolved at import
   time, which is safe because a language switch reloads the page. */

import { $, esc, cssVar, fmtBytes, fmtInt } from './dom.js';
import { api } from './api.js';
import { icon } from './icons.js';
import { t } from '../i18n.js';
import { copyUid } from './ui.js';

export const TYPE_ORDER = ['note', 'checkpoint', 'anti_pattern', 'reasoning', 'handoff', 'diagram'];

export const TYPES = Object.fromEntries(
  TYPE_ORDER.map(tp => [tp, { color: cssVar(`--t-${tp}`) || '#9e9e9e' }]));

/* the raw enum stays lower_snake for CSS classes & payloads; the label is
   display-only */
export const TYPE_LABEL = Object.fromEntries(TYPE_ORDER.map(tp => [tp, t(`type.${tp}`)]));

/* `icon` names an entry in core/icons.js -- all three are ringed marks, so
   a confidence state never reads as the bare cross that dismisses things */
export const CONF = {
  unverified:   { icon: 'unverified', label: t('conf.unverified') },
  confirmed:    { icon: 'confirmed', label: t('conf.confirmed') },
  contradicted: { icon: 'contradicted', label: t('conf.contradicted') },
};

export const REL_SUGGEST = ['relates_to', 'supersedes', 'contradicts', 'duplicates', 'links_to'];

/* datalist options: the canonical value stays in the payload, the
   translated label is display-only */
export const relOptions = () =>
  REL_SUGGEST.map(r => `<option value="${r}">${t(`rel.${r}`)}</option>`).join('');

export const typeColor = tp => (TYPES[tp] || {}).color || '#9e9e9e';
export const typeClass = tp => TYPES[tp] ? `t-${tp}` : '';

export const typeTag = tp =>
  `<span class="type-tag ${typeClass(tp)}"><span class="dot"></span>${esc(tp)}</span>`;

export const confPill = c => {
  const meta = CONF[c];
  return `<span class="conf-pill c-${esc(c)}">${meta ? icon(meta.icon) : ''}${esc(meta ? meta.label : c)}</span>`;
};

export const uidChip = uid =>
  `<span class="uid-chip" data-copy="${esc(uid)}" title="${t('uid.copyTitle')}">${esc(uid)}</span>`;

export const statusTag = s =>
  s === 'archived' ? `<span class="status-tag archived">${t('status.archived')}</span>` : '';

export function wireCopyChips(root) {
  root.querySelectorAll('[data-copy]').forEach(el =>
    el.addEventListener('click', e => { e.stopPropagation(); copyUid(el.dataset.copy); }));
}

/* ─── domains cache (datalists, selects) ─────────────────────────────── */

let cache = null, cachedAt = 0;

export async function getDomains(force = false) {
  if (!force && cache && Date.now() - cachedAt < 60000) return cache;
  const data = await api('/api/domains');
  cache = data.domains;
  cachedAt = Date.now();
  return cache;
}

/* Anything that creates, renames or re-homes a memory invalidates this. */
export const invalidateDomains = () => { cache = null; };

/* The last fetched list without a round-trip, for a datalist that is only
   a convenience -- an empty one is not worth blocking a modal on. */
export const cachedDomains = () => cache || [];

/* ─── rail ───────────────────────────────────────────────────────────── */

export function updateRail(o) {
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
