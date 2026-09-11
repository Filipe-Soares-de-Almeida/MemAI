/* Optimization runs: batches of suggested edits waiting for a human to
   accept or reject them.

   Three levels, each its own address. Level 0 is the grid of runs, level 1
   is one run -- what the batch was checked against, what it would do to the
   store, and a row per kind -- and level 2 is one kind, as a list you pick
   from beside the evidence for whatever is picked.

   The head of level 1 reports how much of the batch the agent VERIFIED
   rather than what applying it would score. The index cannot carry that
   claim: it is round(mean of four axes), each round(met * 100 / active), so
   one suggestion moves it by a fraction of a point and a whole run by one at
   best. `verified` is what differs from one suggestion to the next. */

import { $, esc, fmtInt, fmtDate, dayKey, monthKey, fromKey } from '../core/dom.js';
import { api, seg } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, failed, confirmModal, copyCode } from '../core/ui.js';
import { typeTag, uidChip, statusTag, confPill, wireCopyChips, failedHTML,
         relLabel, relTypeTitle, peerName, kindColor,
         kindLabel, kindTitle, CONF } from '../core/shared.js';
import { renderRich, wireRich } from '../core/richtext.js';
import { markPair } from '../core/textdiff.js';
import { go, previousRoute, replaceParams } from '../core/router.js';
import { openRecord } from './record.js';
import { I18N, t } from '../i18n.js';

/* ─── one suggestion, rendered by kind ───────────────────────────────── */

/* the kinds the before/after pair below knows how to render. Anything else
   is shown as its payload rather than as two empty boxes -- see optRaw. */
const DIFF_KINDS = new Set(['compact', 'reword', 'retag', 'retitle', 'redomain',
                            'crosslist', 'set_confidence', 'review', 'archive',
                            'unleak']);

/* A shape of change, and one pane apiece. A kind is not a diff because
   it replaces something: a body is PROSE and is read, a tag list is a SET
   and is compared item by item, and a confidence is a FLAG the rest of this
   UI already draws as a ringed pill. Rendering all three as two walls of
   text put one word in a pane four hundred pixels tall and asked the reader
   to spot which word it was. */
const SET_KINDS = new Set(['retag', 'crosslist']);
const FLAG_KINDS = new Set(['set_confidence', 'archive']);
const LINE_KINDS = new Set(['retitle', 'redomain', 'review']);

/* A fifth shape: TEXT losing a piece of itself. `unleak` removes a leaked
   tool call from one field, and the payload says WHICH -- a body, a tag
   list, a source reference. One renderer, because it is one kind of change;
   the field picks the wells or the two lines, since a source reference in a
   pane six hundred pixels tall is one line of text in an empty room. */
const TEXT_KINDS = new Set(['unleak']);
const leakField = s => (s.payload || {}).field || 'content';
const isBodyLeak = s => TEXT_KINDS.has(s.kind) && leakField(s) === 'content';

/* The kinds whose "before" IS the memory's own content. For these, the
   memory-under-review preview and the Before pane print the identical
   string, one above the other, on the same card -- so the preview is
   dropped. Every other diff kind puts a tag, a domain, a confidence or a
   status in Before, and then the preview is the only thing on the card that
   says WHICH memory is being retagged. */
const CONTENT_KINDS = new Set(['compact', 'reword']);

/* A body is drawn by the renderer the record draws it with, not escaped into
   one block: what is stored is a memory's own markup -- bold, lists, fenced
   code, [[uid]] references -- and a reviewer deciding whether to replace it
   has to read it the way it will be read. Escaping put `**` and `[[…]]` on
   the screen as characters, and collapsed every paragraph break with them.
   The 120-character peer and source previews stay plain: they identify a
   memory rather than being read, and a body cut mid-word renders as
   whatever markup the cut happened to leave open. */
const rich = (text, s) => renderRich(text ?? '', s.body_links);

/* The body a rewrite starts from and the body it proposes. The whole of
   each, not the preview: After is complete, and a truncated Before beside
   it reads as text the suggestion is removing. */
const proseBefore = s => rich(s.content_before ?? (s.target || {}).snippet, s);
const proseAfter = s => rich((s.payload || {}).new_content, s);

/* A peer's well: the memory NAMED, and then read.

   The dense previews elsewhere -- a row of the relation rail, a diagram's
   link -- show the title alone and put the body on the tooltip, because a
   row is one line. This well is a third of the pane, and a title in it left
   the rest empty; so the name leads and the body follows under it, which is
   also what the pane is for. A memory with no title has only the body, and
   that is what the well shows. */
function peerWellHTML(m) {
  const p = peerName(m);
  if (!p.named) return `<div class="snippet">${esc(p.text)}</div>`;
  return `<div class="snippet">
    <span class="opt-peer-name">${esc(p.text)}</span>
    ${p.hover ? `<span class="opt-peer-body-text">${esc(p.hover)}</span>` : ''}
  </div>`;
}

function optRelBody(s) {
  const p = s.payload || {}, peers = s.peers || {};
  const pair = s.kind === 'link'
    ? [[t('op.role.from'), peers.from_uid], [t('op.role.to'), peers.to_uid]]
    : [[t('op.role.keep'), peers.keep_uid], [t('op.role.drop'), peers.drop_uid]];
  /* merge does not carry a type in its payload: the edge it writes is always
     a supersedes (db._apply_kind), so that is what the arrow names. */
  const relType = s.kind === 'link' ? (p.relation_type || 'relates_to') : 'supersedes';
  const areas = ['l1', 'l2'], bodies = ['b1', 'b2'];
  return `<div class="opt-rel">
    ${pair.map(([role, m], i) => `
      <span class="opt-label" style="grid-area:${areas[i]}">${esc(role)}</span>
      <div class="opt-peer-body" style="grid-area:${bodies[i]}">
        ${m ? `<div class="opt-peer-meta">${typeTag(m.type)} ${uidChip(m.uid)} ${statusTag(m.status)}</div>
          ${peerWellHTML(m)}` : `<div class="snippet">${t('op.missing')}</div>`}
      </div>`).join('')}
    <div class="opt-arrow" title="${esc(relTypeTitle(relType) || t('op.relType.title'))}"
      >${esc(relLabel(relType))}${icon('arrow-right')}</div>
  </div>`;
}

function optDistillBody(s) {
  const p = s.payload || {};
  const srcs = (s.sources || []).map(m => `
    <div class="opt-peer-body">
      ${m && !m.missing ? `<div class="opt-peer-meta">${typeTag(m.type)} ${uidChip(m.uid)} ${statusTag(m.status)}</div>
        ${peerWellHTML(m)}` : `<div class="snippet">${t('op.missing')}</div>`}
    </div>`).join('');
  return `<div class="opt-distill">
    <span class="opt-label">${t('op.distill.sources', { n: (s.sources || []).length })}</span>
    ${srcs}
    <div class="opt-arrow" title="${esc(relTypeTitle('supersedes') || t('op.relType.title'))}"
      >${esc(relLabel('supersedes'))}${icon('arrow-right')}</div>
    <span class="opt-label">${t('op.distill.new')}${p.new_type ? ` · ${esc(p.new_type)}` : ''}${p.domain ? ` · ${esc(p.domain)}` : ''}</span>
    ${p.title ? `<div class="snippet"><strong>${esc(p.title)}</strong></div>` : ''}
    <div class="snippet rt">${rich(p.new_content, s)}</div>
  </div>`;
}

/* A kind with no renderer here still has to be readable. This used to fall
   through to an empty before/after pair with a live Apply button under it,
   which asked for a decision about nothing. */
function optRaw(s) {
  return `<div class="opt-unknown">
    <span class="opt-label">${t('op.unknownKind')}</span>
    <pre class="snippet">${esc(JSON.stringify(s.payload ?? {}, null, 2))}</pre>
  </div>`;
}

/* ── a FLAG changing state ──
   set_confidence and archive move a memory between a handful of named
   states. Both states are drawn with the mark the rest of the UI uses for
   them, side by side on one line: the change is the pair, and there is no
   text to read. */

const statusPill = st => st === 'archived'
  ? `<span class="status-tag archived">${t('status.archived')}</span>`
  : `<span class="status-tag active">${t('status.active')}</span>`;

