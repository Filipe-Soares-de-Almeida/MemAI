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

/* ─── memory picker ────────────────────────────────────────────────────
   The lookup field used wherever one memory has to point at another: the
   relations editor in a record, the link editor on a diagram step.

   The keyboard model here was already right and is unchanged: results are
   real buttons so a keyboard can reach and pick one, and the list is
   dismissed by focus leaving the whole picker rather than by a timeout
   racing the click it was waiting for.

   What was wrong was everything the reader sees. The row led with the uid
   at a fixed width and gave the snippet whatever was left, under nowrap +
   ellipsis -- so on a store whose titles share a long prefix ("TICKET-1042
   — ...") the ellipsis removed the only part that told two memories apart,
   and several rows rendered as the identical string. Meanwhile /api/lookup
   already returned `domain` and `status` and this dropped both, so an
   archived memory was indistinguishable from a live one. Picking then
   replaced the title you had just recognised with the uid you cannot read.

   The row leads with the snippet at full width over two lines now, and the
   things that separate near-identical memories -- domain, type, why it
   matched, whether it is archived -- ride underneath in one quiet line.
   The uid keeps its place there, as reference rather than as the headline.

   Returns resolve(): async, and the ONLY way to get a uid out of here. */
export function wireUidPicker({ input, results, exclude, label }) {
  let picked = null;
  let seq = 0;                     /* filters and typing race; last call wins */
  const filters = { type: '', domain: '', tag: '', status: 'active' };

  results.setAttribute('role', 'group');
  results.setAttribute('aria-label', label || t('lookup.aria'));
  input.setAttribute('aria-expanded', 'false');
  if (results.id) input.setAttribute('aria-controls', results.id);

  results.innerHTML = `
    <div class="picker-filters">
      <select data-f="type" aria-label="${t('lookup.filter.type')}">
        <option value="">${t('lookup.filter.type')}</option>
        ${TYPE_ORDER.map(tp => `<option value="${tp}">${esc(TYPE_LABEL[tp])}</option>`).join('')}
      </select>
      <select data-f="domain" aria-label="${t('lookup.filter.domain')}">
        <option value="">${t('lookup.filter.domain')}</option>
      </select>
      <input type="text" data-f="tag" placeholder="${t('lookup.filter.tag')}"
             aria-label="${t('lookup.filter.tag')}" autocomplete="off">
      <label class="inline-label">
        <input type="checkbox" data-f="archived">${t('lookup.filter.archived')}
      </label>
    </div>
    <div class="picker-list"></div>
    <div class="picker-foot" hidden></div>`;

  const list = results.querySelector('.picker-list');
  const foot = results.querySelector('.picker-foot');
  const domainSel = results.querySelector('[data-f="domain"]');

  /* the cache is usually warm from the view behind the drawer; when it is
     not, the select fills in a moment later rather than blocking the list */
  getDomains().then(ds => {
    domainSel.insertAdjacentHTML('beforeend',
      ds.map(d => `<option value="${esc(d.domain)}">${esc(d.domain)}</option>`).join(''));
  }).catch(() => { /* the filter is an accelerator, not a requirement */ });

  const anyFilter = () => Boolean(filters.type || filters.domain || filters.tag)
    || filters.status !== 'active';

  const close = () => {
    results.hidden = true;
    input.setAttribute('aria-expanded', 'false');
  };
  const open = () => {
    const wasClosed = results.hidden;
    results.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    /* Ten two-line rows is ~500px inside a drawer that scrolls, so a field
       near the bottom opens a list that is mostly below the fold with
       nothing saying so. Only on the way open, and only when it actually
       overflows -- scrolling on every keystroke would be its own problem. */
    if (wasClosed && results.getBoundingClientRect().bottom > window.innerHeight)
      results.scrollIntoView({ block: 'end' });
  };
  /* The field keeps the TITLE, because that is what was recognised; the uid
     rides in a data attribute, where the payload can reach it and the reader
     does not have to. */
  const choose = (uid, title) => {
    picked = uid;
    input.value = title || uid;
    input.dataset.uid = uid;
    input.classList.add('has-pick');
    close();
    input.focus();
  };
  const unpick = () => {
    picked = null;
    delete input.dataset.uid;
    input.classList.remove('has-pick');
  };

  const row = it => {
    const meta = [
      it.domain ? `<span class="picker-domain">${esc(it.domain)}</span>` : '',
      `<span>${esc(TYPE_LABEL[it.type] || it.type)}</span>`,
      it.match_source
        ? `<span class="match-badge" title="${esc(scoreTitle(it))}">${esc(it.match_source)}</span>` : '',
      `<span class="picker-uid">${esc(it.uid)}</span>`,
      it.status === 'archived' ? statusTag('archived') : '',
    ].filter(Boolean).join('<span class="picker-sep" aria-hidden="true">·</span>');
    return `
      <button type="button" class="picker-item" data-pick="${esc(it.uid)}"
              data-title="${esc(it.snippet)}" title="${esc(it.snippet)}">
        <span class="dot" style="--c:${typeColor(it.type)}"></span>
        <span class="picker-snippet">${esc(it.snippet)}</span>
        <span class="picker-meta">${meta}</span>
      </button>`;
  };

  async function run() {
    const mine = ++seq;
    const qs = new URLSearchParams({
      q: input.value.trim(), exclude: exclude || '',
      type: filters.type, domain: filters.domain, tag: filters.tag,
      status: filters.status,
    });
    let r;
    try {
      r = await api(`/api/lookup?${qs}`);
    } catch (err) {
      if (mine !== seq) return;
      /* This used to be an empty catch commented "best-effort", so a failed
         lookup was indistinguishable from a store with nothing in it. */
      list.innerHTML = `<div class="picker-note picker-note-bad">${t('lookup.failed')}</div>`;
      foot.hidden = true;
      open();
      return;
    }
    if (mine !== seq) return;       /* a newer query already answered */
    list.innerHTML = r.items.map(row).join('') || `
      <div class="picker-note">
        ${anyFilter() ? t('lookup.emptyFiltered') : t('lookup.empty')}
        ${anyFilter() ? `<button type="button" class="btn btn-sm" data-clearf>${t('lookup.filter.clear')}</button>` : ''}
      </div>`;
    foot.textContent = r.has_more ? t('lookup.more') : '';
    foot.hidden = !r.has_more;
    open();
    list.querySelectorAll('[data-pick]').forEach(b =>
      b.addEventListener('click', () => choose(b.dataset.pick, b.dataset.title)));
    const clear = list.querySelector('[data-clearf]');
    if (clear) clear.addEventListener('click', () => {
      Object.assign(filters, { type: '', domain: '', tag: '', status: 'active' });
      results.querySelectorAll('[data-f]').forEach(el => {
        if (el.type === 'checkbox') el.checked = false; else el.value = '';
      });
      run();
    });
  }

  const runSoon = debounce(run, 280);

  results.querySelectorAll('[data-f]').forEach(el => {
    const read = () => {
      if (el.dataset.f === 'archived') filters.status = el.checked ? '' : 'active';
      else filters[el.dataset.f] = el.value;
    };
    /* a select or a checkbox is a discrete decision and answers at once;
       only free text waits for the typist to stop */
    el.addEventListener(el.tagName === 'INPUT' && el.type === 'text' ? 'input' : 'change',
      () => { read(); (el.type === 'text' ? runSoon : run)(); });
  });

  input.addEventListener('input', () => { unpick(); runSoon(); });
  input.addEventListener('focus', run);
  /* Down walks into the list and Escape gives up on it. stopPropagation on
     Escape because the drawer this usually sits in closes on the same key,
     and dismissing a suggestion list is not asking to leave the record. */
  input.addEventListener('keydown', e => {
    if (results.hidden) return;
    if (e.key === 'ArrowDown') {
      const first = list.querySelector('[data-pick]');
      if (first) { e.preventDefault(); first.focus(); }
    } else if (e.key === 'Escape') {
      e.stopPropagation();
      close();
    }
  });
  results.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      close();
      input.focus();
      return;
    }
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    /* inside a filter control the arrows belong to the control: they walk a
       select's options, and stealing them would break the native widget */
    if (e.target.closest('.picker-filters')) return;
    const items = [...list.querySelectorAll('[data-pick]')];
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

  /* The uid to submit, or null and the reason why not.

     Both call sites used to read `picked() || input.value.trim()`, which
     sent whatever had been typed and let the API fail on it -- so a
     mistyped uid came back as a red toast in the corner, one round trip
     away from the field it was about. Pasting a uid still works, because
     the placeholder promises it; it is now VERIFIED here, and a paste that
     resolves adopts the memory's title exactly as a pick would. */
  return async function resolve() {
    if (picked) return { uid: picked };
    const raw = input.value.trim();
    if (!raw) return { uid: null, reason: 'empty' };
    /* The one uid the lookup will never return is the record you are on, so
       pasting it would otherwise be reported as "no memory has that uid" --
       true of the search, and a lie about the store. */
    if (exclude && raw === exclude) return { uid: null, reason: 'self' };
    try {
      const r = await api(`/api/lookup?q=${seg(raw)}&exclude=${seg(exclude || '')}&status=`);
      const hit = r.items.find(it => it.uid === raw);
      if (hit) { choose(hit.uid, hit.snippet); return { uid: hit.uid }; }
    } catch {
      return { uid: null, reason: 'failed' };
    }
    return { uid: null, reason: 'unknown' };
  };
}

/* the two scores behind a match badge. Reference for someone judging a
   borderline candidate, which is why it is a title and not on the row. */
const scoreTitle = it => [
  it.fts_rank !== undefined && it.fts_rank !== null ? `bm25 ${Number(it.fts_rank).toFixed(2)}` : '',
  it.vec_distance !== undefined && it.vec_distance !== null ? `cos ${Number(it.vec_distance).toFixed(3)}` : '',
].filter(Boolean).join(' ');

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
