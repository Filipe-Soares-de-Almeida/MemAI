/* A task's checklist: every write repaints it from the {task, status} the server answers; one item's
   panel is open at a time. `onStatus` reports a close or reopen, `onWrite` any accepted write. */

import { esc, fmtAgo, fmtDate, fmtInt } from '../core/dom.js';
import { api, seg } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, failed, openDropMenu, confirmModal, copyCode, keysHTML, saveKeysHTML } from '../core/ui.js';
import { renderRich, wireRich } from '../core/richtext.js';
import { highlightIn } from '../core/highlight.js';
import { pickMemories } from '../core/link-picker.js';
import { typeTag } from '../core/shared.js';
import { go, parseHash, refreshBehind } from '../core/router.js';
import { t } from '../i18n.js';
import { MEMORY, TASK } from '../contract.js';

const STATES = TASK.ITEM_STATES;

/* One click on an item's mark moves it along todo, doing, done; a done or a
   dropped item goes back to todo. */
const NEXT = { todo: 'doing', doing: 'done', done: 'todo', dropped: 'todo' };

const OLDER_SHOWN = 3;

const NOTE_TITLE_MAX = MEMORY.TITLE_MAX;
const { NOTE_MAX } = TASK;

/* the opening of a body as plain words: markup that only means something when drawn goes */
const peekOf = text => String(text || '')
  .replace(/```[^\n]*|={2,}|\|?\s*:?-{2,}:?\s*(?=\||$)|[|`*]/gm, ' ')
  .replace(/\s+/g, ' ').trim().slice(0, 180);

/* What survives a repaint, per task: the open item, the editors that are open,
   and the text typed but not yet sent. */
let ui = null;
const fresh = uid => ({
  uid, open: '', goalEditing: false, adding: false, allComments: false,
  /* the note being edited by id, or `new:<item key>`; its draft outlives a repaint */
  noteEdit: '', noteDraft: null,
  /* notes opened or shut by hand; a list of one note starts open */
  notesOpen: new Set(), notesShut: new Set(),
  drafts: new Map(), progress: null,
  /* what the last paint drew, so the next one animates only what is new */
  seen: null, pulse: '', opened: '',
});

/* how long a deleted item takes to leave before the list is repainted */
const LEAVE_MS = 160;
/* one lap of the in-progress arc, as --spin in admin.css */
const SPIN_S = 3.2;
const commentKey = c => `${c.created_at}|${c.item}|${c.author}|${c.body.length}`;

/* A selector that finds the same control again after a repaint. */
const selectorOf = el => {
  if (el.id) return `#${CSS.escape(el.id)}`;
  if (el.hasAttribute('data-unlink'))
    return `[data-unlink="${CSS.escape(el.dataset.unlink)}"][data-item="${CSS.escape(el.dataset.item)}"]`;
  for (const a of ['data-step', 'data-toggle', 'data-menu', 'data-draft', 'data-send', 'data-link',
                   'data-note-field', 'data-note-open', 'data-note-edit', 'data-note-add', 'data-note-scope'])
    if (el.hasAttribute(a)) return `[${a}="${CSS.escape(el.getAttribute(a))}"]`;
  return '';
};
const attr = (name, value) => `[${name}="${CSS.escape(value)}"]`;

