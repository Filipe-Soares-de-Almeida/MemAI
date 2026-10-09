/* A task checklist's working state and its write path: every accepted write replaces the task with
   the {task, status} the server answers, and names the control focus lands on afterwards. */

import { nextTick, onBeforeUnmount, reactive, ref, shallowRef } from 'vue';
import { esc } from '../../core/dom.ts';
import { sectionLabel } from '../../core/shared.js';
import { confirmModal, failed, toast } from '../../core/ui.js';
import { pickMemories } from '../../core/link-picker.js';
import { parseHash, refreshBehind } from '../../core/router.ts';
import { I18N, t } from '../../i18n.ts';
import type { I18nKey } from '../../i18n.ts';
import { ITEM_MARK } from '../../core/vocab.js';
import { TASK } from '../../contract.ts';
import * as client from '../../api/client.ts';
import type { TaskAnswer, TaskDepend, TaskItem, TaskRecord } from '../../api/types.ts';

/* One click on an item's mark moves it along todo, doing, done; done or dropped go back to todo. */
export const NEXT: Record<string, string> = { todo: 'doing', doing: 'done', done: 'todo', dropped: 'todo' };
export const STATES: readonly string[] = TASK.ITEM_STATES;
export const MARK: Record<string, string> = ITEM_MARK;
export const OLDER_SHOWN = 3;
/* how long a deleted item takes to leave before the list drops it */
const LEAVE_MS = 160;
/* how long a row stays highlighted after a reference to it is followed */
const FLASH_MS = 1200;
/* one lap of the in-progress arc, as --spin in admin.css */
const SPIN_S = 3.2;

/* the phase a new arc starts at, so every arc turns on one shared clock */
export const spinAt = (): number => -((performance.now() / 1000) % SPIN_S).toFixed(3);

/* the opening of a body as plain words: markup that only means something when drawn goes */
export const notePeek = (text: string): string => text
  .replace(/```[^\n]*|={2,}|\|?\s*:?-{2,}:?\s*(?=\||$)|[|`*]/gm, ' ')
  .replace(/\s+/g, ' ').trim().slice(0, 180);

/* total is the items not dropped, so the count, the percentage and the bar read the same */
export function progressOf(task: TaskRecord): { total: number; done: number; pct: number } {
  const count = (s: string) => task.items.filter(i => i.state === s).length;
  const done = count('done');
  const total = task.items.length - count('dropped');
  return { total, done, pct: total > 0 ? Math.floor((done * 100) / total) : 0 };
}

export const attr = (name: string, value: string | number): string => `[${name}="${CSS.escape(String(value))}"]`;

/* the key an item's controls and panels carry: its id, as text */
export const keyOf = (item: { id: number }): string => String(item.id);

export interface BriefField { key: string; label: string; optional?: boolean }
export const BRIEF: BriefField[] = TASK.BRIEF;

/* a brief's present fields in template order, each with its label as the dashboard names it */
export const briefOf = (brief: Record<string, string>) => BRIEF.filter(f => brief[f.key])
  .map(f => ({ key: f.key, raw: f.label, label: sectionLabel('task_note', f), text: brief[f.key] }));

/* mirrors memai.sections.render_spec: a required field always written, an empty optional one left out */
export const renderBrief = (brief: Record<string, string>): string => BRIEF
  .filter(f => !f.optional || (brief[f.key] || '').trim())
  .map(f => `${f.label}: ${(brief[f.key] || '').trim()}`).join('\n');

const filled = (brief: Record<string, string>) => BRIEF.filter(f => (brief[f.key] || '').trim());

/* a draft leaving its items: extra info alone is the text it came from, anything more keeps its labels */
const bodyOf = (brief: Record<string, string>): string => {
  const have = filled(brief);
  if (have.length === 1 && have[0].key === 'extra_info') return brief.extra_info.trim();
  return have.map(f => `${f.label}: ${brief[f.key].trim()}`).join('\n');
};

