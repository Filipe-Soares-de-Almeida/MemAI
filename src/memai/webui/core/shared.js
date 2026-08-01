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

/* Relation vocabularies. Two, because they name different things: a
   relation between memories is not the same statement as the tie from a
   diagram step to the memory that explains it. Both stay OPEN -- the API
   accepts any string, and relations predating either list have to remain
   editable -- which is what the "other…" escape below is for. */
export const REL_SUGGEST = ['relates_to', 'supersedes', 'contradicts', 'duplicates', 'links_to'];
export const DG_REL_SUGGEST = ['explains', 'contradicts', 'relates_to'];

const REL_OTHER = '__other';

/* A relation type was a text input behind a <datalist>, and admin.css hides
   the native datalist indicator -- so the field looked like free text and
   gave no sign a known set existed. Recall where recognition was available.
   A select names the five, and the escape hatch keeps the string open.

   Emits the select AND its custom-value sibling; wireRelTypeField() joins
   them and returns the value getter. */
export const relTypeField = ({ selId, customId, options, value = '', ariaLabel }) => {
  const known = !value || options.includes(value);
  return `
    <select id="${selId}" class="rel-type-sel" aria-label="${esc(ariaLabel)}">
      <option value="">${t('dr.rel.type.placeholder')}</option>
      ${options.map(r => `<option value="${r}"${value === r ? ' selected' : ''}>${esc(t(`rel.${r}`))}</option>`).join('')}
      <option value="${REL_OTHER}"${known ? '' : ' selected'}>${t('dr.rel.type.other')}</option>
    </select>
    <input type="text" id="${customId}" class="rel-type-custom"${known ? ' hidden' : ''}
           placeholder="${t('dr.rel.type.customPlaceholder')}"
           aria-label="${t('dr.rel.type.customPlaceholder')}"
           value="${known ? '' : esc(value)}" autocomplete="off">`;
};

export function wireRelTypeField(sel, custom) {
  sel.addEventListener('change', () => {
    const other = sel.value === REL_OTHER;
    custom.hidden = !other;
    if (other) custom.focus();
  });
  return () => (sel.value === REL_OTHER ? custom.value.trim() : sel.value);
}

export const typeColor = tp => (TYPES[tp] || {}).color || '#9e9e9e';
export const typeClass = tp => TYPES[tp] ? `t-${tp}` : '';

export const typeTag = tp =>
  `<span class="type-tag ${typeClass(tp)}"><span class="dot"></span>${esc(tp)}</span>`;

/* `compact` drops the word and keeps the mark, for a dense list row where the
   same three states repeat fifty times: the ring shape and the colour carry
   it, and the word is one hover and one drawer away. The title is not
   decoration here -- it is the only place the label survives. */
export const confPill = (c, compact = false) => {
  const meta = CONF[c];
  const label = esc(meta ? meta.label : c);
  return `<span class="conf-pill c-${esc(c)}${compact ? ' compact' : ''}"${compact ? ` title="${label}"` : ''}>`
    + `${meta ? icon(meta.icon) : ''}${compact ? '' : label}</span>`;
};

/* A button, not a span with a click handler: pressing it copies, so it has
   to be reachable by keyboard and it has to say what it does. The visible
   text is the uid, which names the target but not the action, hence the
   aria-label as well. */
export const uidChip = uid =>
  `<button type="button" class="uid-chip" data-copy="${esc(uid)}"
           title="${t('uid.copyTitle')}"
           aria-label="${esc(t('a11y.copyUid', { uid }))}">${esc(uid)}</button>`;

/* ─── a load that failed ──────────────────────────────────────────────────
   Six places rendered a fatal load error with `.empty` -- the same grey centred
   sentence as "no results" -- so a dropped connection on Maintenance looked
   exactly like a clean database, and the app had no retry control anywhere at
   all. This is deliberately not `.empty`: left-aligned, marked, and carrying
   the one thing that helps. */