function flagPairHTML(s) {
  const tg = s.target || {}, p = s.payload || {};
  const pair = s.kind === 'set_confidence'
    ? [confPill(tg.confidence || 'unverified'), confPill(p.confidence)]
    : [statusPill(tg.status || 'active'), statusPill('archived')];
  return `<div class="opt-flag">
    <span class="opt-label">${t('op.before')}</span>
    <span class="opt-flag-was">${pair[0]}</span>
    <span class="opt-flag-arrow">${icon('arrow-right')}</span>
    <span class="opt-label">${t('op.after')}</span>
    <span class="opt-flag-now">${pair[1]}</span>
    ${s.kind === 'archive' && p.reason
      ? `<span class="opt-flag-why">${esc(p.reason)}</span>` : ''}
  </div>`;
}

/* ── a SET gaining and losing members ──
   Tags and cross-listings are collections, so what changed is which items
   left and which arrived -- not where the characters differ in a joined
   string. Both sides are drawn in full so what STAYED is visible too. */

const SET_SPLIT = /\s*,\s*/;
const setOf = v => (Array.isArray(v) ? v : String(v ?? '').split(SET_SPLIT))
  .map(x => String(x).trim()).filter(Boolean);

function setPairHTML(s) {
  const tg = s.target || {}, p = s.payload || {};
  const [was, now] = s.kind === 'retag'
    ? [setOf(tg.tags), setOf(p.tags)]
    : [setOf(tg.also), setOf(p.also)];
  const gone = was.filter(x => !now.includes(x));
  const born = now.filter(x => !was.includes(x));
  const chips = (items, other, cls) => items.length
    ? items.map(x => `<span class="opt-schip${other.includes(x) ? '' : ' ' + cls}">${esc(x)}</span>`).join('')
    : `<span class="opt-schip is-none">${t('op.set.none')}</span>`;
  return `<div class="opt-set">
    <span class="opt-label">${t('op.before')} · ${t('op.set.n', { n: was.length })}</span>
    <span class="opt-label">${t('op.after')} · ${t('op.set.n', { n: now.length })}</span>
    <div class="opt-chips">${chips(was, now, 'is-gone')}</div>
    <div class="opt-chips">${chips(now, was, 'is-new')}</div>
    <div class="opt-set-sum">${[
      gone.length ? `<span class="opt-set-out">${t('op.set.dropped', { n: gone.length })}</span>` : '',
      born.length ? `<span class="opt-set-in">${t('op.set.added', { n: born.length })}</span>` : '',
      gone.length || born.length ? '' : `<span>${t('op.set.same')}</span>`,
    ].join('')}</div>
  </div>`;
}

/* ── a VALUE being replaced ──
   A title, a domain path, a review date: one short line each, so the pair
   is two lines and not two panes. The words that differ are still marked --
   an appended path segment is easy to miss -- so these carry the diff
   classes the pane wires up. */
function linePairHTML(s) {
  const tg = s.target || {}, p = s.payload || {};
  const mono = s.kind === 'redomain' ? ' is-path' : '';
  const [was, now] = s.kind === 'retitle' ? [tg.title, p.title]
    : s.kind === 'redomain' ? [tg.domain, p.domain]
    : [tg.review_after, p.review_after];   /* review */
  return `<div class="opt-line">
    <span class="opt-label">${t('op.before')}</span>
    <span class="opt-label">${t('op.after')}</span>
    <div class="opt-lval opt-diff-b${mono}">${esc(was || '—')}</div>
    <div class="opt-lval opt-diff-a${mono}">${esc(now || '—')}</div>
  </div>`;
}

/* ── PROSE being rewritten ──
   The only kind whose change is a body someone has to read, and the only
   one that earns two full panes with a scroller each. */
function prosePairHTML(s) {
  return `<div class="opt-diff">
    <span class="opt-label opt-diff-bl">${t('op.before')}${
      s.chars_before ? ` · ${t('op.chars', { n: fmtInt(s.chars_before) })}` : ''}</span>
    <span class="opt-label opt-diff-al">${t('op.after')}${
      s.chars_after ? ` · ${t('op.chars', { n: fmtInt(s.chars_after) })}` : ''}</span>
    <div class="snippet opt-diff-b rt">${proseBefore(s)}</div>
    <div class="opt-arrow">${icon('arrow-right')}</div>
    <div class="snippet opt-diff-a rt">${proseAfter(s)}</div>
  </div>`;
}

/* ── TEXT being cleaned ──
   The pair a reviewer of an `unleak` reads: the field as it stands, and the
   same field with the call's own source gone. The label names the field --
   two walls of text do not say whether they are a body or a tag list -- and
   the diff classes are the ones the pane already marks, so what LEAVES is
   marked in place. */
function textPairHTML(s) {
  const p = s.payload || {}, field = leakField(s);
  const was = s.text_before ?? '', now = p.new_text ?? '';
  const label = (side, n) => `${t(side)} · ${t('op.field.' + field)}${
    n ? ` · ${t('op.chars', { n: fmtInt(n) })}` : ''}`;
  if (field !== 'content') {
    return `<div class="opt-line">
      <span class="opt-label">${esc(label('op.before'))}</span>
      <span class="opt-label">${esc(label('op.after'))}</span>
      <div class="opt-lval opt-diff-b">${esc(was || '—')}</div>
      <div class="opt-lval opt-diff-a">${esc(now || '—')}</div>
    </div>`;
  }
  return `<div class="opt-diff">
    <span class="opt-label opt-diff-bl">${esc(label('op.before', s.chars_before))}</span>
    <span class="opt-label opt-diff-al">${esc(label('op.after', s.chars_after))}</span>
    <div class="snippet opt-diff-b rt">${rich(was, s)}</div>
    <div class="opt-arrow">${icon('arrow-right')}</div>
    <div class="snippet opt-diff-a rt">${rich(now, s)}</div>
  </div>`;
}

/* "applied 8 · 2 failed" used to be painted 'bad' -- total-failure red for a
   mostly-successful batch -- and res.failed, which carries the id AND the reason
   for every one that did not go through, was thrown away. So the screen said
   something went wrong and gave you no way to find out what. A partial result is
   a warning, and the ids go where they can be read. */
function reportApplied(res) {
  const bad = res.failed || [];
  if (!bad.length) { toast(t('op.toast.appliedN', { n: res.applied }), 'ok'); return; }
  toast(t('op.toast.appliedN', { n: res.applied }) + t('op.toast.failedN', { m: bad.length }),
        'warn', { detail: bad.map(f => `#${f.id}: ${f.error}`).join(' · ') });
}

/* ─── shared furniture ───────────────────────────────────────────────── */

/* The way out of a level, as the same control the record's bar carries: a
   .btn with the chevron in front of its label, not a link wearing a border
   of its own. */
const backButton = (id, label) => `<button type="button" class="btn btn-sm opt-back" id="${id}"
  >${icon('chevron-left')}<span class="opt-back-text">${esc(label)}</span></button>`;

/* Up one level, and back to the SAME page when that is where you came from.

   router.backTo() cannot do this: it decides by view name, and all three
   levels are 'optimization', so from a group it fired history.back() at
   whatever the previous entry happened to be -- another group, or the page
   you had just reloaded on, which moved nothing at all. Matching the hash
   is what "where you came from" actually means here. */
function upTo(params) {
  const qs = new URLSearchParams(params || {}).toString();
  const target = `#/optimization${qs ? '?' + qs : ''}`;
  if (previousRoute().hash === target) history.back();
  else location.hash = target;
}

/* How much of a set the agent checked against the store as it stands. Three
   states, because "none of them" and "none left to decide" are not the same
   answer: amber while something unchecked is still pending, green when the
   pending ones are all checked, and quiet once nothing is pending. */
function verifiedState(verified, pending) {
  if (!pending) return 'done';
  return verified === pending ? 'all' : 'some';
}

const VERIFIED_FILL = { all: 'var(--ok)', some: 'var(--warn)', done: 'var(--ink-3)' };

/* The sentence a group's row says about itself. The server counts; the
   catalog words it.

   Three kinds have a second sentence for a batch that is not of one mind:
   moving to ONE domain can name it, moving to four cannot, and the same
   goes for the confidence being set and the relation being created. The
   server sends the field empty when the batch disagrees, which is what
   picks the variant here. */
