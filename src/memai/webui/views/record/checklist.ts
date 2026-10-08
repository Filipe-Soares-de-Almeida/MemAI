/* A task checklist's working state and its write path: every accepted write replaces the task with
   the {task, status} the server answers, and names the control focus lands on afterwards. */

import { nextTick, onBeforeUnmount, reactive, ref, shallowRef } from 'vue';
import { esc } from '../../core/dom.ts';
import { confirmModal, failed, toast } from '../../core/ui.js';
import { pickMemories } from '../../core/link-picker.js';
import { parseHash, refreshBehind } from '../../core/router.ts';
import { t } from '../../i18n.ts';
import type { I18nKey } from '../../i18n.ts';
import { TASK } from '../../contract.ts';
import * as client from '../../api/client.ts';
import type { TaskAnswer, TaskItem, TaskRecord } from '../../api/types.ts';

/* One click on an item's mark moves it along todo, doing, done; done or dropped go back to todo. */
export const NEXT: Record<string, string> = { todo: 'doing', doing: 'done', done: 'todo', dropped: 'todo' };
export const STATES: readonly string[] = TASK.ITEM_STATES;
export const OLDER_SHOWN = 3;
/* how long a deleted item takes to leave before the list drops it */
const LEAVE_MS = 160;
/* one lap of the in-progress arc, as --spin in admin.css */
const SPIN_S = 3.2;

/* the phase a new arc starts at, so every arc turns on one shared clock */
export const spinAt = (): number => -((performance.now() / 1000) % SPIN_S).toFixed(3);

/* the opening of a body as plain words: markup that only means something when drawn goes */
export const notePeek = (text: string): string => text
  .replace(/```[^\n]*|={2,}|\|?\s*:?-{2,}:?\s*(?=\||$)|[|`*]/gm, ' ')
  .replace(/\s+/g, ' ').trim().slice(0, 180);

export function progressOf(task: TaskRecord): { total: number; done: number; dropped: number; pct: number } {
  const count = (s: string) => task.items.filter(i => i.state === s).length;
  const total = task.items.length;
  const done = count('done');
  const dropped = count('dropped');
  const live = total - dropped;
  return { total, done, dropped, pct: live > 0 ? Math.floor((done * 100) / live) : 0 };
}

export const attr = (name: string, value: string | number): string => `[${name}="${CSS.escape(String(value))}"]`;

export interface NoteDraft { title: string; body: string; items: string[] }
interface Hooks { onStatus: (status: string) => void; onWrite: (answer: TaskAnswer) => void }
type Call = (uid: string, body: Record<string, unknown>) => Promise<TaskAnswer>;

