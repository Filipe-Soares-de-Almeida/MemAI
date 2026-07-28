/* Vocabulary every view shares: the memory types, the confidence scale,
   the small render fragments built from them, and the domains cache.

   Type colors live in admin.css (--t-*) and are read from there, so the
   canvas engines, the legends and the CSS classes cannot drift apart.
   Display labels bake once per page load -- t() is resolved at import
   time, which is safe because a language switch reloads the page. */

import { $, esc, cssVar, fmtBytes, fmtInt, debounce } from './dom.js';
import { api, seg } from './api.js';
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

/* ─── uid picker ───────────────────────────────────────────────────────
   The lookup field used wherever one memory has to point at another: the
   relations editor in a record, the link editor on a diagram step. Each view
   had its own copy, and both copies had the same two faults.

   The results were divs chosen by `mousedown`, so a keyboard could neither
   reach one nor pick one -- which made creating a relation a mouse-only
   operation. And the list was dismissed by a timeout 180ms after the input
   blurred, a race against the very click it was waiting for; it is dismissed
   by focus leaving the whole picker now, which is the actual condition.

   Returns picked(): the uid chosen from the list, or null once the field has
   been typed in again. */
export function wireUidPicker({ input, results, exclude, label }) {
  let picked = null;
  results.setAttribute('role', 'group');
  results.setAttribute('aria-label', label || t('lookup.aria'));
  input.setAttribute('aria-expanded', 'false');
  if (results.id) input.setAttribute('aria-controls', results.id);

  const close = () => {
    results.hidden = true;
    input.setAttribute('aria-expanded', 'false');
  };
  const choose = uid => {
    picked = uid;
    input.value = uid;
    close();
    input.focus();
  };

  const lookup = debounce(async () => {
    try {
      const r = await api(`/api/lookup?q=${seg(input.value.trim())}&exclude=${seg(exclude)}`);
      results.innerHTML = r.items.map(it => `
        <button type="button" class="picker-item" data-pick="${esc(it.uid)}">
          <span class="dot" style="--c:${typeColor(it.type)}"></span>
          <span class="uid-chip" style="cursor:inherit">${esc(it.uid)}</span>
          <span class="snippet">${esc(it.snippet)}</span>
        </button>`).join('') || `<div class="picker-item">${t('lookup.empty')}</div>`;
      results.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      results.querySelectorAll('[data-pick]').forEach(b =>
        b.addEventListener('click', () => choose(b.dataset.pick)));
    } catch { /* lookup is best-effort */ }
  }, 280);

  input.addEventListener('input', () => { picked = null; lookup(); });
  input.addEventListener('focus', lookup);
  /* Down walks into the list and Escape gives up on it. stopPropagation on
     Escape because the drawer this usually sits in closes on the same key,
     and dismissing a suggestion list is not asking to leave the record. */
  input.addEventListener('keydown', e => {
    if (results.hidden) return;
    if (e.key === 'ArrowDown') {
      const first = results.querySelector('[data-pick]');
      if (first) { e.preventDefault(); first.focus(); }
    } else if (e.key === 'Escape') {
      e.stopPropagation();
      close();
    }
  });
  results.addEventListener('keydown', e => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'Escape') return;
    if (e.key === 'Escape') {
      e.stopPropagation();
      close();
      input.focus();
      return;
    }
    const items = [...results.querySelectorAll('[data-pick]')];
    const next = items[items.indexOf(document.activeElement) + (e.key === 'ArrowDown' ? 1 : -1)];
    e.preventDefault();
    (next || input).focus();
  });
  /* focusout on the picker as a whole, not blur on the input: focus moving
     from the field INTO the list is not focus leaving the picker */
  const host = input.closest('.picker') || input.parentElement;
  host.addEventListener('focusout', e => {
    if (!host.contains(e.relatedTarget)) close();
  });

  return () => picked;
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
    <div class="rh-meter"><div class="rh-fill" style="--v:${cov / 100}"></div></div>
    <div class="rh-row"><span>${t('rail.active')}</span><b>${fmtInt(o.totals.active)}</b></div>`;
  $('#dbBadge').textContent = t('badge.active', { n: fmtInt(o.totals.active) });
  $('#dbBadge').title = o.db.path;
}