const WHAT_MIXED = { redomain: 'to', set_confidence: 'conf', link: 'rel' };

/* Two of those facts are vocabularies this UI already translates, and the
   server sends the stored spelling. Naming a confidence `contradicted` and
   a relation `relates_to` inside an otherwise translated sentence is the
   same leak the kind masks close. A domain path stays as it is: it is data,
   not vocabulary. */
const WHAT_MASK = {
  conf: c => midSentence((CONF[c] || {}).label || c),
  rel: v => midSentence(relLabel(v)),
};

/* Both vocabularies are written to stand alone -- "Contradicted", "Relates
   to" -- and these facts land in the middle of a sentence, where a capital
   reads as a proper noun. A value that is already lower stays as it is. */
const midSentence = v => (v ? v.charAt(0).toLocaleLowerCase() + v.slice(1) : v);

function groupWhat(g) {
  const facts = g.facts || {};
  const field = WHAT_MIXED[g.kind];
  const key = `op.what.${g.kind}${field && !facts[field] ? 'Mixed' : ''}`;
  const shown = Object.fromEntries(Object.entries(facts).map(
    ([k, v]) => [k, WHAT_MASK[k] && v ? WHAT_MASK[k](v) : v]));
  const line = t(key, { n: g.pending || g.total, ...shown });
  return line === key ? t('op.what.other', { n: g.pending || g.total, kind: kindLabel(g.kind) }) : line;
}

/* ─── entry point ────────────────────────────────────────────────────── */

/* Four addresses, one view.

     #/optimization                      the month, and the day it opens on
     #/optimization?review=YYYY-MM-DD    that day's pending, one at a time
     #/optimization?run=N                one run
     #/optimization?run=N&kind=K         one kind of one run, one at a time

   The month carries `?month=` and `?day=` so a reload and a shared link land
   on the same screen. */
export async function renderOptimization(view, params, ctx) {
  const runId = Number(params.get('run') || 0);
  const review = params.get('review') || '';

  if (!runId) {
    const runs = (await api('/api/optimization/runs')).runs;
    if (ctx.stale()) return;
    /* the day scope is resolved against the run index rather than sent to
       the server: created_at is UTC and a calendar day is the reader's own
       (see byDay), so which runs a day holds is decided here */
    if (review) {
      const mine = runs.filter(r => dayKey(new Date(r.created_at)) === review);
      renderOptGroup(view, dayScope(review, mine));
      return;
    }
    renderOptCalendar(view, runs, params);
    return;
  }
  const sum = await api(`/api/optimization/summary?run=${seg(runId)}`);
  if (ctx.stale()) return;
  const kind = params.get('kind') || '';
  if (kind) renderOptGroup(view, kindScope(sum, kind));
  else renderOptRun(view, sum);
}

/* ─── level 0 — the month, and the day it opens ───────────────────────────
   The axis is the month. The grid says which days a maintenance pass ran on
   and which of those still hold a decision; the rail opens the selected day
   at the level of the suggestion.

   A day is a LOCAL day. `created_at` is UTC (db.now_iso), so grouping is
   done here rather than by asking the server for a date: a run staged at
   23:30 belongs to the day the reader was living, and a `date(created_at)`
   filter would file it under the next one. The rail asks for suggestions by
   RUN ID for the same reason (see admin.optimization_suggestions).

   Deliberately NOT kept: the searchable grid of every run. The month is the
   whole index now. */

/* Which weekday a row starts on, 0 = Sunday. One constant, so the header and
   the columns under it cannot disagree.

   I18N.weekdays is authored Monday-first (it labels the activity heatmap,
   which chunks the last 30 days in sevens and aligns to no week at all), so
   the names are ROTATED to whatever this says rather than read in order. */
const WEEK_START = 0;

const weekdayNames = () => {
  const names = I18N.weekdays || [];
  /* the array starts on Monday, which is index 1 of a Sunday-based week */
  return names.map((_, i) => names[(i + WEEK_START + 6) % names.length]);
};

const longDate = (date, opts) => {
  try { return date.toLocaleDateString(I18N.numberLocale, opts); }
  catch { return dayKey(date); }
};

/* Every run of the store, indexed by the local day it was staged on. */
function byDay(runs) {
  const days = new Map();
  for (const r of runs) {
    const when = new Date(r.created_at);
    if (isNaN(when)) continue;
    const key = dayKey(when);
    let slot = days.get(key);
    if (!slot) days.set(key, slot = { key, runs: [], total: 0, pending: 0, applied: 0, rejected: 0 });
    slot.runs.push(r);
    slot.total += r.total;
    slot.pending += r.pending;
    slot.applied += r.applied;
    slot.rejected += r.rejected;
  }
  for (const slot of days.values()) slot.runs.sort((a, b) => a.id - b.id);
  return days;
}

/* ── the grid ── */

/* One month as 7-column cells, starting on WEEK_START and padded at both
   ends so the grid is always whole weeks. */
function monthCells(month, days, today, selected) {
  const at = fromKey(`${month}-01`);
  const lead = (at.getDay() - WEEK_START + 7) % 7;
  const dim = new Date(at.getFullYear(), at.getMonth() + 1, 0).getDate();
  const cells = [];
  for (let i = 0; i < lead; i++) cells.push(null);
  for (let d = 1; d <= dim; d++) cells.push(d);
  while (cells.length % 7) cells.push(null);

  return cells.map(d => {
    /* The lead and the tail are cells too, and inert: an erased corner
       punches a hole in the block the month reads as. */
    if (d === null) return '<div class="opt-cell opt-cell-out" aria-hidden="true"></div>';
    const key = `${month}-${String(d).padStart(2, '0')}`;
    const slot = days.get(key);
    const has = !!slot;
    const open = slot ? slot.pending : 0;
    /* One tick per RUN, coloured by whether THAT run still holds a decision:
       a day with three runs of which one is open reads as two settled and
       one waiting, which a single day-level colour cannot say. */
    const bars = (slot ? slot.runs : []).slice(0, 3).map(r =>
      `<span class="opt-tick${r.pending ? ' is-open' : ''}"></span>`).join('');
    const more = slot && slot.runs.length > 3;
    const cls = ['opt-cell'];
    if (has) cls.push('has-runs');
    if (key === selected) cls.push('is-sel');
    if (open) cls.push('has-open');
    if (key === today) cls.push('is-today');
    return `<button type="button" class="${cls.join(' ')}" data-day="${key}"
        aria-pressed="${key === selected}"
        aria-label="${esc(longDate(fromKey(key), { weekday: 'long', day: 'numeric', month: 'long' })
          + ' · ' + (has ? t('op.cal.aria.has', { n: slot.runs.length, s: slot.total, o: open })
                         : t('op.cal.aria.none')))}">
      <span class="opt-cell-top">
        <span class="opt-cell-day">${d}</span>
        ${open ? `<span class="opt-cell-open">${t('op.cal.openN', { n: open })}</span>` : ''}
      </span>
      ${bars ? `<span class="opt-ticks">${bars}</span>` : ''}
      <span class="opt-cell-n">${has
        ? (more ? t('op.cal.runsAnd', { n: slot.runs.length, s: slot.total })
                : t('op.cal.sugShort', { n: slot.total }))
        : ''}</span>
    </button>`;
  }).join('');
}

/* What the whole month came to, under the grid. */
function monthFoot(month, days) {
  let runs = 0, sug = 0, open = 0, worked = 0;
  const dim = new Date(fromKey(`${month}-01`).getFullYear(),
                       fromKey(`${month}-01`).getMonth() + 1, 0).getDate();
  for (const [key, slot] of days) {
    if (!key.startsWith(month)) continue;
    worked += 1;
    runs += slot.runs.length;
    sug += slot.total;
    open += slot.pending;
  }
  if (!runs) {
    return `<span>${t('op.cal.foot.noRuns')}</span>
    <span class="opt-foot-gap"></span>
    <span>${t('op.cal.foot.noRunsHint')}</span>`;
  }
  return `<span>${t('op.cal.foot.runs', { n: fmtInt(runs) })}</span>
    <span>${t('op.cal.foot.sug', { n: fmtInt(sug) })}</span>
    <span class="${open ? 'opt-foot-open' : 'opt-foot-done'}">${
      open ? t('op.cal.foot.open', { n: fmtInt(open) }) : t('op.cal.foot.allDone')}</span>
    <span class="opt-foot-gap"></span>
    <span>${t('op.cal.foot.idle', { n: fmtInt(dim - worked) })}</span>`;
}