export function mountTask(host, { uid, task, status }, { onStatus, onWrite } = {}) {
  if (!ui || ui.uid !== uid) ui = fresh(uid);
  /* this mount's own state: a write that lands after another task has taken
     the module's `ui` still clears its drafts on the object it was started on */
  const state = ui;
  let current = task;
  let currentStatus = status;
  let busy = false;
  /* where focus goes on the next repaint, first match wins; a control that is
     about to leave the page names its successor here */
  let want = [];
  const focusAfter = (...selectors) => { want = selectors; };
  const alive = () => host.isConnected;
  /* what this paint animates in: items and comments not drawn before, the
     mark that just changed, the panel that just opened, a task that just closed */
  let enter = { items: new Set(), comments: new Set(), pulse: '', opened: '', closed: false };

  /* ── the write path ── */

  const apply = res => {
    const was = currentStatus;
    current = res.task;
    currentStatus = res.status;
    if (alive()) paint();
    if (alive() && was !== currentStatus) onStatus?.(currentStatus);
  };

  /* One write at a time, so a second click cannot read a stale state; `onOk`
     runs (and is awaited) on acceptance, before the repaint. */
  const write = async (path, body, { method = 'POST', errKey = 'task.err.save', onOk } = {}) => {
    if (busy) { want = []; return null; }
    busy = true;
    try {
      const res = await api(`/api/tasks/${seg(uid)}/${path}`, { method, body });
      await onOk?.(state);
      apply(res);
      if (!alive()) refreshIfShown();
      else onWrite?.(res);
      return res;
    } catch (err) {
      want = [];
      failed(errKey, err);
      return null;
    } finally {
      busy = false;
    }
  };

  const refreshIfShown = () => {
    const { name, params } = parseHash();
    if (name === 'memory' && params.get('uid') === uid) refreshBehind();
  };

  /* A write that closes the task says so, and Undo puts the item back in
     the state it was in. Undo is itself a write, and says nothing more. */
  const setItem = async (key, next, { quiet = false } = {}) => {
    const before = current.items.find(i => i.key === key)?.state;
    const wasOpen = current.state === 'open';
    const res = await write('item', { item: key, state: next }, { onOk: s => { s.pulse = key; } });
    if (!res || quiet) return;
    if (wasOpen && res.task.state !== 'open') {
      toast(t(`task.toast.${res.task.state}`), 'ok', {
        action: { label: t('common.undo'), run: () => {
          focusAfter(attr('data-step', key));
          setItem(key, before, { quiet: true });
        } },
      });
    } else if (!wasOpen && res.task.state === 'open') {
      toast(t('task.toast.reopened'), 'ok');
    }
  };

  /* Deleting asks first, plays the row out, then focuses the next item's mark, else the
     previous one's, else the add control. */
  const deleteItem = async key => {
    const item = current.items.find(i => i.key === key);
    if (!item || busy) return;
    const ok = await confirmModal({
      title: t('task.delete.title'), body: t('task.delete.body', { text: esc(item.text) }),
      okLabel: t('task.item.delete'), danger: true });
    if (!ok) return;
    const at = current.items.findIndex(i => i.key === key);
    if (at < 0) return;
    const wasOpen = current.state === 'open';
    focusAfter(...[current.items[at + 1], current.items[at - 1]].filter(Boolean)
      .map(i => attr('data-step', i.key)), '#tkAddOpen');
    const res = await write('item', { item: key }, {
      method: 'DELETE', errKey: 'task.err.delete',
      onOk: async s => {
        if (s.open === key) s.open = '';
        s.drafts.delete(key);
        host.querySelector(`.tk-item${attr('data-key', key)}`)?.classList.add('is-leaving');
        await new Promise(done => setTimeout(done, LEAVE_MS));
      },
    });
    if (!res) return;
    toast(t(wasOpen && res.task.state !== 'open' ? `task.toast.${res.task.state}` : 'task.toast.deleted'), 'ok');
  };

  /* ── painting ── */

  const progressOf = () => {
    const total = current.items.length;
    const count = s => current.items.filter(i => i.state === s).length;
    return { total, done: count('done'), dropped: count('dropped') };
  };

  /* A comment is a line of a thread: the writer's mark (a round person, or a
     square prompt for an agent), who and when, then the message. */
  const commentHTML = c => {
    const person = c.author === 'person';
    return `<article class="tk-c ${person ? 'is-person' : 'is-agent'}${enter.comments.has(commentKey(c)) ? ' is-new' : ''}">
      <span class="tk-c-av" aria-hidden="true">${icon(person ? 'person' : 'agent')}</span>
      <div class="tk-c-main">
        <header class="tk-c-head">
          <span class="tk-c-who">${esc(t(person ? 'task.author.person' : 'task.author.agent'))}</span>
          ${c.session && !person
            ? `<span class="tk-c-session" title="${esc(c.session)}">${esc(c.session.slice(0, 14))}</span>` : ''}
          <time class="tk-c-when" datetime="${esc(c.created_at)}" title="${esc(c.created_at)}">${fmtAgo(c.created_at)}</time>
        </header>
        <p class="tk-c-body">${esc(c.body)}</p>
      </div>
    </article>`;
  };

  /* the thread's last line: your reply, with the send control inside its box */
  const composerHTML = (scope, { reply = false } = {}) => {
    const ph = t(reply ? 'task.comment.reply' : 'task.comment.placeholder');
    const draft = state.drafts.get(scope) || '';
    return `<div class="tk-c tk-compose is-person">
      <span class="tk-c-av" aria-hidden="true">${icon('person')}</span>
      <div class="tk-reply">
        <textarea class="tk-box" rows="2" data-draft="${esc(scope)}"
                  placeholder="${esc(ph)}" aria-label="${esc(ph)}">${esc(draft)}</textarea>
        <div class="tk-reply-foot">
          <span class="tk-hint">${saveKeysHTML(t('task.comment.hint'))}</span>
          <button type="button" class="btn btn-sm btn-solid" data-send="${esc(scope)}" ${draft.trim() ? '' : 'disabled'}>${t('task.comment.send')}</button>
        </div>
      </div>
    </div>`;
  };

  const scopeSum = n => (n ? t('task.note.scope.n', { n }) : t('task.note.scope.all'));

  /* Which items a note applies to, picked from the checklist itself; none picked is the whole task. */
  const scopeHTML = chosen => `<div class="tk-scope">
      <div class="tk-scope-head">
        <span class="rf-sub">${t('task.note.scope')}</span>
        <span class="tk-scope-sum" data-note-scope-sum>${scopeSum(chosen.size)}</span>
      </div>
      <div class="tk-scope-list" role="group" aria-label="${esc(t('task.note.scope.aria'))}">
        ${current.items.map(i => `<label class="tk-scope-row" title="${esc(i.text)}">
            <input type="checkbox" data-note-scope="${esc(i.key)}"${chosen.has(i.key) ? ' checked' : ''}>
            <span class="tk-scope-text">${esc(i.text)}</span>
          </label>`).join('')}
      </div>
    </div>`;

  /* The record's field editor without its preview: saving is what shows the note as it reads. */
  const noteFormHTML = (note, scope) => {
    const d = state.noteDraft;
    const id = note ? String(note.id) : `new:${scope}`;
    return `<section class="rf is-open tk-note" data-note-form="${esc(id)}">
      <header class="rf-head">
        <input class="tk-note-title-box" data-note-field="title" maxlength="${NOTE_TITLE_MAX}" spellcheck="false"
               aria-label="${esc(t('task.note.title'))}" placeholder="${esc(t('task.note.title'))}"
               value="${esc(d.title)}">
      </header>
      <div class="rf-split rf-solo">
        <div class="rf-pane">
          <textarea data-note-field="body" rows="12" spellcheck="false" maxlength="${NOTE_MAX}"
                    aria-label="${esc(t('task.note.body'))}" placeholder="${esc(t('task.note.body'))}">${esc(d.body)}</textarea>
          <div class="tk-note-foot">
            <span class="tk-hint">${saveKeysHTML(t('dr.key.save'))}${keysHTML(['Esc'], t('dr.key.close'))}</span>
            <span class="rf-count" data-note-count>${t('dr.sections.count', { n: d.body.length, max: NOTE_MAX })}</span>
          </div>
        </div>
      </div>
      ${current.items.length ? scopeHTML(new Set(d.items)) : ''}
      <div class="rf-save">
        <button type="button" class="btn btn-solid btn-sm" data-note-save="${esc(id)}">${t('common.save')}</button>
        <button type="button" class="btn btn-sm" data-note-cancel>${t('common.cancel')}</button>
        ${note ? `<button type="button" class="btn btn-sm btn-danger tk-note-del" data-note-del="${note.id}">${icon('trash')}${t('task.note.delete')}</button>` : ''}
      </div>
    </section>`;
  };

  const noteIsOpen = (n, list) => state.notesOpen.has(String(n.id))
    || (list.length === 1 && !state.notesShut.has(String(n.id)));

  /* A note reads as a record field: its name on the raised head, its body as rich text. Shut, the
     head carries the opening lines, as a row of the record's index does. */
  const noteHTML = (n, list) => {
    if (String(n.id) === state.noteEdit) return noteFormHTML(n, '');
    const open = noteIsOpen(n, list);
    return `<article class="rf tk-note${open ? '' : ' is-shut'}">
      <header class="rf-head">
        <button type="button" class="tk-note-toggle" data-note-open="${n.id}"
                aria-expanded="${open}"${open ? ` aria-controls="tkn-${n.id}"` : ''}>
          <span class="tk-chev" aria-hidden="true">${icon('chevron-right')}</span>
          <span class="tk-note-name">
            <span class="tk-note-title">${esc(n.title)}</span>
            ${open ? '' : `<span class="tk-note-peek">${esc(peekOf(n.body))}</span>`}
          </span>
        </button>
        <button type="button" class="rf-edit" data-note-edit="${n.id}"
                aria-label="${esc(t('task.note.edit'))}">${icon('pencil')}${t('common.edit')}</button>
      </header>
      ${open ? `<div class="rf-body" id="tkn-${n.id}"><div class="content-prose rt">${renderRich(n.body, n.body_links)}</div></div>` : ''}
    </article>`;
  };

  const notesHTML = (list, scope) => {
    const writing = state.noteEdit === `new:${scope}`;
    return `<div class="tk-sub">
        <h3 class="tk-sub-h">${t('task.notes')}<span class="rs-n">${list.length}</span></h3>
        <button type="button" class="rs-more" data-note-add="${esc(scope)}">${t('task.note.add')}</button>
      </div>
      <div class="tk-notes">
        ${list.map(n => noteHTML(n, list)).join('') || (writing ? '' : `<div class="hint-sm">${t('task.notes.empty')}</div>`)}
        ${writing ? noteFormHTML(null, scope) : ''}
      </div>`;
  };

  const itemPanelHTML = item => {
    const thread = current.comments.filter(c => c.item === item.key);
    return `<div class="tk-panel${enter.opened === item.key ? ' is-enter' : ''}" id="tkp-${esc(item.key)}" role="group"
                 aria-label="${esc(t('task.panel.aria', { text: item.text }))}">
      ${notesHTML(current.notes.filter(n => n.items.includes(item.key)), item.key)}
      <div class="tk-sub">
        <h3 class="tk-sub-h">${t('task.links')}<span class="rs-n">${item.links.length}</span></h3>
        <button type="button" class="rs-more" data-link="${esc(item.key)}">${t('task.link.add')}</button>
      </div>
      ${item.links.length ? `<div class="tk-links">${item.links.map(l => `<div class="rs-rel tk-link">
          ${typeTag(l.type)}
          <button type="button" class="snippet tk-link-open" data-open="${esc(l.uid)}"
                  title="${esc(l.uid)}">${esc(l.title || l.uid)}</button>
          <button type="button" class="icon-btn danger" data-unlink="${esc(l.uid)}"
                  data-item="${esc(item.key)}" title="${esc(t('task.link.remove'))}"
                  aria-label="${esc(t('task.link.removeNamed', { title: l.title || l.uid }))}">${icon('close')}</button>
        </div>`).join('')}</div>`
        : `<div class="hint-sm">${t('task.links.empty')}</div>`}
      <div class="tk-sub"><h3 class="tk-sub-h">${t('task.comments')}<span class="rs-n">${thread.length}</span></h3></div>
      <div class="tk-cs">
        ${thread.map(commentHTML).join('')}
        ${composerHTML(item.key, { reply: thread.length > 0 })}
      </div>
    </div>`;
  };

  /* a repaint rebuilds the arc, so its turn starts at the angle the shared clock says */
  const spinAt = () => -((performance.now() / 1000) % SPIN_S).toFixed(3);

  const itemHTML = item => {
    const open = state.open === item.key;
    const links = item.links.length;
    const talk = current.comments.filter(c => c.item === item.key).length;
    const notes = current.notes.filter(n => n.items.includes(item.key)).length;
    const next = NEXT[item.state];
    const action = t(`task.mark.${next}`);
    const stateName = t(`task.state.${item.state}`);
    return `<li class="tk-item${open ? ' is-open' : ''}${enter.items.has(item.key) ? ' is-new' : ''}" data-s="${esc(item.state)}" data-key="${esc(item.key)}">
      <div class="tk-row">
        <button type="button" class="tk-state${enter.pulse === item.key ? ' is-pulse' : ''}" data-s="${esc(item.state)}" data-step="${esc(item.key)}"
                title="${esc(action)}"
                aria-label="${esc(t('task.state.aria', { text: item.text, state: stateName, action }))}">
          <span class="tk-ring"${item.state === 'doing' ? ` style="--spin-at:${spinAt()}s"` : ''}>${{ doing: icon('ongoing'), done: icon('check'), dropped: icon('minus') }[item.state] || ''}</span>
        </button>
        <button type="button" class="tk-main" data-toggle="${esc(item.key)}"
                aria-expanded="${open}"${open ? ` aria-controls="tkp-${esc(item.key)}"` : ''}>
          <span class="tk-text">${esc(item.text)}</span>
          <span class="tk-counts">${notes
            ? `<span class="tk-count" title="${esc(t('task.notes.n', { n: notes }))}"><span aria-hidden="true">${icon('note')}${notes}</span><span class="sr-only">${esc(t('task.notes.n', { n: notes }))}</span></span>` : ''}${links
            ? `<span class="tk-count" title="${esc(t('task.links.n', { n: links }))}"><span aria-hidden="true">${icon('relation')}${links}</span><span class="sr-only">${esc(t('task.links.n', { n: links }))}</span></span>` : ''}${talk
            ? `<span class="tk-count" title="${esc(t('task.comments.n', { n: talk }))}"><span aria-hidden="true">${icon('comment')}${talk}</span><span class="sr-only">${esc(t('task.comments.n', { n: talk }))}</span></span>` : ''}</span>
          <span class="tk-chev">${icon('chevron-right')}</span>
        </button>
        <button type="button" class="icon-btn tk-menu" data-menu="${esc(item.key)}"
                title="${esc(t('task.item.menu'))}"
                aria-label="${esc(t('task.item.menuNamed', { text: item.text }))}">${icon('more')}</button>
      </div>
      ${open ? itemPanelHTML(item) : ''}
    </li>`;
  };

  const progressHTML = () => {
    const p = progressOf();
    const before = state.progress || p;
    const frac = n => (p.total ? n / p.total : 0);
    const was = n => (before.total ? n / before.total : 0);
    return `<div class="tk-prog">
      <div class="tk-prog-line">
        <span class="tk-prog-n">${p.total
          ? t('task.progress', { done: fmtInt(p.done), total: fmtInt(p.total) })
          : t('task.progress.none')}</span>
        ${p.dropped ? `<span class="chip" title="${esc(t('task.dropped.why'))}">${t('task.dropped', { n: fmtInt(p.dropped) })}</span>` : ''}
      </div>
      <div class="bar-track tk-bar" role="progressbar" aria-valuemin="0" aria-valuemax="${p.total}"
           aria-valuenow="${p.done}"
           aria-valuetext="${esc(t('task.progress.text', { done: p.done, total: p.total })
             + (p.dropped ? `, ${t('task.dropped', { n: p.dropped })}` : ''))}">
        <div class="bar-fill" data-fill="${frac(p.done)}" style="--v:${was(before.done)}"></div>
        ${p.dropped ? `<div class="tk-bar-drop" data-v0="${frac(p.done)}" data-v="${frac(p.dropped)}"
             style="--v0:${was(before.done)};--v:${was(before.dropped)}"></div>` : ''}
      </div>
    </div>`;
  };

  const closedHTML = () => {
    if (current.state === 'open') return '';
    const done = current.state === 'completed';
    return `<div class="tk-closed ${done ? 'is-completed' : 'is-cancelled'}${enter.closed ? ' is-new' : ''}" role="status">
      <span class="tk-closed-mark">${icon(done ? 'check' : 'minus')}</span>
      <span class="tk-closed-text"><b>${t(`task.state.${current.state}`)}</b>
        ${current.completed_at
          ? `<span class="tk-closed-when" title="${esc(current.completed_at)}">${esc(fmtDate(current.completed_at))}</span>` : ''}
        <span>${t(`task.closed.${current.state}`)}</span></span>
    </div>`;
  };

  const goalHTML = () => {
    if (state.goalEditing) {
      return `<div class="tk-goal is-editing">
        <textarea class="tk-box tk-goal-box" id="tkGoalBox" rows="3"
                  aria-label="${esc(t('task.goal.aria'))}">${esc(current.goal)}</textarea>
        <div class="tk-actions">
          <button type="button" class="btn btn-sm btn-solid" id="tkGoalSave">${t('common.save')}</button>
          <button type="button" class="btn btn-sm btn-ghost" id="tkGoalCancel">${t('common.cancel')}</button>
        </div>
      </div>`;
    }
    return `<div class="tk-goal">
      ${current.goal
        ? `<p class="tk-goal-text">${esc(current.goal)}</p>`
        : `<p class="tk-goal-text is-empty">${t('task.goal.empty')}</p>`}
      <button type="button" class="icon-btn" id="tkGoalEdit" title="${esc(t('task.goal.edit'))}"
              aria-label="${esc(t('task.goal.edit'))}">${icon('pencil')}</button>
    </div>`;
  };

  const addHTML = () => state.adding
    ? `<div class="tk-add is-open">
        <textarea class="tk-box" id="tkAddBox" rows="4"
                  placeholder="${esc(t('task.add.placeholder'))}"
                  aria-label="${esc(t('task.add.placeholder'))}">${esc(state.drafts.get('+items') || '')}</textarea>
        <div class="tk-actions">
          <button type="button" class="btn btn-sm btn-solid" id="tkAddSend">${t('task.add.send')}</button>
          <button type="button" class="btn btn-sm btn-ghost" id="tkAddCancel">${t('common.cancel')}</button>
        </div>
      </div>`
    : `<div class="tk-add"><button type="button" class="btn btn-sm btn-ghost" id="tkAddOpen">${t('task.add.open')}</button></div>`;

  const threadHTML = () => {
    const all = current.comments.filter(c => !c.item);
    const hidden = state.allComments ? 0 : Math.max(0, all.length - OLDER_SHOWN);
    return `<section class="tk-thread tk-card" aria-label="${esc(t('task.comments.aria'))}">
      <h3 class="tk-h">${t('task.comments')}<span class="rs-n">${all.length}</span></h3>
      ${hidden ? `<button type="button" class="rs-more tk-older" id="tkOlder">${t('task.comments.older', { n: hidden })}</button>` : ''}
      <div class="tk-cs">
        ${all.slice(hidden).map(commentHTML).join('')
          || `<div class="hint-sm tk-empty">${t('task.comments.empty')}</div>`}
        ${composerHTML('', { reply: all.length > 0 })}
      </div>
    </section>`;
  };

  function paint() {
    /* the control the caret was on, so a repaint does not drop a keyboard
       user or a half-typed comment back to the top of the page */
    const was = document.activeElement;
    const kept = host.contains(was) ? selectorOf(was) : '';
    const caret = was?.selectionStart ?? null;
    const tries = want.length ? want : [kept];
    want = [];
    const seen = state.seen;
    enter = {
      items: new Set(seen ? current.items.map(i => i.key).filter(k => !seen.items.has(k)) : []),
      comments: new Set(seen ? current.comments.map(commentKey).filter(k => !seen.comments.has(k)) : []),
      pulse: state.pulse, opened: state.opened,
      closed: Boolean(seen) && seen.open && current.state !== 'open',
    };
    state.pulse = '';
    state.opened = '';
    host.innerHTML = `<div class="tk">
      <div class="tk-head tk-card${current.state === 'open' ? '' : ' is-closed'}">
        ${closedHTML()}
        ${goalHTML()}
        ${progressHTML()}
      </div>
      <section class="tk-tnotes tk-card" aria-label="${esc(t('task.notes'))}">
        ${notesHTML(current.notes.filter(n => !n.items.length), '')}
      </section>
      <div class="tk-list tk-card">
        <ul class="tk-items">${current.items.map(itemHTML).join('')}</ul>
        ${addHTML()}
      </div>
      ${threadHTML()}
    </div>`;
    wire();
    const back = tries.filter(Boolean).map(sel => host.querySelector(sel)).find(Boolean);
    if (back) {
      back.focus({ preventScroll: true });
      if (caret !== null && back.setSelectionRange) back.setSelectionRange(caret, caret);
    }
    /* the fill is drawn at its old value and moves on the next frame, so the
       bar transitions */
    const fill = host.querySelector('.bar-fill');
    const drop = host.querySelector('.tk-bar-drop');
    if (fill) requestAnimationFrame(() => requestAnimationFrame(() => {
      fill.style.setProperty('--v', fill.dataset.fill);
      drop?.style.setProperty('--v0', drop.dataset.v0);
      drop?.style.setProperty('--v', drop.dataset.v);
    }));
    state.progress = progressOf();
    state.seen = {
      items: new Set(current.items.map(i => i.key)),
      comments: new Set(current.comments.map(commentKey)),
      open: current.state === 'open',
    };
  }

  /* ── wiring ── */

  function wire() {
    const q = s => host.querySelector(s);
    const all = s => host.querySelectorAll(s);

    all('[data-step]').forEach(b => b.addEventListener('click', () => {
      const item = current.items.find(i => i.key === b.dataset.step);
      if (item) setItem(item.key, NEXT[item.state]);
    }));

    all('[data-toggle]').forEach(b => b.addEventListener('click', () => {
      state.open = state.open === b.dataset.toggle ? '' : b.dataset.toggle;
      state.opened = state.open;
      paint();
    }));

    all('[data-menu]').forEach(b => b.addEventListener('click', () => {
      const item = current.items.find(i => i.key === b.dataset.menu);
      if (!item) return;
      openDropMenu(b, [
        ...STATES.filter(s => s !== item.state).map(s => ({
          label: t(`task.mark.${s}`), danger: s === 'dropped',
          run: () => { focusAfter(attr('data-menu', item.key)); setItem(item.key, s); },
        })),
        { sep: true },
        { label: t('task.item.delete'), danger: true, run: () => deleteItem(item.key),
          note: current.items.length < 2 ? t('task.item.deleteLast') : '' },
      ], { align: 'right' });
    }));

    all('[data-link]').forEach(b => b.addEventListener('click', async () => {
      const item = current.items.find(i => i.key === b.dataset.link);
      const chosen = await pickMemories({
        title: t('task.link.pickTitle'), exclude: uid, linked: item.links.map(l => l.uid),
        okLabel: t('task.link.add'),
      });
      if (!chosen?.uids.length) return;
      focusAfter(attr('data-link', item.key));
      await write('link', { item: item.key, target: chosen.uids }, { errKey: 'task.err.link' });
    }));
    /* the control that goes is replaced by the next link's, else the
       previous one's, else the item's "add link" */
    all('[data-unlink]').forEach(b => b.addEventListener('click', () => {
      const item = b.dataset.item;
      const row = b.closest('.tk-link');
      const near = [row?.nextElementSibling, row?.previousElementSibling]
        .map(r => r?.querySelector('[data-unlink]')).filter(Boolean)
        .map(selectorOf);
      focusAfter(...near, attr('data-link', item));
      write('link', { item, target: b.dataset.unlink },
            { method: 'DELETE', errKey: 'task.err.link' });
    }));

    /* a link opens the record it names */
    all('[data-open]').forEach(b => b.addEventListener('click',
      () => go('memory', { uid: b.dataset.open })));

    all('[data-draft]').forEach(box => {
      const scope = box.dataset.draft;
      const send = host.querySelector(`[data-send="${CSS.escape(scope)}"]`);
      const post = async () => {
        const body = box.value.trim();
        if (!body) return;
        focusAfter(attr('data-draft', scope));
        await write('comment', { body, item: scope },
                    { errKey: 'task.err.comment', onOk: s => s.drafts.delete(scope) });
      };
      box.addEventListener('input', () => {
        state.drafts.set(scope, box.value);
        send.disabled = !box.value.trim();
      });
      box.addEventListener('keydown', e => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); post(); }
      });
      send.addEventListener('click', post);
    });

    q('#tkOlder')?.addEventListener('click', () => { state.allComments = true; paint(); });

    /* ── task notes ── */
    const openLink = target => go('memory', { uid: target });
    all('.tk-note .rf-body').forEach(body => wireRich(body, { open: openLink, copy: copyCode }));
    highlightIn(host).catch(() => {});

    all('[data-note-open]').forEach(b => b.addEventListener('click', () => {
      const id = b.dataset.noteOpen;
      if (b.getAttribute('aria-expanded') === 'true') { state.notesOpen.delete(id); state.notesShut.add(id); }
      else { state.notesShut.delete(id); state.notesOpen.add(id); }
      paint();
    }));
    const editNote = which => {
      const note = current.notes.find(n => String(n.id) === which);
      const scope = which.startsWith('new:') ? which.slice(4) : '';
      state.noteEdit = which;
      state.noteDraft = { title: note?.title || '', body: note?.body || '',
                          items: note ? [...note.items] : scope ? [scope] : [] };
      focusAfter(note ? '[data-note-field="body"]' : '[data-note-field="title"]');
      paint();
    };
    all('[data-note-add]').forEach(b => b.addEventListener('click', () => editNote(`new:${b.dataset.noteAdd}`)));
    all('[data-note-edit]').forEach(b => b.addEventListener('click', () => editNote(b.dataset.noteEdit)));

    const form = q('[data-note-form]');
    if (form) {
      const d = state.noteDraft;
      const target = form.dataset.noteForm;
      const fresh = target.startsWith('new:');
      const back = fresh ? attr('data-note-add', target.slice(4)) : attr('data-note-edit', target);
      const title = form.querySelector('[data-note-field="title"]');
      const body = form.querySelector('[data-note-field="body"]');
      const save = form.querySelector('[data-note-save]');
      const count = form.querySelector('[data-note-count]');
      const ready = () => { save.disabled = !d.title.trim() || !d.body.trim(); };
      const leave = () => { state.noteEdit = ''; state.noteDraft = null; focusAfter(back); paint(); };
      const submit = async () => {
        if (save.disabled) return;
        const payload = { title: d.title.trim(), body: d.body.trim(),
                          items: current.items.map(i => i.key).filter(k => d.items.includes(k)) };
        if (!fresh) payload.id = Number(target);
        focusAfter(back);
        await write('note', payload, { errKey: 'task.err.note', onOk: s => {
          s.noteEdit = '';
          s.noteDraft = null;
          if (!fresh) { s.notesShut.delete(target); s.notesOpen.add(target); }
        } });
      };
      ready();
      title.addEventListener('input', () => { d.title = title.value; ready(); });
      body.addEventListener('input', () => {
        d.body = body.value;
        ready();
        count.textContent = t('dr.sections.count', { n: d.body.length, max: NOTE_MAX });
      });
      form.querySelectorAll('[data-note-scope]').forEach(box => box.addEventListener('change', () => {
        const key = box.dataset.noteScope;
        d.items = box.checked ? [...d.items.filter(k => k !== key), key] : d.items.filter(k => k !== key);
        form.querySelector('[data-note-scope-sum]').textContent = scopeSum(d.items.length);
      }));
      form.addEventListener('keydown', e => {
        if (e.key === 'Escape') { e.preventDefault(); leave(); }
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); submit(); }
      });
      save.addEventListener('click', submit);
      form.querySelector('[data-note-cancel]').addEventListener('click', leave);
      form.querySelector('[data-note-del]')?.addEventListener('click', async () => {
        const note = current.notes.find(n => String(n.id) === target);
        if (!note || busy) return;
        const ok = await confirmModal({
          title: t('task.note.delete'), body: t('task.note.delete.body', { title: esc(note.title) }),
          okLabel: t('task.note.delete'), danger: true });
        if (!ok) return;
        await write('note', { id: note.id }, { method: 'DELETE', errKey: 'task.err.note',
          onOk: s => { s.noteEdit = ''; s.noteDraft = null; } });
      });
    }

    /* ── the goal ── */
    q('#tkGoalEdit')?.addEventListener('click', () => {
      state.goalEditing = true;
      paint();
      const box = q('#tkGoalBox');
      box.focus();
      box.setSelectionRange(box.value.length, box.value.length);
    });
    const goalBox = q('#tkGoalBox');
    if (goalBox) {
      const leave = () => { state.goalEditing = false; focusAfter('#tkGoalEdit'); paint(); };
      const save = async () => {
        const goal = goalBox.value.trim();
        if (goal === current.goal) { leave(); return; }
        await write('goal', { goal }, { onOk: s => { s.goalEditing = false; focusAfter('#tkGoalEdit'); } });
      };
      q('#tkGoalSave').addEventListener('click', save);
      q('#tkGoalCancel').addEventListener('click', leave);
      goalBox.addEventListener('keydown', e => {
        if (e.key === 'Escape') leave();
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); save(); }
      });
    }

    /* ── more items ── */
    q('#tkAddOpen')?.addEventListener('click', () => {
      state.adding = true;
      paint();
      q('#tkAddBox').focus();
    });
    const addBox = q('#tkAddBox');
    if (addBox) {
      const send = async () => {
        if (!addBox.value.trim()) return;
        await write('items', { items: addBox.value }, {
          errKey: 'task.err.items',
          onOk: s => { s.adding = false; s.drafts.delete('+items'); focusAfter('#tkAddOpen'); },
        });
      };
      addBox.addEventListener('input', () => state.drafts.set('+items', addBox.value));
      q('#tkAddSend').addEventListener('click', send);
      const close = () => { state.adding = false; focusAfter('#tkAddOpen'); paint(); };
      q('#tkAddCancel').addEventListener('click', close);
      addBox.addEventListener('keydown', e => {
        if (e.key === 'Escape') close();
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); send(); }
      });
    }
  }

  paint();
}