export function useChecklist(uid: string, task: TaskRecord, status: string, hooks: Hooks) {
  const current = shallowRef(task);
  let currentStatus = status;
  const ui = reactive({
    open: '', goalEditing: false, adding: false, allComments: false,
    /* the note being edited by id, or `new:<item key>` */
    noteEdit: '', noteDraft: null as NoteDraft | null,
    /* notes opened or shut by hand; a list of one note starts open */
    notesOpen: new Set<string>(), notesShut: new Set<string>(),
    drafts: new Map<string, string>(), leaving: '',
  });
  /* what the last write brought in, so only that animates */
  const enter = reactive({ items: new Set<string>(), comments: new Set<number>(), pulse: '', opened: '',
                           closed: false });
  const host = ref<HTMLElement | null>(null);
  let alive = true;
  let busy = false;
  /* where focus goes once the next change is drawn, first match wins */
  let want: string[] = [];
  onBeforeUnmount(() => { alive = false; });

  const focusAfter = (...selectors: string[]) => { want = selectors; };
  function land() {
    const tries = want;
    want = [];
    if (tries.length) nextTick(() => {
      tries.map(s => host.value?.querySelector<HTMLElement>(s)).find(Boolean)?.focus({ preventScroll: true });
    });
  }

  function apply(res: TaskAnswer) {
    if (!res.task) return;
    const was = currentStatus;
    const before = current.value;
    const items = new Set(before.items.map(i => i.key));
    const comments = new Set(before.comments.map(c => c.id));
    enter.items = new Set(res.task.items.map(i => i.key).filter(k => !items.has(k)));
    enter.comments = new Set(res.task.comments.map(c => c.id).filter(id => !comments.has(id)));
    enter.closed = before.state === 'open' && res.task.state !== 'open';
    current.value = res.task;
    currentStatus = res.status;
    land();
    if (alive && was !== currentStatus) hooks.onStatus(currentStatus);
  }

  /* the task as the record read it again; nothing in it is new to this view */
  function sync(task: TaskRecord) {
    if (task === current.value) return;
    current.value = task;
  }

  /* One write at a time, so a second click cannot act on a stale state; `onOk` runs, awaited, once
     the write is accepted and before the answer is drawn. */
  async function write(call: Call, body: Record<string, unknown>,
                       { errKey = 'task.err.save', onOk }: { errKey?: I18nKey; onOk?: () => unknown } = {}) {
    if (busy) { want = []; return null; }
    busy = true;
    try {
      const res = await call(uid, body);
      enter.pulse = '';
      await onOk?.();
      apply(res);
      if (alive) hooks.onWrite(res);
      else {
        const { name, params } = parseHash();
        if (name === 'memory' && params.get('uid') === uid) refreshBehind();
      }
      return res;
    } catch (err) {
      want = [];
      failed(errKey, err);
      return null;
    } finally {
      busy = false;
    }
  }

  /* A write that closes the task says so, with an Undo that puts the item back as it was. */
  async function setItem(key: string, next: string, { quiet = false } = {}) {
    const before = current.value.items.find(i => i.key === key)?.state;
    const wasOpen = current.value.state === 'open';
    const res = await write(client.tasks.item, { item: key, state: next }, { onOk: () => { enter.pulse = key; } });
    if (!res?.task || quiet) return;
    if (wasOpen && res.task.state !== 'open') {
      toast(t(`task.toast.${res.task.state}` as I18nKey), 'ok', {
        action: { label: t('common.undo'), run: () => {
          if (!before) return;
          focusAfter(attr('data-step', key));
          void setItem(key, before, { quiet: true });
        } },
      });
    } else if (!wasOpen && res.task.state === 'open') {
      toast(t('task.toast.reopened'), 'ok');
    }
  }

  /* Asks first, plays the row out, then focuses the next item's mark, else the previous one's, else
     the add control. */
  async function deleteItem(key: string) {
    const item = current.value.items.find(i => i.key === key);
    if (!item || busy) return;
    const ok = await confirmModal({
      title: t('task.delete.title'), body: t('task.delete.body', { text: esc(item.text) }),
      okLabel: t('task.item.delete'), danger: true });
    if (!ok) return;
    const items = current.value.items;
    const at = items.findIndex(i => i.key === key);
    if (at < 0) return;
    const wasOpen = current.value.state === 'open';
    focusAfter(...[items[at + 1], items[at - 1]].filter(Boolean).map(i => attr('data-step', i.key)), '#tkAddOpen');
    const res = await write(client.tasks.deleteItem, { item: key }, {
      errKey: 'task.err.delete',
      onOk: async () => {
        if (ui.open === key) ui.open = '';
        ui.drafts.delete(key);
        ui.leaving = key;
        await new Promise(done => setTimeout(done, LEAVE_MS));
      },
    });
    ui.leaving = '';
    if (!res?.task) return;
    toast(t(wasOpen && res.task.state !== 'open' ? `task.toast.${res.task.state}` as I18nKey : 'task.toast.deleted'),
          'ok');
  }

  function toggle(key: string) {
    ui.open = ui.open === key ? '' : key;
    enter.opened = ui.open;
  }

  async function link(item: TaskItem) {
    const chosen = await pickMemories({
      title: t('task.link.pickTitle'), exclude: uid, linked: item.links.map(l => l.uid),
      okLabel: t('task.link.add'),
    });
    if (!chosen?.uids.length) return;
    focusAfter(attr('data-link', item.key));
    await write(client.tasks.link, { item: item.key, target: chosen.uids }, { errKey: 'task.err.link' });
  }

  /* the control that goes is replaced by the next link's, else the previous one's, else "add link" */
  function unlink(item: TaskItem, target: string) {
    const at = item.links.findIndex(l => l.uid === target);
    const near = [item.links[at + 1], item.links[at - 1]].filter(Boolean)
      .map(l => `${attr('data-unlink', l.uid)}${attr('data-item', item.key)}`);
    focusAfter(...near, attr('data-link', item.key));
    void write(client.tasks.unlink, { item: item.key, target }, { errKey: 'task.err.link' });
  }

  async function post(scope: string) {
    const body = (ui.drafts.get(scope) || '').trim();
    if (!body) return;
    focusAfter(attr('data-draft', scope));
    await write(client.tasks.comment, { body, item: scope },
                { errKey: 'task.err.comment', onOk: () => ui.drafts.delete(scope) });
  }

  /* ── notes ── */

  const noteOpen = (id: number, count: number) => ui.notesOpen.has(String(id))
    || (count === 1 && !ui.notesShut.has(String(id)));

  function toggleNote(id: number, open: boolean) {
    const key = String(id);
    if (open) { ui.notesOpen.delete(key); ui.notesShut.add(key); }
    else { ui.notesShut.delete(key); ui.notesOpen.add(key); }
  }

  function editNote(which: string) {
    const note = current.value.notes.find(n => String(n.id) === which);
    const scope = which.startsWith('new:') ? which.slice(4) : '';
    ui.noteEdit = which;
    ui.noteDraft = { title: note?.title || '', body: note?.body || '',
                     items: note ? [...note.items] : scope ? [scope] : [] };
    focusAfter(note ? '[data-note-field="body"]' : '[data-note-field="title"]');
    land();
  }

  const noteBack = (which: string) =>
    (which.startsWith('new:') ? attr('data-note-add', which.slice(4)) : attr('data-note-edit', which));

  function leaveNote() {
    const back = noteBack(ui.noteEdit);
    ui.noteEdit = '';
    ui.noteDraft = null;
    focusAfter(back);
    land();
  }

  function scopeNote(key: string, on: boolean) {
    const d = ui.noteDraft;
    if (!d) return;
    d.items = on ? [...d.items.filter(k => k !== key), key] : d.items.filter(k => k !== key);
  }

  const noteReady = () => Boolean(ui.noteDraft?.title.trim() && ui.noteDraft.body.trim());

  async function submitNote() {
    const d = ui.noteDraft;
    if (!d || !noteReady()) return;
    const target = ui.noteEdit;
    const fresh = target.startsWith('new:');
    const payload: Record<string, unknown> = {
      title: d.title.trim(), body: d.body.trim(),
      items: current.value.items.map(i => i.key).filter(k => d.items.includes(k)),
    };
    if (!fresh) payload.id = Number(target);
    focusAfter(noteBack(target));
    await write(client.tasks.note, payload, { errKey: 'task.err.note', onOk: () => {
      ui.noteEdit = '';
      ui.noteDraft = null;
      if (!fresh) { ui.notesShut.delete(target); ui.notesOpen.add(target); }
    } });
  }

  async function deleteNote() {
    const note = current.value.notes.find(n => String(n.id) === ui.noteEdit);
    if (!note || busy) return;
    const ok = await confirmModal({
      title: t('task.note.delete'), body: t('task.note.delete.body', { title: esc(note.title) }),
      okLabel: t('task.note.delete'), danger: true });
    if (!ok) return;
    await write(client.tasks.deleteNote, { id: note.id }, { errKey: 'task.err.note',
      onOk: () => { ui.noteEdit = ''; ui.noteDraft = null; } });
  }

  /* ── the goal and new items ── */

  function editGoal() { ui.goalEditing = true; }
  function leaveGoal() { ui.goalEditing = false; focusAfter('#tkGoalEdit'); land(); }

  async function saveGoal(text: string) {
    const goal = text.trim();
    if (goal === current.value.goal) { leaveGoal(); return; }
    await write(client.tasks.goal, { goal }, { onOk: () => { ui.goalEditing = false; focusAfter('#tkGoalEdit'); } });
  }

  function openAdd() { ui.adding = true; focusAfter('#tkAddBox'); land(); }
  function closeAdd() { ui.adding = false; focusAfter('#tkAddOpen'); land(); }

  async function sendAdd() {
    if (!(ui.drafts.get('+items') || '').trim()) return;
    await write(client.tasks.items, { items: ui.drafts.get('+items') }, {
      errKey: 'task.err.items',
      onOk: () => { ui.adding = false; ui.drafts.delete('+items'); focusAfter('#tkAddOpen'); },
    });
  }

  return { current, ui, enter, host, sync, setItem, deleteItem, toggle, link, unlink, post,
           noteOpen, toggleNote, editNote, leaveNote, scopeNote, noteReady, submitNote, deleteNote,
           editGoal, leaveGoal, saveGoal, openAdd, closeAdd, sendAdd };
}

export type Checklist = ReturnType<typeof useChecklist>;
