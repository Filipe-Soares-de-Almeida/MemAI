/* Choosing which memories to point at, as a form of its own.

   This replaces a one-line lookup field that sat inside whatever form
   needed it. That field worked and was still the wrong shape for the job:

     * one pick per open. Attaching four notes to one step meant four
       rounds of search, type, pick, submit.
     * a 280-character snippet was the whole of what you got to judge by.
       Two checkpoints from the same ticket open with the same sentence,
       so the thing that tells them apart is often past the truncation.
     * nothing said what was about to be linked except the text left in
       the field -- which looked exactly like text somebody had typed.

   So: a dialog. Results on the left, the FULL memory on the right, and
   what has been chosen in a tray that is impossible to miss. Nothing is
   written until Link is pressed, and the caller gets a list of uids --
   what to do with them (a relation, a node link, a jump) stays the
   caller's business.

   Opened over the form that raised it, never instead of it: see the
   modal stack in core/ui.js. */

import { esc, fmtDate, fmtInt, debounce } from './dom.js';
import { api, seg } from './api.js';
import { icon } from './icons.js';
import { openModal, closeModal } from './ui.js';
import { typeColor, typeClass, confPill, statusTag, uidChip, relTypeField,
         wireRelTypeField, TYPE_LABEL, typeItems, getDomains } from './shared.js';
import { pickerFor, setPickerValue, wirePicker, fixedItems } from './pick.js';
import { domainPickerHTML, wireDomainPicker } from './domain-picker.js';
import { t } from '../i18n.js';

/* Records already fetched for the preview pane. Arrowing down a list of
   twenty re-reads the same rows on the way back up, and a memory does not
   change while a dialog is open over it -- but it certainly can between two
   dialogs, so this is emptied every time one opens. */
const previews = new Map();

const previewOf = async uid => {
  if (!previews.has(uid)) previews.set(uid, await api(`/api/memories/${seg(uid)}`));
  return previews.get(uid);
};

/* the score behind a match badge -- reference for judging a borderline
   candidate, which is why it is a title and not a column */
const scoreTitle = it =>
  it.fts_rank != null ? `bm25 ${Number(it.fts_rank).toFixed(2)}` : '';

/* One result. A real button, so the keyboard reaches it; `aria-pressed`
   rather than a checkbox because pressing it is what selects, and a
   separate box to tick would be a second target for one decision. */
const rowHTML = (it, { picked, linked }) => {
  const meta = [
    it.domain ? `<span class="picker-domain">${esc(it.domain)}</span>` : '',
    `<span>${esc(TYPE_LABEL[it.type] || it.type)}</span>`,
    it.match_source
      ? `<span class="match-badge" title="${esc(scoreTitle(it))}">${esc(it.match_source)}</span>` : '',
    `<span class="picker-uid">${esc(it.uid)}</span>`,
    it.status === 'archived' ? statusTag('archived') : '',
    linked ? `<span class="status-tag">${t('lp.alreadyLinked')}</span>` : '',
  ].filter(Boolean).join('<span class="picker-sep" aria-hidden="true">·</span>');
  return `
    <button type="button" class="picker-item lp-item${linked ? ' lp-linked' : ''}"
            data-uid="${esc(it.uid)}" aria-pressed="${picked ? 'true' : 'false'}"
            ${linked ? 'disabled' : ''}>
      <span class="lp-check" aria-hidden="true">${icon(picked ? 'confirmed' : 'unverified')}</span>
      <span class="picker-snippet">${esc(it.snippet)}</span>
      <span class="picker-meta">${meta}</span>
    </button>`;
};

/* What the right-hand pane says about one memory: everything the row had
   to leave out, and the whole content rather than its first 280 chars. */
const previewHTML = m => `
  <div class="lp-prev-head">
    <span class="type-tag ${typeClass(m.type)}"><span class="dot"></span>${esc(m.type)}</span>
    ${confPill(m.confidence)}
    ${statusTag(m.status)}
    ${uidChip(m.uid)}
  </div>
  <div class="lp-prev-meta">
    ${m.domain ? `<span class="chip">${esc(m.domain)}</span>` : ''}
    ${m.tags ? `<span class="lp-prev-tags">${esc(m.tags)}</span>` : ''}
    <span>${fmtDate(m.created_at)}</span>
    <span>${t('lp.size', { n: fmtInt((m.content || '').length) })}</span>
  </div>
  <pre class="content-pre content-prose lp-prev-body">${esc(m.content || '')}</pre>`;