/* ── the rail ── */

function railHeadHTML(key, slot, today) {
  const date = fromKey(key);
  return `<div class="opt-day-head">
    <div class="opt-day-title">
      <span class="opt-day-when">${esc(longDate(date, { weekday: 'long', day: 'numeric', month: 'long' }))}</span>
      ${key === today ? `<span class="opt-day-today">${t('op.cal.today')}</span>` : ''}
      <span class="opt-foot-gap"></span>
      ${slot && slot.pending ? `<button type="button" class="btn btn-sm opt-day-review" data-seeday="${esc(key)}"
        title="${esc(t('op.cal.reviewDayTitle'))}">${t('op.cal.reviewDay')}${icon('chevron-right')}</button>` : ''}
    </div>
    <div class="opt-day-sub">${slot
      ? t('op.cal.daySub', { n: slot.runs.length, s: slot.total })
      : t('op.cal.dayNone')}</div>
  </div>`;
}

/* One run of the day, as a card that opens it.

   The card carries the run's number and the one count that says what is
   left to do with it -- what is still open, or, once nothing is, what it
   applied. Everything else about the run is on the page the card leads to,
   which is where a decision is taken. */
function lotCardHTML(r) {
  const open = r.pending;
  /* The kinds a run holds, most of them first, so the card says what sort
     of curation is waiting inside without being opened. */
  const kinds = [...(r.kinds || [])].sort((a, b) => b.total - a.total);
  const chips = kinds.slice(0, 4).map(k => `<span class="opt-lot-kind"
      title="${esc(t('op.cal.lot.kindTitle', { kind: kindLabel(k.kind), n: k.total }))}">
      <span class="opt-lot-dot" style="background:${kindColor(k.kind)}"></span>
      <span>${esc(kindLabel(k.kind))}</span></span>`).join('');
  const more = kinds.length - 4;
  /* What the run has SETTLED against what it still holds, in the same meter
     the run page states its case with. A run part-applied reads as part
     applied; the count beside it names the half that still wants an
     answer. */
  const seg = (n, c, label) => n
    ? `<div class="meter-seg" style="flex:${n};background:var(${c})" title="${esc(label)}"></div>` : '';
  const counts = open
    ? t('op.cal.lot.openOf', { n: fmtInt(open), all: fmtInt(r.total) })
    : t('op.cal.lot.appliedOf', { n: fmtInt(r.applied), all: fmtInt(r.total) })
      + (r.rejected ? ` · ${t('op.cal.lot.rejectedN', { n: fmtInt(r.rejected) })}` : '');
  return `<button type="button" class="opt-lot ${open ? 'is-open' : 'is-settled'}"
      data-openrun="${r.id}" title="${esc(t('op.cal.openRun', { id: r.id }))}">
    <span class="opt-lot-top">
      <span class="opt-lot-id">#${r.id}</span>
      <span class="opt-lot-at">${esc(fmtTime(r.created_at))}</span>
      <span class="opt-lot-what">${open ? t('op.cal.lot.open') : t('op.cal.lot.settled')}</span>
    </span>
    ${r.note ? `<span class="opt-lot-note">${esc(r.note)}</span>` : ''}
    <span class="meter opt-lot-meter">
      ${seg(r.applied, '--ok', t('op.applied'))}
      ${seg(r.rejected, '--bad', t('op.rejected'))}
      ${seg(open, '--warn', t('op.cal.lot.open'))}
    </span>
    <span class="opt-lot-count">${counts}</span>
    <span class="opt-lot-kinds">${chips}${
      more > 0 ? `<span class="opt-lot-more">${t('op.cal.lot.more', { n: more })}</span>` : ''}</span>
  </button>`;
}