/* mirrors memai.sections.write_depends */
export function writeDepends(deps: TaskDepend[]): string {
  if (!deps.length) return 'none';
  return deps.map(d => {
    const head = d.item != null ? `[[#${d.item}]]`
      : `deleted "${d.text.replace(/"/g, "'").split(/\s+/).filter(Boolean).join(' ')}"`;
    const why = d.reason.trim();
    return why ? `${head} (${why})` : head;
  }).join(', ');
}

export interface NoteDraft {
  title: string; body: string; items: number[]; brief: Record<string, string>;
  /* the brief's DEPENDS ON as chips, and the field's text when it never read as one */
  depends: TaskDepend[]; legacy: string;
}

/* the brief a draft sends: its fields, DEPENDS ON written from its chips, and a field written before the
   chips kept under EXTRA INFO */
const briefToSend = (d: NoteDraft): Record<string, string> => ({
  ...d.brief,
  ...(d.legacy ? { extra_info: [(d.brief.extra_info || '').trim(), t('task.depends.carried', { text: d.legacy })]
    .filter(Boolean).join('\n') } : {}),
  depends_on: writeDepends(d.depends),
});

/* the brief a draft leaving its items turns into text: DEPENDS ON only beside another field, or when it names something */
const briefOfDraft = (d: NoteDraft): Record<string, string> => (d.depends.length
  || BRIEF.some(f => !f.optional && f.key !== 'depends_on' && (d.brief[f.key] || '').trim()) ? briefToSend(d) : { ...d.brief });

/* The text is held as brief fields on items and as a body off them. The fields stay while off, so an
   untouched body restores them on re-tick; an edited one goes under EXTRA INFO. */
function setScope(d: NoteDraft, items: number[]) {
  const had = d.items.length > 0;
  d.items = items;
  if (had && !items.length) {
    d.body = bodyOf(briefOfDraft(d));
  } else if (!had && items.length && d.body.trim() !== bodyOf(briefOfDraft(d))) {
    d.brief = d.body.trim() ? { extra_info: d.body } : {};
  }
}

interface Hooks { onStatus: (status: string) => void; onWrite: (answer: TaskAnswer) => void }
type Call = (uid: string, body: Record<string, unknown>) => Promise<TaskAnswer>;

