/* The writes on a whole level. A domain has no status of its own: archiving one is the per-memory
   archive over its subtree, and cross-listings stay where they live. */

import { esc, fmtInt } from '../../core/dom.ts';
import { confirmModal, failed, promptModal, toast } from '../../core/ui.js';
import { invalidateDomains } from '../../core/shared.js';
import { moveToProjectModal } from '../../core/projects.js';
import { refreshBehind } from '../../core/router.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { DomainEntry } from '../../api/types.ts';
import { queue } from './store.ts';

const moveToProject = moveToProjectModal as (opts: { domain: string }) => Promise<boolean>;

async function setStatus(domain: string, status: 'archived' | 'active', reason: string): Promise<void> {
  try {
    const r = await client.domains.status({ domain, status, reason });
    /* the button was live because the level had counts, so zero means someone else wrote first */
    if (!r.affected) { toast(t('do.arch.nothing')); return; }
    /* Undo restores exactly the uids the server flipped, never everything archived in the scope;
       the server withholds the list past what /api/bulk takes back. */
    const undo = status === 'archived' && r.uids.length ? {
      action: {
        label: t('common.undo'),
        run: () => client.bulk({ action: 'restore', uids: r.uids })
          .then(() => {
            toast(t('do.arch.undone', { n: fmtInt(r.uids.length) }), 'ok');
            invalidateDomains();
            refreshBehind();
          })
          .catch(err => failed('err.bulk', err)),
      },
    } : {};
    toast(t(status === 'archived' ? 'do.arch.done' : 'do.rest.done', { n: fmtInt(r.affected) }), 'ok', undo);
    invalidateDomains();
    refreshBehind();
  } catch (err) { failed('err.domain', err); }
}

export async function archiveDomain(d: DomainEntry): Promise<void> {
  const reason = await promptModal({
    title: t('do.arch.title'),
    body: `${t('do.arch.body', { n: fmtInt(d.subtree_active), domain: esc(d.domain) })}${
      d.subtree_also ? ` ${t('do.arch.crossing')}` : ''}`,
    label: t('bulk.reason.label'),
    okLabel: t('common.archive'),
    danger: true,
  });
  if (reason !== null) await setStatus(d.domain, 'archived', reason);
}

export async function restoreDomain(d: DomainEntry): Promise<void> {
  const ok = await confirmModal({
    title: t('do.rest.title'),
    body: t('do.rest.body', { n: fmtInt(d.subtree_archived), domain: esc(d.domain) }),
    okLabel: t('common.restore'),
  });
  if (ok) await setStatus(d.domain, 'active', '');
}

export async function sendToProject(d: DomainEntry): Promise<void> {
  if (!await moveToProject({ domain: d.domain })) return;
  invalidateDomains();
  refreshBehind();
}

/* In the order they were dropped: nesting A under B and then B under C would otherwise address a
   path the first move has already retired. */
export async function applyQueue(): Promise<void> {
  let affected = 0;
  try {
    for (const m of [...queue.value]) affected += (await client.domains.rename({ from: m.from, to: m.to })).affected;
  } catch (err) { failed('err.domain', err); }
  queue.value = [];
  toast(t('do.q.done', { affected: fmtInt(affected) }), 'ok');
  invalidateDomains();
  refreshBehind();
}