const fmtTime = iso => {
  const d = new Date(iso);
  return isNaN(d) ? '' : `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

/* ── the view ── */

function renderOptCalendar(view, runs, params) {
  const days = byDay(runs);
  const today = dayKey(new Date());
  /* Where to open when the address says nothing: the newest day that holds
     something to decide, else the newest day worked at all, else today. An
     empty month with an empty rail is the one arrival that tells a reader
     nothing. */
  const keys = [...days.keys()].sort();
  const firstOpen = keys.filter(k => days.get(k).pending).pop();
  const landing = firstOpen || keys[keys.length - 1] || today;
  let selected = params.get('day') || landing;
  if (!days.has(selected) && selected !== today && !/^\d{4}-\d{2}-\d{2}$/.test(selected)) selected = landing;
  let month = params.get('month') || selected.slice(0, 7);

  view.innerHTML = `<div class="opt-shell">
    <h2 class="sr-only">${t('op.title')}</h2>
    ${runs.length ? `<div class="opt-cal-work">
      <div class="opt-cal" id="optCal"></div>
      <div class="opt-day panel" id="optDay"></div>
    </div>` : `<div class="empty">${t('op.emptyRuns')}</div>`}
  </div>`;
  if (!runs.length) return;

  /* the address carries the month and the day, so a reload and a shared link
     land on the same screen; replaceState because walking a month back is
     not a step worth pressing Back through */
  const remember = () => replaceParams('optimization', { month, day: selected });

  function paintMonth() {
    const at = fromKey(`${month}-01`);
    const head = weekdayNames().map(w => `<span class="opt-dow">${esc(w)}</span>`).join('');
    $('#optCal').innerHTML = `
      <div class="opt-cal-bar">
        <h3 class="opt-cal-month">${esc(longDate(at, { month: 'long', year: 'numeric' }))}</h3>
        <span class="opt-cal-step">
          <button type="button" class="opt-step" id="optPrev"
                  title="${esc(t('op.cal.prev'))}" aria-label="${esc(t('op.cal.prev'))}">${icon('chevron-left')}</button>
          <button type="button" class="opt-step" id="optNext"
                  title="${esc(t('op.cal.next'))}" aria-label="${esc(t('op.cal.next'))}">${icon('chevron-right')}</button>
        </span>
        <button type="button" class="btn btn-sm" id="optToday">${t('op.cal.jumpToday')}</button>
        <span class="opt-foot-gap"></span>
        <span class="opt-legend"><span class="opt-tick"></span>${t('op.cal.legend.done')}</span>
        <span class="opt-legend"><span class="opt-tick is-open"></span>${t('op.cal.legend.open')}</span>
        <span class="opt-legend"><span class="opt-tick is-none"></span>${t('op.cal.legend.idle')}</span>
      </div>
      <div class="opt-grid-scroll">
        <div class="opt-grid" role="grid" aria-label="${esc(t('op.cal.gridAria'))}">
          ${head}${monthCells(month, days, today, selected)}
        </div>
      </div>
      <div class="opt-cal-foot">${monthFoot(month, days)}</div>`;

    $('#optPrev').addEventListener('click', () => step(-1));
    $('#optNext').addEventListener('click', () => step(1));
    $('#optToday').addEventListener('click', () => {
      month = today.slice(0, 7); selected = today;
      remember(); paintMonth(); paintDay();
    });
    view.querySelectorAll('[data-day]').forEach(b => b.addEventListener('click', () => {
      selected = b.dataset.day;
      remember(); paintMonth(); paintDay();
    }));
  }

  /* Stepping a month takes the rail with it, onto the same day-of-month, so
     `selected` always names a cell the grid is drawing. Short months clamp
     to their last day. */
  function step(by) {
    const at = fromKey(`${month}-01`);
    const next = new Date(at.getFullYear(), at.getMonth() + by, 1);
    month = monthKey(next);
    const wanted = Number(selected.slice(8));
    const dim = new Date(next.getFullYear(), next.getMonth() + 1, 0).getDate();
    selected = `${month}-${String(Math.min(wanted, dim)).padStart(2, '0')}`;
    remember();
    paintMonth();
    paintDay();
  }

  /* The rail is a LAUNCHER: the day it names, and the runs staged on it as
     cards. A decision is taken on the run's own page, where the evidence
     for it is; nothing here reads a suggestion. */
  function paintDay() {
    const host = $('#optDay');
    if (!host) return;
    const slot = days.get(selected);
    host.innerHTML = railHeadHTML(selected, slot, today)
      + (slot
        ? `<div class="opt-lots">${slot.runs.map(lotCardHTML).join('')}</div>`
        : `<div class="empty opt-day-empty">
             ${icon('db-file', { cls: 'opt-empty-mark' })}
             <p class="opt-empty-msg">${t('op.cal.emptyDay', {
               day: longDate(fromKey(selected), { day: 'numeric', month: 'long' }) })}</p>
             <p class="hint-sm">${t('op.cal.emptyHint')}</p>
           </div>`);

    host.querySelectorAll('[data-openrun]').forEach(b => b.addEventListener('click',
      () => go('optimization', { run: b.dataset.openrun })));
    host.querySelectorAll('[data-seeday]').forEach(b => b.addEventListener('click',
      () => go('optimization', { review: b.dataset.seeday })));
  }

  paintMonth();
  paintDay();
}

/* ─── level 1 — one run ──────────────────────────────────────────────── */

/* The head of the rail: how much of what is still pending was checked
   against the store as it stands. This is the one reading that separates
   "apply the group" from "open them one at a time", and it is already
   written on every suggestion -- nothing is computed to show it. */
function verifiedPanel(sum) {
  const { verified, pending } = sum;
  const unchecked = pending - verified;
  const bar = pending ? `<div class="meter opt-vf-meter">
      ${verified ? `<div class="meter-seg" style="flex:${verified};background:var(--ok)"
        title="${esc(t('op.vf.checked'))}: ${verified}"></div>` : ''}
      ${unchecked ? `<div class="meter-seg" style="flex:${unchecked};background:var(--warn)"
        title="${esc(t('op.vf.unchecked'))}: ${unchecked}"></div>` : ''}
    </div>` : '';
  return `<div class="panel opt-vf">
    <h3 class="panel-title">${t('op.vf.title')}
      <span class="panel-aside">${t('op.vf.pendingN', { n: fmtInt(pending) })}</span></h3>
    ${pending ? `
      <div class="opt-hero">
        <span class="opt-hero-n">${fmtInt(verified)}</span>
        <span class="opt-hero-of">${t('op.vf.ofN', { n: fmtInt(pending) })}</span>
      </div>
      ${bar}
      <div class="legend">
        <span class="legend-item"><span class="dot" style="--c:var(--ok)"></span>${t('op.vf.checked')} <b>${fmtInt(verified)}</b></span>
        <span class="legend-item"><span class="dot" style="--c:var(--warn)"></span>${t('op.vf.unchecked')} <b>${fmtInt(unchecked)}</b></span>
      </div>
      <p class="hint-sm">${unchecked ? t('op.vf.hintSome', { n: fmtInt(unchecked) }) : t('op.vf.hintAll')}</p>
    ` : `<p class="hint-sm">${t('op.vf.hintNone')}</p>`}
  </div>`;
}

/* The rail's second card: what the undecided half of the run would do,
   counted off the staged payloads. A row whose figure is zero is dropped --
   "relations created: 0" is a line about something the run does not do. */
function ledgerPanel(sum) {
  const L = sum.ledger;
  const rows = [
    [t('op.lg.memories'), t('op.lg.ofActive', { n: fmtInt(L.memories), all: fmtInt(L.active) }), ''],
    [t('op.lg.relations'), `+${fmtInt(L.relations)}`, 'pos', L.relations],
    [t('op.lg.confirmed'), `+${fmtInt(L.confirmed)}`, 'pos', L.confirmed],
    /* a rewrite normally removes text, so the row is named for the common
       direction and swaps its wording when a run happens to add */
    [L.chars > 0 ? t('op.lg.charsAdded') : t('op.lg.chars'),
     t(L.chars > 0 ? 'op.lg.charsUp' : 'op.lg.charsDown', { n: fmtInt(Math.abs(L.chars)) }),
     L.chars > 0 ? 'pos' : 'neg', L.chars],
    [t('op.lg.archived'), fmtInt(L.archived), '', L.archived],
    [t('op.lg.domains'), fmtInt(L.domains), '', L.domains],
  ].filter(r => r.length === 3 || r[3]);
  /* The filename is the thing to keep -- it is what you would restore from --
     but it is longer than the rail, so the line says the backup exists and
     the name is one hover away rather than three wrapped rows. */
  const name = (sum.run.backup_path || '').split(/[\\/]/).pop();
  const backup = sum.run.backup_path
    ? `<div class="opt-lg-backup" title="${esc(t('op.backupNote', { name }))}">
        ${icon('confirmed')}<span class="opt-lg-backup-name">${esc(name)}</span></div>` : '';
  return `<div class="panel opt-lg">
    <h3 class="panel-title">${t('op.lg.title')}</h3>
    <dl class="opt-ledger">
      ${rows.map(([k, v, cls]) =>
        `<dt>${esc(k)}</dt><dd${cls ? ` class="${cls}"` : ''}>${esc(v)}</dd>`).join('')}
    </dl>
    ${backup}
  </div>`;
}

function groupRow(g) {
  const state = verifiedState(g.verified, g.pending);
  const value = g.pending
    ? `<b>${fmtInt(g.verified)}</b> ${t('op.grp.ofN', { n: fmtInt(g.pending) })}`
    : `<span class="opt-grp-quiet">${g.applied ? t('op.applied') : t('op.rejected')}</span>`;
  /* Every button carries its verb alone. The count each one would repeat is
     two columns to the left, and reading it twice on one row made the two
     numbers look like two different measurements. What a bulk press is
     about to touch is named in the dialog it opens. */
  const acts = g.pending ? `
      <button type="button" class="btn btn-sm btn-ghost" data-rejectkind="${esc(g.kind)}"
              data-n="${g.pending}">${t('common.reject')}</button>
      <button type="button" class="btn btn-sm" data-applykind="${esc(g.kind)}"
              data-n="${g.pending}">${t('common.apply')}</button>`
    : g.applied ? `
      <button type="button" class="btn btn-sm btn-ghost" data-undokind="${esc(g.kind)}"
              data-n="${g.applied}">${t('common.undo')}</button>` : '';
  return `<div class="opt-grp${g.pending ? '' : ' decided'}">
    <span class="opt-grp-mark" style="background:${VERIFIED_FILL[state]}"></span>
    <button type="button" class="opt-grp-kind" data-open="${esc(g.kind)}"
            title="${esc(kindTitle(g.kind))}">${esc(kindLabel(g.kind))}</button>
    <span class="opt-grp-what">${esc(groupWhat(g))}</span>
    <span class="opt-grp-n${g.pending ? '' : ' nil'}">${g.pending || '—'}</span>
    <span class="opt-grp-val">${value}</span>
    <span class="opt-grp-acts">
      ${acts}
      <button type="button" class="btn btn-sm opt-grp-go" data-open="${esc(g.kind)}"
              title="${esc(t('op.grp.open', { kind: kindLabel(g.kind) }))}">${t('op.grp.detail')}${icon('chevron-right')}</button>
    </span>
  </div>`;
}

function renderOptRun(view, sum) {
  const runId = sum.run.id;
  view.innerHTML = `<div class="opt-shell">
    <div class="opt-bar">
      ${backButton('optBack', t('op.backToRuns'))}
      <h2 class="opt-bar-title">${t('op.runTitle', { id: runId })}
        <em class="opt-run-when">· ${fmtDate(sum.run.created_at)}</em></h2>
      <span class="opt-bar-end">
        <span class="panel-aside">${t('op.summary', {
          p: sum.pending,
          a: sum.groups.reduce((n, g) => n + g.applied, 0),
          r: sum.groups.reduce((n, g) => n + g.rejected, 0) })}</span>
        <button type="button" class="btn btn-danger btn-sm" id="optDiscard">${t('op.discard')}</button>
      </span>
    </div>
    ${sum.run.note ? `<div class="opt-run-note-line">${esc(sum.run.note)}</div>` : ''}
    <div class="opt-work">
      <div class="opt-rail">
        ${verifiedPanel(sum)}
        ${ledgerPanel(sum)}
      </div>
      <div class="opt-groups">
        <div class="opt-groups-head">
          <h3 class="panel-title">${t('op.grp.title')}</h3>
          <span class="panel-aside">${t('op.grp.aside', {
            n: sum.pending, g: sum.groups.filter(g => g.pending).length })}</span>
          <span class="opt-foot-gap"></span>
          <button type="button" class="btn btn-sm" id="optApplyAll"
                  ${sum.pending ? '' : 'disabled'}>${t('op.applyAll')}</button>
        </div>
        <div class="opt-grp-scroll">
          ${sum.groups.length ? `
            <div class="opt-grp opt-grp-th">
              <span></span><span>${t('op.grp.col.kind')}</span><span>${t('op.grp.col.what')}</span>
              <span class="opt-grp-n">${t('op.grp.col.pending')}</span>
              <span>${t('op.grp.col.checked')}</span><span></span>
            </div>
            ${sum.groups.map(groupRow).join('')}
          ` : `<div class="empty">${t('op.emptyRun')}</div>`}
        </div>
        <div class="opt-groups-foot">
          <span class="dot" style="--c:var(--warn)"></span>
          <span>${t('op.grp.legend')}</span>
          <span class="opt-foot-gap"></span>
          <span class="opt-grp-note">${t('op.grp.footNote')}</span>
        </div>
      </div>
    </div>
  </div>`;

  const reload = () => renderOptimization(view, new URLSearchParams({ run: String(runId) }),
                                          { stale: () => false });

  $('#optBack').addEventListener('click', () => upTo());
  view.querySelectorAll('[data-open]').forEach(b => b.addEventListener('click',
    () => go('optimization', { run: runId, kind: b.dataset.open })));

  view.querySelectorAll('[data-applykind]').forEach(b => b.addEventListener('click', async () => {
    const kind = b.dataset.applykind, n = +b.dataset.n;
    if (!(await confirmModal({ title: t('op.group.applyConfirm.title'),
      body: t('op.group.applyConfirm.body', { n, kind: kindLabel(kind), id: runId }),
      okLabel: t('op.group.applyConfirm.ok') }))) return;
    b.disabled = true;
    try {
      reportApplied(await api('/api/optimization/apply-all', { body: { run: runId, kind } }));
      await reload();
    } catch (err) { failed('err.optimize', err); b.disabled = false; }
  }));

  view.querySelectorAll('[data-rejectkind]').forEach(b => b.addEventListener('click', async () => {
    const kind = b.dataset.rejectkind, n = +b.dataset.n;
    if (!(await confirmModal({ title: t('op.group.rejectConfirm.title'),
      body: t('op.group.rejectConfirm.body', { n, kind: kindLabel(kind), id: runId }),
      okLabel: t('op.group.rejectConfirm.ok') }))) return;
    b.disabled = true;
    try {
      const r = await api('/api/optimization/reject-all', { body: { run: runId, kind } });
      toast(t('op.toast.rejectedN', { n: r.rejected }), 'ok');
      await reload();
    } catch (err) { failed('err.optimize', err); b.disabled = false; }
  }));

  /* An undone group goes back to pending, so this is the inverse of the
     group Apply above and not a second kind of write. */
  view.querySelectorAll('[data-undokind]').forEach(b => b.addEventListener('click', async () => {
    const n = +b.dataset.n;
    if (!(await confirmModal({ title: t('op.group.undoConfirm.title'),
      body: t('op.group.undoConfirm.body', { n, kind: kindLabel(b.dataset.undokind), id: runId }),
      okLabel: t('op.group.undoConfirm.ok') }))) return;
    b.disabled = true;
    try {
      const r = await api(`/api/optimization/suggestions?run=${seg(runId)}&kind=${seg(b.dataset.undokind)}&status=applied`);
      for (const s of r.suggestions) await api('/api/optimization/revert', { body: { id: s.id } });
      toast(t('op.toast.revertedN', { n: r.suggestions.length }), 'ok');
      await reload();
    } catch (err) { failed('err.optimize', err); b.disabled = false; }
  }));

  $('#optApplyAll').addEventListener('click', async () => {
    if (!sum.pending) { toast(t('op.toast.nothingPending'), ''); return; }
    if (!(await confirmModal({ title: t('op.applyAllConfirm.title'),
      body: t('op.applyAllConfirm.body', { n: sum.pending, id: runId }),
      okLabel: t('op.applyAllConfirm.ok') }))) return;
    try {
      reportApplied(await api('/api/optimization/apply-all', { body: { run: runId } }));
      await reload();
    } catch (err) { failed('err.optimize', err); }
  });

  $('#optDiscard').addEventListener('click', async () => {
    if (!(await confirmModal({ title: t('op.discardConfirm.title'),
      body: t('op.discardConfirm.body', { id: runId }),
      okLabel: t('op.discardConfirm.ok'), danger: true }))) return;
    try {
      await api(`/api/optimization/runs/${seg(runId)}`, { method: 'DELETE' });
      toast(t('op.toast.discarded'), 'ok');
      go('optimization');
    } catch (err) { failed('err.optimize', err); }
  });
}

/* ─── what a level-2 list is ABOUT ────────────────────────────────────────
   One kind of one run, or a whole day across runs and kinds. The list, the
   evidence pane, the selection and its footer are the same work either way;
   these are the only things that differ, so they are the only things the
   two builders below disagree on. */

function kindScope(sum, kind) {
  const runId = sum.run.id;
  const meta = sum.groups.find(g => g.kind === kind);
  return {
    kind,
    title: kindLabel(kind),
    sub: meta ? t('op.group.countPending', { p: meta.pending, t: meta.total })
              : t('op.group.countAll', { t: 0 }),
    listAria: t('op.sel.listAria', { kind: kindLabel(kind) }),
    emptyMsg: t('op.emptyGroup'),
    back: { label: t('op.runTitle', { id: runId }), to: { run: runId } },
    query: `run=${seg(runId)}&kind=${seg(kind)}`,
    body: { run: runId, kind },
    /* the whole history of the group: what is still open, and what was
       already decided about it */
    keepDecided: false,
    pending: meta ? meta.pending : 0,
    async refresh() {
      const fresh = await api(`/api/optimization/summary?run=${seg(runId)}`);
      const g = fresh.groups.find(x => x.kind === kind);
      return { pending: g ? g.pending : 0, total: g ? g.total : 0,
               sub: t('op.group.countPending', { p: g ? g.pending : 0, t: g ? g.total : 0 }) };
    },
  };
}

function dayScope(day, runsOfDay) {
  const withWork = runsOfDay.filter(r => r.pending);
  const ids = (withWork.length ? withWork : runsOfDay).map(r => r.id);
  const label = (() => {
    try { return fromKey(day).toLocaleDateString(I18N.numberLocale,
      { weekday: 'long', day: 'numeric', month: 'long' }); }
    catch { return day; }
  })();
  const totals = () => runsOfDay.reduce(
    (a, r) => ({ pending: a.pending + r.pending, total: a.total + r.total }),
    { pending: 0, total: 0 });
  const now = totals();
  return {
    kind: '',
    title: label,
    sub: t('op.cal.scopeDay', { n: now.pending, all: now.total }),
    listAria: t('op.cal.dayAria', { day: label }),
    emptyMsg: t('op.cal.allDecided', { n: now.total }),
    back: { label: t('op.title'), to: { day } },
    /* only what is still open. A day can hold thirteen runs, and pulling
       every decided suggestion of all of them to review the four that are
       not is a request the size of the month. */
    query: `runs=${seg(ids.join(','))}&status=pending`,
    body: { runs: ids },
    /* a row that was just decided stays where it was, marked, instead of
       vanishing out from under the reader */
    keepDecided: true,
    pending: now.pending,
    async refresh() {
      const fresh = (await api('/api/optimization/runs')).runs
        .filter(r => dayKey(new Date(r.created_at)) === day);
      const sum = fresh.reduce(
        (a, r) => ({ pending: a.pending + r.pending, total: a.total + r.total }),
        { pending: 0, total: 0 });
      return { ...sum, sub: t('op.cal.scopeDay', { n: sum.pending, all: sum.total }) };
    },
  };
}

/* ─── level 2 — a list beside the evidence for what it holds ─────────── */

/* What a row in the list says about its suggestion, in one line under the
   name: which memory, how much text the rewrite drops, and whether the
   agent checked it. */
function rowMeta(s) {
  const bits = [];
  if (s.target_uid) bits.push(`<span class="opt-row-uid">${esc(s.target_uid)}</span>`);
  /* Only while it is still a proposal. Applying rewrites the memory, so
     `chars_before` is then the length of the new body and the difference
     reads as 0% -- a row saying the rewrite changed nothing. */
  if (s.status === 'pending' && s.chars_before) {
    const pct = Math.round((s.chars_after - s.chars_before) * 100 / s.chars_before);
    bits.push(`<span>${pct > 0 ? '+' : ''}${pct}%</span>`);
  }
  if (s.status === 'applied') bits.push(`<span class="opt-row-done">${t('op.applied')}</span>`);
  else if (s.status === 'rejected') bits.push(`<span class="opt-row-done">${t('op.rejected')}</span>`);
  else bits.push(s.verified
    ? `<span class="opt-row-vf">${t('op.vf.checked')}</span>`
    : `<span class="opt-row-nvf">${t('op.vf.unchecked')}</span>`);
  return bits.join('');
}

/* The name a suggestion goes by in the list. A memory's title if it has
   one, its opening otherwise, and the pair of uids when the suggestion is
   about two memories rather than a field. */
function rowName(s) {
  const tg = s.target || {};
  if (tg.title) return tg.title;
  if (tg.snippet) return tg.snippet;
  const p = s.payload || {};
  if (s.kind === 'link') return `${p.from_uid || '?'} → ${p.to_uid || '?'}`;
  if (s.kind === 'merge') return `${p.keep_uid || '?'} ← ${p.drop_uid || '?'}`;
  if (s.kind === 'distill') return p.title || t('op.distill.new');
  return s.kind;
}

function detailHTML(s, at, total) {
  if (!s) return `<div class="empty">${t('op.pickOne')}</div>`;
  const relKind = s.kind === 'link' || s.kind === 'merge' || s.kind === 'distill';
  /* One pane per SHAPE of change, not one pane for every kind -- see the
     block that defines SET_KINDS. */
  const body = s.kind === 'distill' ? optDistillBody(s)
    : relKind ? optRelBody(s)
    : TEXT_KINDS.has(s.kind) ? textPairHTML(s)
    : CONTENT_KINDS.has(s.kind) ? prosePairHTML(s)
    : FLAG_KINDS.has(s.kind) ? flagPairHTML(s)
    : SET_KINDS.has(s.kind) ? setPairHTML(s)
    : LINE_KINDS.has(s.kind) ? linePairHTML(s)
    : optRaw(s);
  const tg = s.target || {};
  const openUid = s.target_uid || s.new_uid;   /* distill: the created memory, once applied */
  const decided = s.status !== 'pending';
  return `<div class="opt-detail-head">
      <span class="opt-kind" title="${esc(kindTitle(s.kind))}">${esc(kindLabel(s.kind))}</span>
      ${tg.type ? typeTag(tg.type) : ''}
      ${s.target_uid ? uidChip(s.target_uid) : ''}
      ${tg.domain ? `<span class="chip">${esc(tg.domain)}</span>` : ''}
      <span class="opt-foot-gap"></span>
      <span class="opt-detail-at">${t('op.detail.at', { i: at + 1, n: total })}</span>
    </div>
    ${tg.title ? `<div class="opt-detail-title">${esc(tg.title)}</div>` : ''}
    ${s.rationale ? `<div class="opt-why">
      <span class="opt-label">${t('op.why')}</span>
      <div class="opt-why-body rt">${rich(s.rationale, s)}</div></div>` : ''}
    ${relKind || CONTENT_KINDS.has(s.kind) || isBodyLeak(s) || !s.target ? '' : `<div class="opt-preview">
      <span class="opt-label">${t('op.underReview')}</span>
      <div class="snippet">${esc(tg.snippet || '')}</div></div>`}
    ${body}
    <div class="opt-detail-foot">
      ${s.verified
        ? `<span class="opt-verified" title="${esc(s.verified)}">${icon('confirmed')}<span
             class="opt-verified-text">${t('op.verified', { v: esc(s.verified) })}</span></span>`
        : `<span class="opt-verified muted">${icon('unverified')}<span
             class="opt-verified-text">${t('op.noVerified')}</span></span>`}
      ${openUid ? `<button type="button" class="btn btn-sm btn-ghost" data-openopt="${esc(openUid)}">${t('common.openRecord')}</button>` : ''}
      ${decided
        ? (s.status === 'applied'
            ? `<button type="button" class="btn btn-sm" data-revert="${s.id}">${t('common.undo')}</button>`
            : `<span class="status-tag archived">${t('op.rejected')}</span>`)
        : `<button type="button" class="btn btn-sm" data-reject="${s.id}">${t('common.reject')}</button>
           <button type="button" class="btn btn-solid btn-sm" data-apply="${s.id}">${t('common.apply')}</button>`}
    </div>`;
}

function renderOptGroup(view, scope) {
  view.innerHTML = `<div class="opt-shell">
    <div class="opt-bar">
      ${backButton('optBack', scope.back.label)}
      <h2 class="opt-bar-title">${esc(scope.title)}
        <em class="opt-run-when">· ${esc(scope.sub)}</em></h2>
    </div>
    <div class="opt-two">
      <div class="opt-list" id="optList"><div class="loading"><span class="spin"></span></div></div>
      <div class="panel opt-detail" id="optDetail"></div>
    </div>
  </div>`;

  $('#optBack').addEventListener('click', () => upTo(scope.back.to));

  let items = [];            /* the group's suggestions, in id order */
  let picked = 0;            /* which one the detail pane is showing */
  let marked = new Set();    /* the ids the footer's two buttons act on */
  let anchor = 0;            /* where a Shift-extended range starts */

  const pendingIds = () => items.filter(s => s.status === 'pending').map(s => s.id);
  const markedPending = () => pendingIds().filter(id => marked.has(id));

  /* The scope can empty out entirely -- every suggestion decided -- and then
     this level has nothing left to be about. The header says so rather than
     the whole view being rebuilt under the reader. */
  const reloadRun = async () => {
    const fresh = await scope.refresh();
    if (!view.isConnected) return;
    scope.pending = fresh.pending;
    const when = view.querySelector('.opt-run-when');
    if (when) when.textContent = `· ${fresh.sub}`;
  };

  const paintDetail = () => {
    const host = $('#optDetail');
    if (!host) return;
    host.innerHTML = detailHTML(items[picked], picked, items.length);
    /* Before and After are two walls of nearly the same text; the marks are
       what tells them apart. Taken from the panes rather than from the
       payload, so the words marked are the words drawn. */
    markPair(host.querySelector('.opt-diff-b'), host.querySelector('.opt-diff-a'));
    wireCopyChips(host);
    /* the [[uid]] references drawn in the bodies and the rationale open the
       record they name, and a fenced block copies itself -- same two hooks
       the record wires on its own blocks */
    wireRich(host, { open: openRecord, copy: copyCode });
    host.querySelectorAll('[data-openopt]').forEach(b =>
      b.addEventListener('click', () => openRecord(b.dataset.openopt)));
    /* A scope that only asked for what was still open cannot hold the row
       once it is decided, so refetching would make it vanish from under the
       reader mid-walk. There, the row is marked where it stands and only the
       header counts are re-read; a scope holding the whole history refetches
       and shows the decision as the list's own state. */
    const settle = async (id, status) => {
      if (!scope.keepDecided) { await loadList(); return; }
      const it = items.find(x => x.id === id);
      if (it) it.status = status;
      marked.delete(id);
      paintRows();
      paintFoot();
      paintDetail();
      await reloadRun();
    };
    const act = (btn, path, id, msg, undo, status) => async () => {
      btn.disabled = true;
      try {
        const res = await api(path, { body: { id } });
        toast(res && res.backup ? t('op.toast.appliedBackup') : msg, 'ok', undo ? {
          action: {
            label: t('common.undo'),
            run: () => api(undo, { body: { id } })
              .then(async () => { toast(t('op.toast.reverted'), 'ok'); await settle(id, 'pending'); })
              .catch(err => failed('err.optimize', err)),
          },
        } : {});
        await settle(id, status);
      } catch (err) { failed('err.optimize', err); btn.disabled = false; }
    };
    const b1 = host.querySelector('[data-apply]');
    if (b1) b1.addEventListener('click',
      act(b1, '/api/optimization/apply', +b1.dataset.apply, t('op.toast.applied1'), '/api/optimization/revert', 'applied'));
    const b2 = host.querySelector('[data-reject]');
    if (b2) b2.addEventListener('click',
      act(b2, '/api/optimization/reject', +b2.dataset.reject, t('op.toast.rejected1'), null, 'rejected'));
    const b3 = host.querySelector('[data-revert]');
    if (b3) b3.addEventListener('click',
      act(b3, '/api/optimization/revert', +b3.dataset.revert, t('op.toast.reverted'), null, 'pending'));
  };

  const paintFoot = () => {
    const n = markedPending().length;
    const foot = $('#optFoot');
    if (!foot) return;
    foot.querySelector('.opt-foot-n').textContent = t('op.sel.n', { n });
    foot.querySelectorAll('button').forEach(b => { b.disabled = !n; });
    foot.querySelector('[data-selapply]').textContent = t('op.sel.apply', { n });
    foot.querySelector('[data-selreject]').textContent = t('op.sel.reject', { n });
    const head = $('#optSelAll');
    if (head) {
      const all = pendingIds();
      head.checked = all.length > 0 && n === all.length;
      head.indeterminate = n > 0 && n < all.length;
    }
    const count = $('#optSelCount');
    if (count) count.textContent = t('op.sel.ofN', { n, all: pendingIds().length });
  };

  const paintRows = () => {
    const list = view.querySelector('.opt-rows');
    if (!list) return;
    [...list.children].forEach((row, i) => {
      const s = items[i];
      row.classList.toggle('picked', i === picked);
      row.classList.toggle('marked', marked.has(s.id));
      /* the tick is what "selected" means on a row; the cursor is the one row
         that holds the tab stop */
      row.setAttribute('aria-selected', marked.has(s.id) ? 'true' : 'false');
      row.tabIndex = i === picked ? 0 : -1;
      const box = row.querySelector('input[type=checkbox]');
      if (box) box.checked = marked.has(s.id);
    });
  };

  const pick = (i, focus = false) => {
    picked = Math.max(0, Math.min(items.length - 1, i));
    paintRows();
    paintDetail();
    const row = view.querySelectorAll('.opt-row')[picked];
    if (!row) return;
    row.scrollIntoView({ block: 'nearest' });
    if (focus) row.focus();
  };

  const toggle = (i, on) => {
    const s = items[i];
    if (!s || s.status !== 'pending') return;
    if (on === undefined) on = !marked.has(s.id);
    if (on) marked.add(s.id); else marked.delete(s.id);
  };

  async function loadList() {
    const host = $('#optList');
    if (!host) return;
    host.innerHTML = '<div class="loading"><span class="spin"></span></div>';
    try {
      const r = await api(`/api/optimization/suggestions?${scope.query}`);
      if (!host.isConnected) return;
      items = r.suggestions;
      /* A decision removes an id from the pending set; keeping it marked
         would leave the footer counting work that is already answered. */
      const alive = new Set(pendingIds());
      marked = new Set([...marked].filter(id => alive.has(id)));
      if (!items.length) {
        host.innerHTML = `<div class="empty">${esc(scope.emptyMsg)}</div>`;
        $('#optDetail').innerHTML = '';
        await reloadRun();
        return;
      }
      picked = Math.min(picked, items.length - 1);
      host.innerHTML = `
        <div class="opt-list-head">
          <input type="checkbox" id="optSelAll" aria-label="${esc(t('op.sel.all'))}">
          <span class="mg-label" id="optSelCount"></span>
        </div>
        <!-- role="grid" and not listbox, for the reason the memory list is one
             too: a row owns a checkbox, which an option may not contain. -->
        <div class="opt-rows" role="grid" aria-multiselectable="true" aria-label="${esc(scope.listAria)}">
          ${items.map((s, i) => `
            <div class="opt-row" role="row" data-i="${i}" aria-selected="false" tabindex="-1">
              <span class="opt-row-box" role="gridcell">${s.status === 'pending'
                ? `<input type="checkbox" tabindex="-1" aria-label="${esc(t('op.sel.one'))}">`
                : icon(s.status === 'applied' ? 'confirmed' : 'close', { cls: 'opt-row-mark' })}</span>
              <span class="opt-row-body" role="gridcell">
                <span class="opt-row-name">${esc(rowName(s))}</span>
                <span class="opt-row-meta">${rowMeta(s)}</span>
              </span>
            </div>`).join('')}
        </div>
        <div class="opt-list-foot" id="optFoot">
          <span class="opt-foot-n"></span>
          <span class="opt-foot-gap"></span>
          <button type="button" class="btn btn-sm" data-selreject></button>
          <button type="button" class="btn btn-solid btn-sm" data-selapply></button>
        </div>
        <p class="hint-sm opt-keyhint">${t('op.sel.hint')}</p>`;
      wireList();
      paintRows();
      paintFoot();
      paintDetail();
      await reloadRun();
    } catch (err) {
      if (!host.isConnected) return;
      host.innerHTML = failedHTML(err);
      host.querySelector('[data-retry]').addEventListener('click', loadList);
    }
  }

  function wireList() {
    view.querySelectorAll('.opt-row').forEach(row => {
      const i = +row.dataset.i;
      row.addEventListener('click', e => {
        if (e.target.closest('input[type=checkbox]')) return;   /* the box speaks for itself */
        if (e.shiftKey) {
          const [lo, hi] = anchor < i ? [anchor, i] : [i, anchor];
          for (let j = lo; j <= hi; j++) toggle(j, true);
        } else {
          anchor = i;
        }
        pick(i);
        paintFoot();
      });
      const box = row.querySelector('input[type=checkbox]');
      if (box) box.addEventListener('change', () => {
        toggle(i, box.checked);
        anchor = i;
        paintRows();
        paintFoot();
      });
    });

    const rows = view.querySelector('.opt-rows');
    rows.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown') { e.preventDefault(); pick(picked + 1, true); anchor = picked; }
      else if (e.key === 'ArrowUp') { e.preventDefault(); pick(picked - 1, true); anchor = picked; }
      else if (e.key === 'Home') { e.preventDefault(); pick(0, true); anchor = picked; }
      else if (e.key === 'End') { e.preventDefault(); pick(items.length - 1, true); anchor = picked; }
      else if (e.key === ' ') { e.preventDefault(); toggle(picked); paintRows(); paintFoot(); }
      else return;
    });

    $('#optSelAll').addEventListener('change', e => {
      marked = e.target.checked ? new Set(pendingIds()) : new Set();
      paintRows();
      paintFoot();
    });

    /* `what` names the operation in its own confirm: one shared wording
       here would have asked "apply the selection?" before rejecting it. */
    const bulk = async (what, path, msg) => {
      const ids = markedPending();
      if (!ids.length) return;
      if (!(await confirmModal({ title: t(`op.sel.${what}Confirm.title`),
        body: t(`op.sel.${what}Confirm.body`, { n: ids.length, scope: scope.title }),
        okLabel: t(`op.sel.${what}Confirm.ok`) }))) return;
      try {
        const res = await api(path, { body: { ...scope.body, ids } });
        if (msg) toast(msg(res), 'ok'); else reportApplied(res);
        marked = new Set();
        await loadList();
      } catch (err) { failed('err.optimize', err); }
    };
    view.querySelector('[data-selapply]').addEventListener('click',
      () => bulk('apply', '/api/optimization/apply-all', null));
    view.querySelector('[data-selreject]').addEventListener('click',
      () => bulk('reject', '/api/optimization/reject-all',
                 r => t('op.toast.rejectedN', { n: r.rejected })));
  }

  loadList();
}