export const failedHTML = err => `<div class="failed" role="alert">
  <span class="failed-mark">${icon('contradicted')}</span>
  <div class="failed-text">
    <div>${t('err.load')}</div>
    ${err?.message ? `<div class="failed-detail">${esc(err.message)}</div>` : ''}
  </div>
  <button type="button" class="btn btn-sm" data-retry>${t('common.retry')}</button>
</div>`;

/* Wraps a loader so a failure reports itself into `hostSel` with a Retry wired
   back to this same wrapper: retrying re-runs exactly what failed, and a second
   failure renders the same way instead of falling silent. The returned function
   is also what a Refresh button should call, so the two paths cannot drift.
   Resolves to undefined on failure rather than rejecting -- the failure has
   already been reported on screen. */
export function retryable(hostSel, loader) {
  const run = () => loader().catch(err => {
    const host = document.querySelector(hostSel);
    if (!host) return;            /* the view was swapped while this was in flight */
    host.innerHTML = failedHTML(err);
    host.querySelector('[data-retry]').addEventListener('click', run);
  });
  return run;
}

export const statusTag = s =>
  s === 'archived' ? `<span class="status-tag archived">${t('status.archived')}</span>` : '';

export function wireCopyChips(root) {
  root.querySelectorAll('[data-copy]').forEach(el =>
    el.addEventListener('click', e => { e.stopPropagation(); copyUid(el.dataset.copy); }));
}

/* ─── domain paths ───────────────────────────────────────────────────────
   A domain is one string that reads as a path: 'acme/x100/p200' is a
   routine inside a module inside a product. The server stores and matches
   it; these are the four things a view needs to DRAW one. */

export const DOMAIN_SEP = '/';

export const domainSegments = d => (d || '').split(DOMAIN_SEP).filter(Boolean);
export const domainLeaf = d => domainSegments(d).slice(-1)[0] || '';
export const domainDepth = d => domainSegments(d).length;

/* Tree order: a parent, then its whole subtree, then its next sibling.
   Comparing the strings would not do it -- '-' sorts before '/', so a root
   named 'acme-legacy' would land between 'acme' and 'acme/x100' and cut a
   subtree in half. Segments, then depth. */
export function byDomainPath(a, b) {
  const x = domainSegments(a.domain), y = domainSegments(b.domain);
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const c = x[i].localeCompare(y[i]);
    if (c) return c;
  }
  return x.length - y.length;
}

/* Options for a domain <select>: tree-ordered, indented by depth, showing
   the leaf with the full path on hover. Repeating the whole path in every
   row makes the reader diff strings to see the shape; indentation shows it.
   The full path is still the value, and still the title -- two sibling
   trees can hold the same leaf name. */
export function domainOptions(domains, selected = '') {
  return domains.slice().sort(byDomainPath).map(d => {
    /* non-breaking: a run of plain spaces inside an <option> collapses */
    const pad = '   '.repeat(Math.max(domainDepth(d.domain) - 1, 0));
    return `<option value="${esc(d.domain)}"${d.domain === selected ? ' selected' : ''}
             title="${esc(d.domain)}">${pad}${esc(domainLeaf(d.domain))}</option>`;
  }).join('');
}

/* For a free-text domain field: the whole path is the value, because that
   is what gets typed and stored. Tree-ordered so the suggestions read as
   the tree they are. */
export const domainDatalist = domains =>
  domains.slice().sort(byDomainPath)
    .map(d => `<option value="${esc(d.domain)}">`).join('');

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
    <div class="rh-meter"><div class="rh-fill" style="--v:${cov / 100}"></div></div>
    <div class="rh-row"><span>${t('rail.active')}</span><b>${fmtInt(o.totals.active)}</b></div>`;
  $('#dbBadge').textContent = t('badge.active', { n: fmtInt(o.totals.active) });
  $('#dbBadge').title = o.db.path;
}
