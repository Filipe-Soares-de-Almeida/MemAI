/* Vocabulary every view shares: memory types, the confidence scale, fragments built from them,
   and the domains cache. Labels resolve t() at import; a language switch reloads the page. */

import { esc, cssVar } from './dom.ts';
import { icon } from './icons.js';
import { fixedItems, pickerFor, wirePicker } from './pick.js';
import { t } from '../i18n.ts';
import { TYPE_ORDER } from './vocab.js';
import { MEMORY } from '../contract.ts';
import * as client from '../api/client.ts';

export { TYPE_ORDER, REL_SUGGEST, DG_REL_SUGGEST } from './vocab.js';

const TYPES = Object.fromEntries(
  TYPE_ORDER.map(tp => [tp, { color: cssVar(`--t-${tp}`) || '#9e9e9e' }]));

/* the raw enum stays lower_snake for CSS classes & payloads; the label is
   display-only */
export const TYPE_LABEL = Object.fromEntries(TYPE_ORDER.map(tp => [tp, t(`type.${tp}`)]));

/* Each confidence state has a ringed mark of its own name in core/icons.js, so it never reads as
   the bare cross that dismisses things. */
export const CONF = Object.fromEntries(MEMORY.CONFIDENCES.map(c =>
  [c, { icon: c, label: t(`conf.${c}`) }]));

/* A pin is a pushpin: filled for every domain, an outline stuck into a base line for one domain,
   so the two differ in shape and not only in weight. */
const PIN_ICON = { global: 'pin', domain: 'pin-domain' };
export const PIN = Object.fromEntries(MEMORY.PINS.map(p =>
  [p, { icon: PIN_ICON[p], label: t(`mem.pin.${p}`) }]));

const pinMark = pin => PIN[pin]
  ? `<span class="pin-mark pin-${pin}" role="img" title="${esc(PIN[pin].label)}"
       aria-label="${esc(PIN[pin].label)}">${icon(PIN[pin].icon)}</span>`
  : '';

/* the relation picker's entry for a type outside the suggestions: it opens a text field */
export const REL_OTHER = '__other';

/* A relation type's display label; the stored string moves to the title (relTypeTitle). The set
   is open, so an unknown type falls back to itself rather than to the key. */
export function relLabel(type) {
  const raw = String(type || '').trim();
  if (!raw) return '';
  const key = `rel.${raw}`;
  const label = t(key);
  return label === key ? raw : label;
}

/* Empty when the label IS the stored string -- a tooltip repeating what is
   already on screen is noise. */
export const relTypeTitle = type => {
  const raw = String(type || '').trim();
  return relLabel(raw) === raw ? '' : t('rel.raw', { type: raw });
};

/* A curation kind's display label, arranged as relLabel: the stored spelling moves to the title,
   and an unknown kind falls back to itself. */
export function kindLabel(kind) {
  const raw = String(kind || '').trim();
  if (!raw) return '';
  const key = `kind.${raw}`;
  const label = t(key);
  return label === key ? raw : label;
}

/* Empty when the label IS the stored string, like relTypeTitle. */
export const kindTitle = kind => {
  const raw = String(kind || '').trim();
  return kindLabel(raw) === raw ? '' : t('kind.raw', { kind: raw });
};

/* How a peer memory is named: its title, else its body (also the tooltip). `named` says which,
   so a fallback stays as quiet as the body it shows (.mem-named). */
export const peerName = peer => {
  const title = String(peer?.title || '').trim();
  const body = String(peer?.snippet || '').trim();
  return { text: title || body, hover: title ? body : '', named: !!title };
};

/* A named picker of the known relation types, with an escape hatch for any string. Emits the
   picker and its custom-value sibling; wireRelTypeField() joins them and returns the getter. */