export function useChecklist(uid: string, task: TaskRecord, status: string, hooks: Hooks) {
  const current = shallowRef(task);
  let currentStatus = status;
  const ui = reactive({
    open: '', goalEditing: false, adding: false, allComments: false,
    /* the key of the item whose text is being edited, its id as text */
    renaming: '',
    /* the note being edited by id, or `new:<item key>` */
    noteEdit: '', noteDraft: null as NoteDraft | null,
    /* notes opened or shut by hand; a list of one note starts open */
    notesOpen: new Set<string>(), notesShut: new Set<string>(),
    drafts: new Map<string, string>(), leaving: '',
  });
  /* what the last write brought in, so only that animates */
  const enter = reactive({ items: new Set<string>(), comments: new Set<number>(), pulse: '', opened: '',
                           closed: false, flash: '' });
  const host = ref<HTMLElement | null>(null);
  let alive = true;
  let busy = false;
  /* where focus goes once the next change is drawn, first match wins */
  let want: string[] = [];
  let flashTimer: ReturnType<typeof setTimeout> | undefined;
  onBeforeUnmount(() => { alive = false; clearTimeout(flashTimer); });

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
    const items = new Set(before.items.map(keyOf));
    const comments = new Set(before.comments.map(c => c.id));
    enter.items = new Set(res.task.items.map(keyOf).filter(k => !items.has(k)));
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
    const before = current.value.items.find(i => keyOf(i) === key)?.state;
    const wasOpen = current.value.state === 'open';
    const res = await write(client.tasks.item, { item: Number(key), state: next }, { onOk: () => { enter.pulse = key; } });
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

  /* What a view holds for an item that is gone goes with it: its open panel, its rename box, its
     drafts and a note being written on it alone. */
  function forget(key: string) {
    if (ui.open === key) ui.open = '';
    if (ui.renaming === key) ui.renaming = '';
    ui.drafts.delete(key);
    if (ui.noteEdit === `new:${key}`) { ui.noteEdit = ''; ui.noteDraft = null; }
    if (ui.noteDraft) {
      const id = Number(key);
      setScope(ui.noteDraft, ui.noteDraft.items.filter(i => i !== id));
      ui.noteDraft.depends = ui.noteDraft.depends.filter(d => d.item !== id);
    }
  }

  /* Asks first, plays the row out, then focuses the next item's mark, else the previous item's, else
     the add control. */
  async function deleteItem(key: string) {
    const items = current.value.items;
    const at = items.findIndex(i => keyOf(i) === key);
    if (at < 0 || busy) return;
    const item = items[at];
    const dependents = items.map((i, n) => ({ n: n + 1, id: i.id }))
      .filter(x => x.id !== item.id && current.value.notes.some(note => note.items.includes(x.id)
        && note.depends?.some(d => d.item === item.id)));
    const needs = !dependents.length ? ''
      : ` ${dependents.length === 1
        ? t('task.delete.dependents.one', { n: dependents[0].n })
        : t('task.delete.dependents.many', {
          list: new Intl.ListFormat(I18N.locale).format(dependents.map(x => String(x.n))) })}`;
    const ok = await confirmModal({
      title: t('task.delete.title'), body: t('task.delete.body', { text: esc(item.text) }) + needs,
      okLabel: t('task.item.delete'), danger: true });
    if (!ok) return;
    const wasOpen = current.value.state === 'open';
    const near = [items[at + 1], items[at - 1]].filter(Boolean).map(i => attr('data-step', keyOf(i)));
    focusAfter(...near, '#tkAddOpen');
    const res = await write(client.tasks.deleteItem, { item: item.id }, {
      errKey: 'task.err.delete',
      onOk: async () => {
        forget(key);
        ui.leaving = key;
        await new Promise(done => setTimeout(done, LEAVE_MS));
      },
    });
    ui.leaving = '';
    if (!res?.task) return;
    toast(t(wasOpen && res.task.state !== 'open' ? `task.toast.${res.task.state}` as I18nKey : 'task.toast.deleted'), 'ok');
  }

  /* Brings an item a reference points at into view: scrolled to, its toggle focused without opening
     its panel, its row highlighted for a moment. */
  async function flashItem(id: string | number) {
    const key = String(id);
    const row = host.value?.querySelector<HTMLElement>(`.tk-item${attr('data-key', key)}`);
    if (!row) return;
    row.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
    host.value?.querySelector<HTMLElement>(attr('data-toggle', key))?.focus({ preventScroll: true });
    clearTimeout(flashTimer);
    if (enter.flash === key) {
      enter.flash = '';
      await nextTick();
      void row.offsetWidth;
    }
    enter.flash = key;
    flashTimer = setTimeout(() => { enter.flash = ''; }, FLASH_MS);
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
    focusAfter(attr('data-link', keyOf(item)));
    await write(client.tasks.link, { item: item.id, target: chosen.uids }, { errKey: 'task.err.link' });
  }

  /* the control that goes is replaced by the next link's, else the previous one's, else "add link" */
  function unlink(item: TaskItem, target: string) {
    const at = item.links.findIndex(l => l.uid === target);
    const near = [item.links[at + 1], item.links[at - 1]].filter(Boolean)
      .map(l => `${attr('data-unlink', l.uid)}${attr('data-item', keyOf(item))}`);
    focusAfter(...near, attr('data-link', keyOf(item)));
    void write(client.tasks.unlink, { item: item.id, target }, { errKey: 'task.err.link' });
  }

  async function post(scope: string) {
    const body = (ui.drafts.get(scope) || '').trim();
    if (!body) return;
    focusAfter(attr('data-draft', scope));
    await write(client.tasks.comment, { body, item: scope ? Number(scope) : 0 },
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
    const brief: Record<string, string> = note?.brief ? { ...note.brief }
      : note?.items.length ? { extra_info: note.body } : {};
    const field = (brief.depends_on || '').trim();
    const legacy = note?.brief && !note.depends && field.toLowerCase() !== 'none' ? field : '';
    delete brief.depends_on;
    ui.noteEdit = which;
    ui.noteDraft = { title: note?.title || '', body: note?.body || '', brief, legacy,
                     depends: (note?.depends ?? []).map(d => ({ ...d })),
                     items: note ? [...note.items] : scope ? [Number(scope)] : [] };
    focusAfter(note ? (note.items.length ? '[data-note-field="goal"]' : '[data-note-field="body"]')
                    : '[data-note-field="title"]');
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

  function scopeNote(id: number, on: boolean) {
    const d = ui.noteDraft;
    if (!d) return;
    setScope(d, on ? [...d.items.filter(i => i !== id), id] : d.items.filter(i => i !== id));
  }

  const noteLength = () => {
    const d = ui.noteDraft;
    if (!d) return 0;
    return d.items.length ? renderBrief(briefToSend(d)).length : d.body.length;
  };

  /* What is wrong with the DEPENDS ON of a brief being written, in words: the server refuses a
     dependency on an item the note applies to. */
  const dependsIssue = (): string => {
    const d = ui.noteDraft;
    if (!d?.items.length) return '';
    const at = current.value.items.findIndex(i => d.items.includes(i.id) && d.depends.some(x => x.item === i.id));
    return at < 0 ? '' : t('task.depends.error.self', { n: at + 1 });
  };

  const noteReady = () => {
    const d = ui.noteDraft;
    if (!d?.title.trim() || noteLength() > TASK.NOTE_MAX || dependsIssue()) return false;
    return d.items.length ? BRIEF.every(f => f.optional || f.key === 'depends_on' || (d.brief[f.key] || '').trim())
      : Boolean(d.body.trim());
  };

  async function submitNote() {
    const d = ui.noteDraft;
    if (!d || !noteReady()) return;
    const target = ui.noteEdit;
    const fresh = target.startsWith('new:');
    const payload: Record<string, unknown> = {
      title: d.title.trim(), body: d.items.length ? renderBrief(briefToSend(d)) : d.body.trim(),
      items: current.value.items.map(i => i.id).filter(id => d.items.includes(id)),
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

  /* ── an item's text ── */

  function renameItem(key: string) { ui.renaming = key; focusAfter(attr('data-rename', key)); land(); }
  function leaveRename() {
    const key = ui.renaming;
    ui.renaming = '';
    focusAfter(attr('data-toggle', key));
    land();
  }

  async function saveRename(key: string, text: string) {
    const item = current.value.items.find(i => keyOf(i) === key);
    const next = text.trim();
    if (!item || !next || next.length > TASK.ITEM_MAX) return;
    if (next === item.text) { leaveRename(); return; }
    await write(client.tasks.itemText, { item: item.id, text: next }, {
      errKey: 'task.err.rename',
      onOk: () => { ui.renaming = ''; focusAfter(attr('data-toggle', key)); },
    });
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

  return { current, ui, enter, host, sync, setItem, deleteItem, flashItem, toggle, link, unlink, post,
           noteOpen, toggleNote, editNote, leaveNote, scopeNote, noteReady, noteLength, dependsIssue, submitNote, deleteNote,
           editGoal, leaveGoal, saveGoal, renameItem, leaveRename, saveRename, openAdd, closeAdd, sendAdd };
}

export type Checklist = ReturnType<typeof useChecklist>;