/**
 * Pick memories to link to. Resolves to `{uids, relation, note}`, or null
 * when the dialog was dismissed. `uids` is never empty on a resolve --
 * confirming is disabled until something is chosen.
 *
 * title       dialog heading
 * exclude     a uid that must not be pickable (the record being edited)
 * linked      uids already tied to this thing -- listed, not pickable twice
 * multi       several at once (the default) or exactly one
 * type        lock the type filter, e.g. 'diagram' for a jump target
 * okLabel     verb on the confirm button; the caller knows what it means
 * relOptions  suggested relation types; omit for a tie that has no type
 * relValue    which of them starts selected
 * withNote    offer a note to store on the tie
 *
 * The relation type and the note live HERE rather than on the form behind
 * this one: they are part of the same decision as which memory, and split
 * across two surfaces they were being filled in before the thing they
 * describe had been chosen.
 */
export function pickMemories({
  title, exclude = '', linked = [], multi = true, type = '', okLabel,
  relOptions = null, relValue = '', withNote = false,
} = {}) {
  return new Promise(resolve => {
    previews.clear();
    const already = new Set(linked);
    /* uid -> the row that was picked, so the tray can name what was chosen
       after the search that found it has been typed over */
    const picked = new Map();
    const filters = { type, domain: '', tag: '', status: 'active' };
    const lpTypes = typeItems({ any: t('lookup.filter.type') });
    let items = [];
    let focused = null;              /* the uid the preview pane is showing */
    let seq = 0;                     /* filters and typing race; last wins */

    const modal = openModal({
      title: esc(title || t('lp.title')),
      wide: true,
      tall: true,
      bodyHTML: `
        <div class="lp">
          <div class="lp-find">
            <input type="text" class="lp-q" id="lpQ" autocomplete="off"
                   placeholder="${t('lp.search')}" aria-label="${t('lp.search')}">
            <div class="picker-filters" id="lpFilters">
              ${type ? '' : pickerFor({ id: 'lpFType', items: lpTypes,
                                        ariaLabel: t('lookup.filter.type') })}
              ${domainPickerHTML({ id: 'lpFDomain', ariaLabel: t('lookup.filter.domain') })}
              <input type="text" data-f="tag" placeholder="${t('lookup.filter.tag')}"
                     aria-label="${t('lookup.filter.tag')}" autocomplete="off">
              <label class="inline-label">
                <input type="checkbox" data-f="archived">${t('lookup.filter.archived')}
              </label>
            </div>
            <div class="lp-list" id="lpList" role="group" aria-label="${t('lookup.aria')}"></div>
            <div class="lp-foot-note" id="lpMore" hidden></div>
          </div>
          <div class="lp-prev" id="lpPrev" aria-live="polite">
            <div class="dg-empty">${t('lp.previewHint')}</div>
          </div>
        </div>
        ${relOptions || withNote ? `<div class="lp-form">
          ${relOptions ? relTypeField({
            selId: 'lpRel', customId: 'lpRelCustom', options: relOptions,
            value: relValue, ariaLabel: t('dr.rel.type.placeholder') }) : ''}
          ${withNote ? `<input type="text" id="lpNote" class="lp-note"
                 placeholder="${t('dr.rel.note.placeholder')}"
                 aria-label="${t('dr.rel.note.placeholder')}">` : ''}
          <div class="field-error" id="lpRelError" role="alert" hidden></div>
        </div>` : ''}
        <div class="lp-tray" id="lpTray" hidden></div>`,
      footHTML: `<span class="lp-count" id="lpCount"></span>
                 <button class="btn" data-x>${t('common.cancel')}</button>
                 <button class="btn btn-solid" data-ok disabled>${esc(okLabel || t('lp.ok'))}</button>`,
    });

    const mq = s => modal.querySelector(s);
    const listEl = mq('#lpList');
    const trayEl = mq('#lpTray');
    const prevEl = mq('#lpPrev');
    const okBtn = mq('[data-ok]');
    const input = mq('#lpQ');

    /* The domain list arrives after the dialog does, so the picker is wired
       once it lands -- unfiltered until then rather than absent, which is the
       same bargain the select version made with its empty <option>. */
    getDomains().then(ds => {
      wireDomainPicker(modal, { id: 'lpFDomain', domains: ds, onPick: domain => {
        filters.domain = domain;
        run();
      } });
    }).catch(() => { /* the filter is an accelerator, not a requirement */ });

    const anyFilter = () => Boolean((type ? '' : filters.type) || filters.domain || filters.tag)
      || filters.status !== 'active';

    /* ── the tray: the answer to "what am I about to link?" ──────────── */
    function paintTray() {
      const n = picked.size;
      trayEl.hidden = n === 0;
      okBtn.disabled = n === 0;
      mq('#lpCount').textContent = n ? t('lp.chosen', { n }) : '';
      trayEl.innerHTML = !n ? '' : `
        <span class="lp-tray-label">${t('lp.chosenLabel')}</span>
        ${[...picked.values()].map(it => `
          <span class="lp-chip" title="${esc(it.snippet)}">
            <span class="dot" style="--c:${typeColor(it.type)}"></span>
            <span class="lp-chip-text">${esc(it.snippet)}</span>
            <button type="button" class="icon-btn" data-drop="${esc(it.uid)}"
                    aria-label="${esc(t('lp.unchoose', { uid: it.uid }))}">${icon('close')}</button>
          </span>`).join('')}`;
      trayEl.querySelectorAll('[data-drop]').forEach(b =>
        b.addEventListener('click', () => toggle(b.dataset.drop)));
    }

    function toggle(uid) {
      if (already.has(uid)) return;
      if (picked.has(uid)) picked.delete(uid);
      else {
        /* single mode is a radio, not a stack of one: picking replaces */
        if (!multi) picked.clear();
        const it = items.find(i => i.uid === uid) || { uid, snippet: uid, type: '' };
        picked.set(uid, it);
      }
      syncRows();
      paintTray();
    }

    /* pressed state without repainting the list: a repaint moves the caret
       off the row that was just pressed, and pressing a row is how you pick */
    function syncRows() {
      listEl.querySelectorAll('[data-uid]').forEach(b => {
        const on = picked.has(b.dataset.uid);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
        b.querySelector('.lp-check').innerHTML = icon(on ? 'confirmed' : 'unverified');
      });
    }

    /* ── the preview pane ───────────────────────────────────────────── */
    async function showPreview(uid) {
      if (uid === focused) return;
      focused = uid;
      prevEl.innerHTML = '<div class="loading"><span class="spin"></span></div>';
      let m;
      try { m = await previewOf(uid); }
      catch {
        if (focused !== uid) return;
        prevEl.innerHTML = `<div class="picker-note picker-note-bad">${t('lp.previewFailed')}</div>`;
        return;
      }
      if (focused !== uid) return;         /* arrowed past it while it loaded */
      prevEl.innerHTML = previewHTML(m);
    }

    /* ── the search ─────────────────────────────────────────────────── */
    async function run() {
      const mine = ++seq;
      /* URLSearchParams and not query(): an empty `status` is a real value
         here -- it means "archived as well" -- and query() drops empties */
      const qs = new URLSearchParams({
        q: input.value.trim(), exclude, limit: '30',
        type: filters.type, domain: filters.domain, tag: filters.tag,
        status: filters.status,
      });
      let r;
      try {
        r = await api(`/api/lookup?${qs}`);
      } catch {
        if (mine !== seq) return;
        listEl.innerHTML = `<div class="picker-note picker-note-bad">${t('lookup.failed')}</div>`;
        mq('#lpMore').hidden = true;
        return;
      }
      if (mine !== seq) return;
      items = r.items;
      listEl.innerHTML = items.map(it => rowHTML(it, {
        picked: picked.has(it.uid), linked: already.has(it.uid),
      })).join('') || `
        <div class="picker-note">
          ${anyFilter() ? t('lookup.emptyFiltered') : t('lookup.empty')}
          ${anyFilter() ? `<button type="button" class="btn btn-sm" data-clearf>${t('lookup.filter.clear')}</button>` : ''}
        </div>`;
      mq('#lpMore').textContent = r.has_more ? t('lookup.more') : '';
      mq('#lpMore').hidden = !r.has_more;

      listEl.querySelectorAll('[data-uid]').forEach(b => {
        b.addEventListener('click', () => toggle(b.dataset.uid));
        /* the preview follows attention, not selection: reading a candidate
           is how you decide, and having to pick it first to read it would
           make every rejected candidate an undo */
        b.addEventListener('focus', () => showPreview(b.dataset.uid));
        b.addEventListener('pointerenter', () => showPreview(b.dataset.uid));
      });
      listEl.querySelector('[data-clearf]')?.addEventListener('click', () => {
        Object.assign(filters, { type, domain: '', tag: '', status: 'active' });
        modal.querySelectorAll('[data-f]').forEach(el => {
          if (el.type === 'checkbox') el.checked = false; else el.value = '';
        });
        /* the pickers hold their own value, so clearing has to say so on the
           button too -- an emptied filter that still reads "note" is a lie */
        setPickerValue(mq('#lpFType'), lpTypes[0]);
        setPickerValue(mq('#lpFDomain'), { value: '', label: t('common.allDomains') });
        run();
      });
      /* Something in the pane from the first paint, so the right half is
         not an empty panel until the pointer happens to cross a row. */
      if (!focused && items.length) showPreview(items[0].uid);
    }

    const runSoon = debounce(run, 280);

    wirePicker(modal, { id: 'lpFType', items: fixedItems(lpTypes), onPick: v => {
      filters.type = v;
      run();
    } });
    modal.querySelectorAll('[data-f]').forEach(el => {
      const read = () => {
        if (el.dataset.f === 'archived') filters.status = el.checked ? '' : 'active';
        else filters[el.dataset.f] = el.value;
      };
      /* a select or a checkbox is a discrete decision and answers at once;
         only free text waits for the typist to stop */
      el.addEventListener(el.tagName === 'INPUT' && el.type === 'text' ? 'input' : 'change',
        () => { read(); (el.type === 'text' ? runSoon : run)(); });
    });

    input.addEventListener('input', runSoon);
    /* Down walks from the field into the list; from a row it walks the
       rows. Enter on a row is its click, which the button already does. */
    input.addEventListener('keydown', e => {
      if (e.key !== 'ArrowDown') return;
      const first = listEl.querySelector('[data-uid]:not([disabled])');
      if (first) { e.preventDefault(); first.focus(); }
    });
    listEl.addEventListener('keydown', e => {
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
      const rows = [...listEl.querySelectorAll('[data-uid]:not([disabled])')];
      const next = rows[rows.indexOf(document.activeElement) + (e.key === 'ArrowDown' ? 1 : -1)];
      e.preventDefault();
      (next || input).focus();
    });

    const clear = () => {
      for (const el of [mq('#lpRel'), mq('#lpRelCustom')]) el?.setAttribute('aria-invalid', 'false');
      mq('#lpRelError').hidden = true;
    };
    const readRel = relOptions
      ? wireRelTypeField(modal, {
          selId: 'lpRel', customId: 'lpRelCustom', options: relOptions, onPick: clear })
      : () => '';
    if (relOptions) {
      mq('#lpRelCustom').addEventListener('input', clear);
    }

    mq('[data-x]').onclick = () => { closeModal(); resolve(null); };
    okBtn.onclick = () => {
      const relation = readRel();
      /* "other…" with nothing typed after it is not a relation type. Left to
         the caller it becomes a silent fall back to that caller's default --
         a tie stored under a name the operator did not choose. */
      if (relOptions && !relation) {
        const bad = mq('#lpRelCustom').hidden ? mq('#lpRel') : mq('#lpRelCustom');
        bad.setAttribute('aria-invalid', 'true');
        mq('#lpRelError').textContent = t('dr.rel.pickType');
        mq('#lpRelError').hidden = false;
        bad.focus();
        return;
      }
      const out = {
        uids: [...picked.keys()],
        relation,
        note: mq('#lpNote')?.value || '',
      };
      closeModal();
      resolve(out);
    };

    paintTray();
    run();
  });
}