export const relItems = options => [
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

/* What a suggestion kind does, as one of the six field roles (--f-*, admin.css); kinds that act
   alike share a hue. Never the --t-* type ramp, where orange means "note". */
const KIND_ROLE = {
  link: 'aim', crosslist: 'aim',
  reword: 'hold', compact: 'hold', distill: 'hold', unleak: 'hold',
  set_confidence: 'ask', redomain: 'ask',
  retitle: 'go', retag: 'go',
  review: 'next',
  archive: 'stop', merge: 'stop',
};

export const kindColor = kind => `var(--f-${KIND_ROLE[kind] || 'aim'})`;

export const typeColor = tp => (TYPES[tp] || {}).color || '#9e9e9e';
export const typeClass = tp => TYPES[tp] ? `t-${tp}` : '';

/* `compact` keeps only the mark for dense rows; the title is then the only place the label
   survives. */
export const confPill = (c, compact = false) => {
  const meta = CONF[c];
  const label = esc(meta ? meta.label : c);
  return `<span class="conf-pill c-${esc(c)}${compact ? ' compact' : ''}"${compact ? ` title="${label}"` : ''}>`
    + `${meta ? icon(meta.icon) : ''}${compact ? '' : label}</span>`;
};

/* A button, since pressing it copies: keyboard-reachable, and aria-labelled because the visible
   uid names the target, not the action. */
export const uidChip = uid =>
  `<button type="button" class="uid-chip" data-copy="${esc(uid)}"
           title="${t('uid.copyTitle')}"
           aria-label="${esc(t('a11y.copyUid', { uid }))}">${esc(uid)}</button>`;

/* A field's translated name. The server's label is the one written in the body, so it stays
   English in storage and rides along in the title; a field with no entry falls back to it. */
export const sectionLabel = (type, section) => {
  const key = `sec.${type}.${section.key}`;
  const named = t(key);
  return named === key ? section.label : named;
};

/* What a field does, by section key: one flat map, since SECTION_SPEC keys do not collide across
   types. A type with no sections falls back to its own colour. */
const SECTION_ROLE = {
  pattern: 'stop', why_wrong: 'hold', instead: 'go',
  intent: 'aim', established: 'go', pursuing: 'hold', open_questions: 'ask',
  hypothesis: 'hold', reasoning: 'aim', result: 'go',
  revised_belief: 'ask', next_time: 'next',
};

/* The custom properties a block's mark and label draw from: --h the fill, --h-ink the letter;
   one colour cannot do both on this ground. */
export const sectionHue = (type, key) => {
  const role = SECTION_ROLE[key];
  return role
    ? `--h: var(--f-${role}); --h-ink: var(--f-${role}-ink)`
    : `--h: var(--t-${type}); --h-ink: var(--t-${type}-ink)`;
};

/* ─── the two closed vocabularies as picker rows, with their marks (core/pick.js draws markup).
   `any` is the no-filter row, styled as a sentence (.pick-any). */

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

export const pinItems = ({ any = '' } = {}) => [
  { value: '', label: any, html: `<span class="pick-any">${esc(any)}</span>` },
  { value: 'any', label: t('mem.pin.any'),
    html: `<span class="pin-pick"><span class="pin-mark pin-any">${icon('pin')}</span>${
      esc(t('mem.pin.any'))}</span>` },
  ...Object.keys(PIN).map(p => ({
    value: p, label: PIN[p].label,
    html: `<span class="pin-pick">${pinMark(p)}${esc(PIN[p].label)}</span>`,
  })),
];

/* ─── a load that failed: deliberately not `.empty`, so a dropped connection never looks like an
   empty store; left-aligned, marked, with a Retry. */

export const failedHTML = err => `<div class="failed" role="alert">
  <span class="failed-mark">${icon('contradicted')}</span>
  <div class="failed-text">
    <div>${t('err.load')}</div>
    ${err?.message ? `<div class="failed-detail">${esc(err.message)}</div>` : ''}
  </div>
  <button type="button" class="btn btn-sm" data-retry>${t('common.retry')}</button>
</div>`;

export const statusTag = s =>
  s === 'archived' ? `<span class="status-tag archived">${t('status.archived')}</span>` : '';

/* ─── domain paths (core/domains.ts), and the markup drawn from them */

export { DOMAIN_SEP, domainSegments, domainLeaf, inDomainPath, byDomainPath,
         domainGuides } from './domains.ts';

/* One row's guide rails as markup. Hosts declare --dom-step and lay rows out as spacer, an 18px
   twist slot, then the name; `leaf` runs the closing stroke across an empty slot. */
export const domainRailHTML = ({ depth, through, last }, { leaf = false } = {}) =>
  depth > 1
    ? `<span class="dom-rail${leaf ? ' dg-leaf' : ''}" aria-hidden="true">${
        through.map(on => `<i class="${on ? 'dg-line' : 'dg-gap'}"></i>`).join('')
      }<i class="dg-elbow${last ? ' dg-end' : ''}"></i></span>`
    : '';

/* ─── domains cache (datalists, selects) ─────────────────────────────── */

let cache = null, cachedAt = 0;

export async function getDomains(force = false) {
  if (!force && cache && Date.now() - cachedAt < 60000) return cache;
  const data = await client.domains.tree();
  cache = data.domains;
  cachedAt = Date.now();
  return cache;
}

/* Anything that creates, renames or re-homes a memory invalidates this. */
export const invalidateDomains = () => { cache = null; };

/* The last fetched list without a round-trip, for a datalist that is only
   a convenience -- an empty one is not worth blocking a modal on. */
export const cachedDomains = () => cache || [];
