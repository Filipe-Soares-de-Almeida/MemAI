/* A task's checklist in the record view; every write repaints it from the {task, status} the server answers.
   One item's links and comments are open at a time, and `onStatus` tells the record when the task closes. */

import { esc, fmtAgo, fmtDate, fmtInt } from '../core/dom.js';
import { api, seg } from '../core/api.js';
import { icon } from '../core/icons.js';
import { toast, failed, openDropMenu } from '../core/ui.js';
import { pickMemories } from '../core/link-picker.js';
import { go, parseHash, refreshBehind } from '../core/router.js';
import { t } from '../i18n.js';

const STATES = ['todo', 'doing', 'done', 'dropped'];

/* One click on an item's mark moves it along todo, doing, done; a done or a
   dropped item goes back to todo. */
const NEXT = { todo: 'doing', doing: 'done', done: 'todo', dropped: 'todo' };

const OLDER_SHOWN = 3;

/* What survives a repaint, per task: the open item, the editors that are open,
   and the text typed but not yet sent. */
let ui = null;
const fresh = uid => ({
  uid, open: '', goalEditing: false, adding: false, allComments: false,
  drafts: new Map(), progress: null,
});

/* A selector that finds the same control again after a repaint. */
const selectorOf = el => {
  if (el.id) return `#${CSS.escape(el.id)}`;
  if (el.hasAttribute('data-unlink'))
    return `[data-unlink="${CSS.escape(el.dataset.unlink)}"][data-item="${CSS.escape(el.dataset.item)}"]`;
  for (const a of ['data-step', 'data-toggle', 'data-menu', 'data-draft', 'data-link'])
    if (el.hasAttribute(a)) return `[${a}="${CSS.escape(el.getAttribute(a))}"]`;
  return '';
};
const attr = (name, value) => `[${name}="${CSS.escape(value)}"]`;

