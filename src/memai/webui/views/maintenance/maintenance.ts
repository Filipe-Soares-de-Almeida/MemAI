/* What the maintenance tabs share: the tab ids, the store operations behind their buttons, the
   backup shelf's naming and grouping, and the context the view provides to its tabs. */

import { inject } from 'vue';
import type { InjectionKey, Ref } from 'vue';
import { fmtBytes, fmtInt } from '../../core/dom.ts';
import { I18N, t } from '../../i18n.ts';
import type { I18nKey } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { Health } from '../../api/types.ts';

export const TABS = ['backups', 'storage', 'sections', 'dupes', 'log', 'warden'] as const;
export type Tab = typeof TABS[number];

export const isTab = (id: string | null): id is Tab => TABS.includes(id as Tab);

/* Free pages worth a mention; below it compacting gives back nothing anyone would notice. */
export const RECLAIM_WARN = 262144;

/* Must match db.SVG_RETENTION_MODES; the server rejects anything else. */
export const RETENTION = ['1d', '7d', '30d', 'never'] as const;
export const WARDEN_MINUTES = [5, 10, 20, 30, 60];

const DAY = 86400000;

export interface Op {
  call: () => Promise<unknown>;
  msg: (r: never) => string;
  confirm?: string;
  danger?: boolean;
}

/* Orphans and vacuum destroy what an undo would need, and sectionize rewrites bodies, so those
   ask first; pruning renders only drops a cache the next read redraws. */
export const OPS = {
  fts: { call: () => client.maintenance.ftsRebuild(),
         msg: (r: { rows: number }) => t('mn.msg.fts', { n: fmtInt(r.rows) }) },
  orphans: { call: () => client.maintenance.cleanOrphans(), confirm: t('mn.confirm.orphans'), danger: true,
             msg: (r: { relations_removed: number }) => t('mn.msg.orphans', { r: r.relations_removed }) },
  vacuum: { call: () => client.maintenance.vacuum(), confirm: t('mn.confirm.vacuum'), danger: true,
            msg: (r: { before: number; after: number }) =>
              t('mn.msg.vacuum', { a: fmtBytes(r.before), b: fmtBytes(r.after) }) },
  backup: { call: () => client.maintenance.backup(),
            msg: (r: { path: string; size: number }) =>
              t('mn.msg.backup', { name: r.path.split(/[\\/]/).pop() ?? '', size: fmtBytes(r.size) }) },
  sectionize: { call: () => client.maintenance.sectionize(), confirm: t('mn.confirm.sectionize'),
                msg: (r: { total: number; rewritten: number; needs_review: number }) =>
                  t('mn.msg.sectionize', { t: fmtInt(r.total), n: fmtInt(r.rewritten), q: fmtInt(r.needs_review) }) },
  'prune-renders': { call: () => client.maintenance.pruneRenders(),
                     msg: (r: { pruned: number; bytes: number }) =>
                       t('mn.msg.pruned', { n: fmtInt(r.pruned), size: fmtBytes(r.bytes) }) },
  'prune-renders-all': { call: () => client.maintenance.pruneRenders({ all: true }),
                         msg: (r: { pruned: number; bytes: number }) =>
                           t('mn.msg.pruned', { n: fmtInt(r.pruned), size: fmtBytes(r.bytes) }) },
} satisfies Record<string, Op>;
export type OpKey = keyof typeof OPS;

/* What a backup was taken for, out of the `<project>-[<kind>-]<YYYYmmdd>-<HHMMSS>.db` name
   db.backup_name writes; nothing in the store records it. */
export function backupKind(name: string, project: string): string {
  let s = String(name).replace(/\.db$/i, '');
  const head = `${project}-`;
  if (s.toLowerCase().startsWith(head.toLowerCase())) s = s.slice(head.length);
  return s.replace(/-?\d{8}-\d{6}$/, '');
}

export function reasonLabel(kind: string): string {
  const run = /^optimize-run(\d+)$/.exec(kind);
  if (run) return t('mn.bk.reason.optimize', { n: run[1] });
  if (!kind) return t('mn.bk.reason.hand');
  const key = `mn.bk.reason.${kind}` as I18nKey;
  const named = t(key);
  return named === key ? kind : named;
}

/* Today and this week are named; anything older is its month, with the year once it is not this one. */
export function dateBucket(iso: string, now = new Date()): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return t('mn.bk.older');
  if (d.toDateString() === now.toDateString()) return t('mn.bk.today');
  if (now.getTime() - d.getTime() < 7 * DAY) return t('mn.bk.thisWeek');
  const month = I18N.months[d.getMonth()] || '';
  return d.getFullYear() === now.getFullYear() ? month : `${month} ${d.getFullYear()}`;
}

/* A zip's name without the shelf it sits under or how it is stored: `General-2026-09.zip` is `2026-09`. */
export const archiveLabel = (name: string, project: string): string =>
  name.replace(/\.zip$/, '').replace(new RegExp(`^${project}-`), '');

/* A week archive reads in the language of the interface; anything else is its own name. */
export function zipLabel(name: string, project: string): string {
  const raw = archiveLabel(name, project);
  const week = /^(\d{4})-W(\d{2})$/.exec(raw);
  return week ? t('mn.zip.week', { week: +week[2], year: week[1] }) : raw;
}

/* A log day heading from a YYYY-MM-DD key; core/dom fmtDay drops the year a months-long log needs. */
export function fmtDayKey(key: string, now = new Date()): string {
  const [y, m, d] = String(key).split('-');
  const month = I18N.months[Number(m) - 1];
  if (!month) return key;
  return `${d} ${month}${y === String(now.getFullYear()) ? '' : ` ${y}`}`;
}

/* Rows grouped under the key each one maps to, in the order the keys are first met. */
export function groupBy<R>(rows: R[], key: (row: R) => string): [string, R[]][] {
  const groups = new Map<string, R[]>();
  for (const row of rows) {
    const k = key(row);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k)?.push(row);
  }
  return [...groups];
}

export interface Segment { name?: string; value: number; fill: string }

/* Widths as percentages of their own total; a zero segment comes out as zero, not a hairline. */
export function segmentWidths(parts: Segment[]): string[] {
  const total = parts.reduce((n, p) => n + Math.max(0, p.value), 0) || 1;
  return parts.map(p => `${(Math.max(0, p.value) / total * 100).toFixed(2)}%`);
}

/* What the view hands its tabs: the one health answer two of them are drawn from, a way to have it
   read again, and `changed`, which ticks after a store operation so a tab holding a list re-reads it. */
export interface Maintenance {
  health: Ref<Health | null>;
  loadHealth: () => Promise<void>;
  changed: Ref<number>;
  touch: () => void;
  badge: (id: Tab, n: number) => void;
  show: (id: Tab) => void;
}

export const MAINTENANCE: InjectionKey<Maintenance> = Symbol('maintenance');

export function useMaintenance(): Maintenance {
  const ctx = inject(MAINTENANCE);
  if (!ctx) throw new Error('a maintenance tab outside the maintenance view');
  return ctx;
}
