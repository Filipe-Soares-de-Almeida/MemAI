/* Vocabulary every view shares: the memory types, the confidence scale,
   the small render fragments built from them, and the domains cache.

   Type colors live in admin.css (--t-*) and are read from there, so the
   canvas engines, the legends and the CSS classes cannot drift apart.
   Display labels bake once per page load -- t() is resolved at import
   time, which is safe because a language switch reloads the page. */

import { $, esc, cssVar, fmtBytes, fmtInt } from './dom.js';
import { api } from './api.js';
import { icon } from './icons.js';
import { fixedItems, pickerFor, wirePicker } from './pick.js';
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
   A named list of the five, and the escape hatch keeps the string open.

   Emits the picker AND its custom-value sibling; wireRelTypeField() joins
   them and returns the value getter. */
const relItems = options => [
  { value: '', label: t('dr.rel.type.placeholder'),
    html: `<span class="pick-any">${t('dr.rel.type.placeholder')}</span>` },
  ...options.map(r => ({ value: r, label: t(`rel.${r}`) })),
  { value: REL_OTHER, label: t('dr.rel.type.other'),
    html: `<span class="pick-any">${t('dr.rel.type.other')}</span>` },
];

export const relTypeField = ({ selId, customId, options, value = '', ariaLabel }) => {
  const known = !value || options.includes(value);
  return pickerFor({
    id: selId, value: known ? value : REL_OTHER, items: relItems(options),
    ariaLabel, cls: 'rel-type-sel',
  }) + `
    <input type="text" id="${customId}" class="rel-type-custom"${known ? ' hidden' : ''}
           placeholder="${t('dr.rel.type.customPlaceholder')}"
           aria-label="${t('dr.rel.type.customPlaceholder')}"
           value="${known ? '' : esc(value)}" autocomplete="off">`;
};

/* `onPick` is the caller's own business on top of revealing the free-text
   sibling -- the link picker clears its validation error there. */
export function wireRelTypeField(root, { selId, customId, options, onPick }) {
  const btn = root.querySelector(`#${selId}`);
  const custom = root.querySelector(`#${customId}`);
  wirePicker(root, {
    id: selId, items: fixedItems(relItems(options)),
    onPick: value => {
      const other = value === REL_OTHER;
      custom.hidden = !other;
      if (other) custom.focus();
      onPick?.(value);
    },
  });
  return () => (btn.dataset.v === REL_OTHER ? custom.value.trim() : btn.dataset.v);
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

/* A field's name in the reader's language. The label the server sends is the
   one WRITTEN IN THE BODY -- the parser's anchor, what FTS indexes, what the
   tool composes -- so it stays English wherever it is stored, and only its
   name on screen is translated. A field with no entry yet falls back to that
   label rather than to a bare key, and the stored spelling rides along in the
   title so the pane and the raw body stay recognisably the same field. */
export const sectionLabel = (type, section) => {
  const key = `sec.${type}.${section.key}`;
  const named = t(key);
  return named === key ? section.label : named;
};

export const sectionLabelHTML = (type, section) =>
  `<span class="sec-label-text" title="${esc(section.label)}">`
  + `${esc(sectionLabel(type, section))}</span>`;

/* ─── the two closed vocabularies, as picker rows ────────────────────────
   A type and a confidence are identified everywhere else in this UI by a
   mark -- a coloured dot, a ringed glyph -- and a native <option> could
   carry neither, so the one list where you PICK one was the one place the
   mark went missing. core/pick.js draws markup, so it comes along.

   `any` is the row that stands for no filter at all. It is a sentence and not
   a value, so it reads as one (.pick-any) instead of impersonating a type. */

export const typeItems = ({ any = '' } = {}) => [
  ...(any ? [{ value: '', label: any, html: `<span class="pick-any">${esc(any)}</span>` }] : []),
  /* the type-tag chrome, with the display label rather than the raw enum:
     the enum is what the payload carries, not what a reader is choosing */
  ...TYPE_ORDER.map(tp => ({
    value: tp,
    label: TYPE_LABEL[tp],
    html: `<span class="type-tag ${typeClass(tp)}"><span class="dot"></span>${esc(TYPE_LABEL[tp])}</span>`,
  })),
];

export const confItems = ({ any = '' } = {}) => [
  ...(any ? [{ value: '', label: any, html: `<span class="pick-any">${esc(any)}</span>` }] : []),
  ...Object.keys(CONF).map(c => ({ value: c, label: CONF[c].label, html: confPill(c) })),
];

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

/* Whether a path IS a scope or sits under it. Segment-wise, like the
   server's own check: 'acme/x1000' is not inside 'acme/x100' however
   similar the two read. An empty scope holds everything. */
export function inDomainPath(domain, scope) {
  const want = domainSegments(scope), segs = domainSegments(domain);
  return want.every((s, i) => segs[i] === s);
}

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

/* The SHAPE of a domain tree, one entry per row: which ancestor columns
   still have a row below them (so their line continues through this one),
   and whether this row closes its branch.

   `list` must already be in tree order (byDomainPath) -- that is what makes
   this one pass: the next row at this depth or shallower is either a sibling
   of this row or the end of its parent.

   Kept here rather than in the one view that draws it, because the shape of
   the tree and the way a given surface draws it are two different things: a
   second renderer must not work the shape out for itself, or the same tree
   ends up drawn as two different trees. */
export function domainGuides(list) {
  const out = [];
  /* cont[k - 1]: the row last seen at depth k has a sibling still to come,
     so the column that branch owns keeps its line through the rows between */
  const cont = [];
  list.forEach((d, i) => {
    const depth = domainDepth(d.domain);
    let last = true;
    for (let j = i + 1; j < list.length; j++) {
      const next = domainDepth(list[j].domain);
      if (next > depth) continue;
      last = next < depth;
      break;
    }
    /* one flag per pass-through column; the connector column is this row's
       own `last`, and a root has neither */
    out.push({ depth, through: cont.slice(1, depth - 1), last });
    cont[depth - 1] = !last;
  });
  return out;
}

/* One row's guide rails as markup, from one entry of domainGuides.

   The columns are drawn by admin.css (.dom-rail), which measures them off
   --dom-step -- so whatever hosts these has to declare that pair and lay its
   row out the same way: the indent spacer, then an 18px twist slot, then the
   name. `leaf` says there is no twist in that slot, and the closing stroke
   runs on across it to the name instead of stopping at an empty box.

   Markup and not just classes, because the domain table and the domain
   picker draw the same tree, and two copies of this loop is how they would
   come to draw it differently. */
export const domainRailHTML = ({ depth, through, last }, { leaf = false } = {}) =>
  depth > 1
    ? `<span class="dom-rail${leaf ? ' dg-leaf' : ''}" aria-hidden="true">${
        through.map(on => `<i class="${on ? 'dg-line' : 'dg-gap'}"></i>`).join('')
      }<i class="dg-elbow${last ? ' dg-end' : ''}"></i></span>`
    : '';

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