export function mountTask(host, { uid, task, status }, { onStatus } = {}) {
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

  /* ── the write path ── */

  const apply = res => {
    const was = currentStatus;
    current = res.task;
    currentStatus = res.status;
    if (alive()) paint();
    if (alive() && was !== currentStatus) onStatus?.(currentStatus);
  };

  /* One write at a time, so a second click cannot read a stale state. `onOk`
     runs on acceptance, before the repaint, so what it clears is not redrawn.
     A write that lands after the host is gone repaints the task's route. */
  const write = async (path, body, { method = 'POST', errKey = 'task.err.save', onOk } = {}) => {
    if (busy) { want = []; return null; }
    busy = true;
    try {
      const res = await api(`/api/tasks/${seg(uid)}/${path}`, { method, body });
      onOk?.(state);
      apply(res);
      if (!alive()) refreshIfShown();
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
    const res = await write('item', { item: key, state: next });
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

  /* ── painting ── */

  const progressOf = () => {
    const total = current.items.length;
    const count = s => current.items.filter(i => i.state === s).length;
    return { total, done: count('done'), dropped: count('dropped') };
  };

  const commentHTML = c => `<div class="tk-c ${c.author === 'person' ? 'is-person' : 'is-agent'}">
    <div class="tk-c-head">
      <span class="tk-c-who">${esc(t(c.author === 'person' ? 'task.author.person' : 'task.author.agent'))}</span>
      ${c.session && c.author !== 'person'
        ? `<span class="tk-c-session" title="${esc(c.session)}">${esc(c.session.slice(0, 14))}</span>` : ''}
      <time class="tk-c-when" datetime="${esc(c.created_at)}" title="${esc(c.created_at)}">${fmtAgo(c.created_at)}</time>
    </div>
    <p class="tk-c-body">${esc(c.body)}</p>
  </div>`;

  const composerHTML = (scope, { rows = 2 } = {}) => `<div class="tk-compose">
    <textarea class="tk-box" rows="${rows}" data-draft="${esc(scope)}"
              placeholder="${esc(t('task.comment.placeholder'))}"
              aria-label="${esc(t('task.comment.placeholder'))}">${esc(state.drafts.get(scope) || '')}</textarea>
    <button type="button" class="btn btn-sm" data-send="${esc(scope)}" ${(state.drafts.get(scope) || '').trim() ? '' : 'disabled'}>${t('task.comment.send')}</button>
  </div>`;

  const itemPanelHTML = item => {
    const thread = current.comments.filter(c => c.item === item.key);
    return `<div class="tk-panel" id="tkp-${esc(item.key)}" role="group"
                 aria-label="${esc(t('task.panel.aria', { text: item.text }))}">
      <div class="tk-sub">
        <h3 class="tk-sub-h">${t('task.links')}<span class="rs-n">${item.links.length}</span></h3>
        <button type="button" class="rs-more" data-link="${esc(item.key)}">${t('task.link.add')}</button>
      </div>
      ${item.links.length ? `<div class="tk-links">${item.links.map(l => `<div class="tk-link">
          <button type="button" class="snippet tk-link-open" data-open="${esc(l.uid)}"
                  title="${esc(l.uid)}">${esc(l.title || l.uid)}</button>
          <button type="button" class="icon-btn danger" data-unlink="${esc(l.uid)}"
                  data-item="${esc(item.key)}" title="${esc(t('task.link.remove'))}"
                  aria-label="${esc(t('task.link.removeNamed', { title: l.title || l.uid }))}">${icon('close')}</button>
        </div>`).join('')}</div>`
        : `<div class="hint-sm">${t('task.links.empty')}</div>`}
      <div class="tk-sub"><h3 class="tk-sub-h">${t('task.comments')}<span class="rs-n">${thread.length}</span></h3></div>
      ${thread.map(commentHTML).join('')}
      ${composerHTML(item.key)}
    </div>`;
  };

  const itemHTML = item => {
    const open = state.open === item.key;
    const links = item.links.length;
    const talk = current.comments.filter(c => c.item === item.key).length;
    const next = NEXT[item.state];
    const action = t(`task.mark.${next}`);
    const stateName = t(`task.state.${item.state}`);
    return `<li class="tk-item${open ? ' is-open' : ''}" data-s="${item.state}" data-key="${esc(item.key)}">
      <div class="tk-row">
        <button type="button" class="tk-state" data-s="${item.state}" data-step="${esc(item.key)}"
                title="${esc(action)}"
                aria-label="${esc(t('task.state.aria', { text: item.text, state: stateName, action }))}">
          <span class="tk-ring">${item.state === 'done' ? icon('check')
            : item.state === 'dropped' ? icon('minus') : ''}</span>
        </button>
        <button type="button" class="tk-main" data-toggle="${esc(item.key)}"
                aria-expanded="${open}"${open ? ` aria-controls="tkp-${esc(item.key)}"` : ''}>
          <span class="tk-text">${esc(item.text)}</span>
          <span class="tk-counts">${links
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
    const shown = p.total ? before.done / p.total : 0;
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
        <div class="bar-fill" data-fill="${frac(p.done)}" style="--v:${shown}"></div>
        ${p.dropped ? `<div class="tk-bar-drop" style="--v0:${frac(p.done)};--v:${frac(p.dropped)}"></div>` : ''}
      </div>
    </div>`;
  };

  const closedHTML = () => {
    if (current.state === 'open') return '';
    const done = current.state === 'completed';
    return `<div class="tk-closed ${done ? 'is-completed' : 'is-cancelled'}" role="status">
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
      ${all.slice(hidden).map(commentHTML).join('')
        || `<div class="hint-sm">${t('task.comments.empty')}</div>`}
      ${composerHTML('')}
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
    host.innerHTML = `<div class="tk">
      <div class="tk-head tk-card${current.state === 'open' ? '' : ' is-closed'}">
        ${closedHTML()}
        ${goalHTML()}
        ${progressHTML()}
      </div>
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
    if (fill) requestAnimationFrame(() => requestAnimationFrame(() => {
      fill.style.setProperty('--v', fill.dataset.fill);
    }));
    state.progress = progressOf();
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
      paint();
    }));

    all('[data-menu]').forEach(b => b.addEventListener('click', () => {
      const item = current.items.find(i => i.key === b.dataset.menu);
      if (!item) return;
      openDropMenu(b, STATES.filter(s => s !== item.state).map(s => ({
        label: t(`task.mark.${s}`), danger: s === 'dropped',
        run: () => { focusAfter(attr('data-menu', item.key)); setItem(item.key, s); },
      })), { align: 'right' });
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
