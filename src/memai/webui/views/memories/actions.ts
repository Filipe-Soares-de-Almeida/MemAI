/* What the inspector writes: the staged edits on Apply, and the acts that run when pressed --
   archive, restore, send to a project, permanent delete -- each behind its own confirmation. */

import { esc } from '../../core/dom.ts';
import { failed, promptModal, toast, typedConfirmModal } from '../../core/ui.js';
import { invalidateDomains } from '../../core/shared.js';
import { moveToProjectModal } from '../../core/projects.js';
import { refreshBehind } from '../../core/router.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { MemoryRow } from '../../api/types.ts';
import { bulkBodies } from './staged.ts';
import type { Staged } from './staged.ts';

export async function applyStaged(picked: MemoryRow[], staged: Staged): Promise<void> {
  const uids = picked.map(m => m.uid);
  try {
    let affected = 0;
    for (const body of bulkBodies(staged)) affected += (await client.bulk({ ...body, uids })).affected;
    if (staged.domain) invalidateDomains();
    toast(t('bulk.updated', { n: affected }), 'ok');
    refreshBehind();
  } catch (err) { failed('err.bulk', err); }
}

/* How many titles the delete confirmation lists before it counts the rest. */
const PURGE_PREVIEW = 5;

/* The dialog says how much of the selection is still active and asks for the phrase with the
   count in it; the server copies the store before it deletes. */
async function purge(picked: MemoryRow[]): Promise<void> {
  const uids = picked.map(m => m.uid);
  const active = picked.filter(m => m.status === 'active').length;
  const titles = picked.slice(0, PURGE_PREVIEW).map(m => `<li>${esc(m.title || m.content)}</li>`).join('');
  const rest = picked.length - PURGE_PREVIEW;
  const phrase = `DELETE ${uids.length}`;
  const ok = await typedConfirmModal({
    title: t('bulk.purge.title', { n: uids.length }),
    bodyHTML: `<div class="dz-hint">${t('bulk.purge.hint')}</div>
      <p class="${active ? 'dz-warn' : 'dz-hint'}">${active
        ? t('bulk.purge.active', { n: active })
        : t('bulk.purge.allArchived', { n: uids.length })}</p>
      <ul class="dz-list">${titles}</ul>
      ${rest > 0 ? `<div class="dz-hint">${t('bulk.purge.more', { n: rest })}</div>` : ''}
      <div class="dz-hint">${t('bulk.purge.final')}</div>`,
    phrase,
    okLabel: t('dz.button'),
  });
  if (!ok) return;
  try {
    const r = await client.memories.purgeMany({ uids, confirm: phrase });
    toast(t('bulk.purge.done', { n: r.purged, name: r.backup }), 'ok');
    invalidateDomains();
    refreshBehind();
  } catch (err) { failed('err.purgeMany', err); }
}

export type Act = 'archive' | 'restore' | 'project' | 'purge';

const moveToProject = moveToProjectModal as (opts: { uids: string[] }) => Promise<boolean>;

export async function runAct(act: Act, picked: MemoryRow[]): Promise<void> {
  const uids = picked.map(m => m.uid);
  if (act === 'project') {
    if (await moveToProject({ uids })) refreshBehind();
    return;
  }
  if (act === 'purge') return purge(picked);
  let reason = '';
  if (act === 'archive') {
    const given = await promptModal({
      title: t('bulk.archive.title'), body: t('bulk.archive.body', { n: uids.length }),
      label: t('bulk.reason.label'), okLabel: t('common.archive'), danger: true });
    if (given === null) return;
    reason = given;
  }
  try {
    const r = await client.bulk({ action: act, reason, uids });
    /* Restore over the same set is the exact inverse of an archive, so it is offered as Undo. */
    toast(t('bulk.updated', { n: r.affected }), 'ok', act === 'archive' ? {
      action: {
        label: t('common.undo'),
        run: () => client.bulk({ action: 'restore', uids })
          .then(() => { toast(t('bulk.undone', { n: uids.length }), 'ok'); refreshBehind(); })
          .catch(err => failed('err.bulk', err)),
      },
    } : {});
    refreshBehind();
  } catch (err) { failed('err.bulk', err); }
}
