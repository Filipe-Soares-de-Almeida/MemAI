/* The backups tab's state: the shelf as the server lists it, which zip is being read, how the
   shelf is grouped, what is ticked on it, and every write a control on the tab makes. */

import { computed, onBeforeUnmount, reactive, ref, shallowRef, watch } from 'vue';
import { esc, fmtBytes, fmtInt } from '../../core/dom.ts';
import { confirmModal, failed, openDialog, promptModal, toast } from '../../core/ui.js';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { ArchiveFile, Archived, Backups, ShelfFile } from '../../api/types.ts';
import ArchiveDialog from './ArchiveDialog.vue';
import { archiveLabel, backupKind, zipLabel } from './maintenance.ts';
import type { Maintenance } from './maintenance.ts';

export type ShelfGroup = 'date' | 'reason';
export type ArchiveWay = 'month' | 'week' | 'name' | 'existing';
/* what the archive dialog resolves to: the request fields that say where the backups go */
export type ArchiveWhere = { group: 'month' | 'week' } | { group: 'name'; label: string }
  | { group: 'existing'; into: string };

export interface ShelfRow extends ShelfFile { kind: string }

export function useShelf(ctx: Maintenance) {
  const data = shallowRef<Backups | null>(null);
  const error = ref<Error | null>(null);
  const group = ref<ShelfGroup>('date');
  /* the archive being read, or null for the shelf; a file inside a zip cannot be ticked */
  const zip = ref<string | null>(null);
  const sel = reactive(new Set<string>());
  const renaming = ref<string | null>(null);
  /* where the last archive dialog sent backups */
  const archiveBy = ref<ArchiveWay>('month');
  const pinning = reactive(new Set<string>());
  let alive = true;
  onBeforeUnmount(() => { alive = false; });

  const project = computed(() => data.value?.project ?? '');
  const files = computed<ShelfRow[]>(() =>
    (data.value?.shelf ?? []).map(f => ({ ...f, kind: backupKind(f.name, project.value) })));
  const archive = computed<ArchiveFile | null>(() =>
    (zip.value && data.value?.archives.find(a => a.name === zip.value)) || null);
  const loose = computed(() => files.value.reduce((n, f) => n + f.size, 0));
  const zipped = computed(() => (data.value?.archives ?? []).reduce((n, a) => n + a.size, 0));
  const picked = computed(() => files.value.filter(f => sel.has(f.name)));
  const pickedSize = computed(() => picked.value.reduce((n, f) => n + f.size, 0));

  /* health carries a short list; the shelf is drawn from its own endpoint so every file can be ticked */
  async function load() {
    try {
      const fresh = await client.maintenance.backups();
      if (!alive) return;
      const names = new Set(fresh.shelf.map(f => f.name));
      for (const n of [...sel]) if (!names.has(n)) sel.delete(n);
      if (renaming.value && !names.has(renaming.value)) renaming.value = null;
      if (zip.value && !fresh.archives.some(a => a.name === zip.value)) zip.value = null;
      data.value = fresh;
      error.value = null;
    } catch (err) {
      if (alive) error.value = err instanceof Error ? err : new Error(String(err));
    }
  }
  watch(ctx.changed, () => void load());

  /* every write redraws from the server: archiving moves files between lists, and the sizes on the
     rail are the point of having done it */
  async function afterWrite(msg: string) {
    toast(msg, 'ok');
    sel.clear();
    await load();
    void ctx.loadHealth();
  }

  function open(name: string | null) {
    zip.value = name;
    sel.clear();
  }

  function toggle(name: string, on: boolean) {
    if (on) sel.add(name); else sel.delete(name);
  }

  function toggleAll(on: boolean) {
    if (on) files.value.forEach(f => sel.add(f.name)); else sel.clear();
  }

  async function pin(name: string) {
    const on = !files.value.find(f => f.name === name)?.pinned;
    pinning.add(name);
    try {
      await client.maintenance.backupPin({ name, pinned: on });
      sel.delete(name);
      await load();
    } catch (err) { failed('err.maintenance', err); }
    pinning.delete(name);
  }

  async function saveName(label: string) {
    const name = renaming.value;
    renaming.value = null;
    if (!name) return;
    try {
      await client.maintenance.backupName({ name, label: label.trim() });
      await load();
    } catch (err) { failed('err.maintenance', err); }
  }

  async function renameZip() {
    const a = archive.value;
    if (!a) return;
    const was = archiveLabel(a.name, project.value);
    const label = await promptModal({ title: t('mn.bk.renameZip'), label: t('mn.bk.renameZipLabel'), value: was,
                                      placeholder: t('mn.arch.namePh'), okLabel: t('mn.bk.renameZip') });
    if (label === null || label.trim() === was) return;
    try {
      const r = await client.maintenance.archiveRename({ name: a.name, label });
      zip.value = r.name;
      await afterWrite(t('mn.msg.zipRenamed', { name: zipLabel(r.name, project.value) }));
    } catch (err) { failed('err.maintenance', err); }
  }

  async function unzip() {
    const a = archive.value;
    if (!a || !(await confirmModal({ title: t('mn.bk.unzip'), okLabel: t('mn.bk.unzip'),
                                     body: t('mn.confirm.unzip', { n: a.count, name: esc(a.name) }) }))) return;
    try {
      const r = await client.maintenance.unarchive({ name: a.name });
      zip.value = null;
      await afterWrite(t('mn.msg.unzipped', { n: fmtInt((r.restored as unknown[]).length) }));
    } catch (err) { failed('err.maintenance', err); }
  }

  async function dropZip() {
    const a = archive.value;
    if (!a || !(await confirmModal({ title: t('mn.bk.deleteZip'), okLabel: t('mn.bk.deleteZip'), danger: true,
                                     body: t('mn.confirm.deleteZip', { n: a.count, name: esc(a.name) }) }))) return;
    try {
      const r = await client.maintenance.archiveDelete({ name: a.name });
      zip.value = null;
      await afterWrite(t('mn.msg.zipDeleted', { n: fmtInt(r.count), name: a.name }));
    } catch (err) { failed('err.maintenance', err); }
  }

  /* putting a backup back replaces every memory in the store, so it asks first; the dialog body is markup */
  async function restore() {
    const one = picked.value[0];
    if (!one || !(await confirmModal({ title: t('common.restore'), okLabel: t('mn.bk.restoreOk'),
                                       body: t('mn.confirm.restore', { name: esc(one.label || one.name) }) }))) return;
    try {
      const r = await client.maintenance.backupRestore({ name: one.name });
      toast(t('mn.msg.restored', { name: one.name, kept: r.kept }), 'ok');
      sel.clear();
      ctx.touch();
    } catch (err) { failed('err.maintenance', err); }
  }

  async function remove() {
    const names = picked.value.map(f => f.name);
    if (!(await confirmModal({ title: t('mn.bk.delete'), okLabel: t('mn.bk.delete'), danger: true,
                               body: t('mn.confirm.deleteBackups', { n: names.length, size: fmtBytes(pickedSize.value) }) })))
      return;
    try {
      const r = await client.maintenance.backupDelete({ names });
      await afterWrite(t('mn.msg.deleted', { n: fmtInt(r.deleted), size: fmtBytes(r.freed) }));
    } catch (err) { failed('err.maintenance', err); }
  }

  async function archiveSelected() {
    const shelf = data.value;
    if (!shelf) return;
    const names = picked.value.map(f => f.name);
    const where = await openDialog(ArchiveDialog, {
      names, size: pickedSize.value, project: shelf.project, zips: shelf.archives.map(a => a.name),
      way: archiveBy.value, onWay: (w: ArchiveWay) => { archiveBy.value = w; },
    }) as ArchiveWhere | null;
    if (!where) return;
    try {
      const r = await client.maintenance.archive({ names, ...where }) as Archived;
      await afterWrite(t('mn.msg.archived', {
        n: fmtInt(r.added), name: r.archives.map(a => zipLabel(a.name, shelf.project)).join(', '),
        raw: fmtBytes(r.raw), size: fmtBytes(r.size) }));
    } catch (err) { failed('err.maintenance', err); }
  }

  return { data, error, group, zip, sel, renaming, pinning, project, files, archive, loose, zipped, picked,
           pickedSize, load, open, toggle, toggleAll, pin, saveName, renameZip, unzip, dropZip, restore, remove,
           archiveSelected };
}

export type Shelf = ReturnType<typeof useShelf>;
