/* The dashboard's API client: one function per memai/admin.py route, grouped by resource, each
   typed with the response api/types.ts generates from memai/admin_schemas.py. */

import { api, query, seg } from '../core/api.ts';
import type * as T from './types.ts';

type Body = Record<string, unknown>;
/* An object goes through core/api.query, which drops empty values; URLSearchParams keeps them. */
type Query = Record<string, unknown> | URLSearchParams | string;
type Id = number | string;

const get = <R>(path: string, q?: Query): Promise<R> => {
  const qs = q === undefined ? ''
    : typeof q === 'string' || q instanceof URLSearchParams ? String(q) : query(q);
  return api<R>(qs ? `${path}?${qs}` : path);
};
const post = <R>(path: string, body: Body = {}): Promise<R> => api<R>(path, { body });
const remove = <R>(path: string, body?: Body): Promise<R> =>
  api<R>(path, body === undefined ? { method: 'DELETE' } : { method: 'DELETE', body });

export const overview = (): Promise<T.Overview> => get('/api/overview');

export const memories = {
  list: (q: Query) => get<T.MemoryPage>('/api/memories', q),
  get: (uid: string) => get<T.MemoryRecord>(`/api/memories/${seg(uid)}`),
  create: (body: Body) => post<T.MemoryCreated>('/api/memories', body),
  content: (uid: string, body: Body) => post<T.Ok>(`/api/memories/${seg(uid)}/content`, body),
  sections: (uid: string, body: Body) => post<T.Ok>(`/api/memories/${seg(uid)}/sections`, body),
  meta: (uid: string, body: Body) => post<T.MetaSaved>(`/api/memories/${seg(uid)}/meta`, body),
  confidence: (uid: string, body: Body) => post<T.Ok>(`/api/memories/${seg(uid)}/confidence`, body),
  pin: (uid: string, body: Body) => post<T.PinSaved>(`/api/memories/${seg(uid)}/pin`, body),
  status: (uid: string, body: Body) => post<T.Ok>(`/api/memories/${seg(uid)}/status`, body),
  purge: (uid: string, body: Body) => post<T.Ok>(`/api/memories/${seg(uid)}/purge`, body),
  purgeMany: (body: Body) => post<T.Purged>('/api/memories/purge', body),
};

export const bulk = (body: Body) => post<T.BulkDone>('/api/bulk', body);

export const tasks = {
  create: (body: Body) => post<T.TaskCreated>('/api/tasks', body),
  item: (uid: string, body: Body) => post<T.TaskAnswer>(`/api/tasks/${seg(uid)}/item`, body),
  deleteItem: (uid: string, body: Body) => remove<T.TaskAnswer>(`/api/tasks/${seg(uid)}/item`, body),
  items: (uid: string, body: Body) => post<T.TaskAnswer>(`/api/tasks/${seg(uid)}/items`, body),
  goal: (uid: string, body: Body) => post<T.TaskAnswer>(`/api/tasks/${seg(uid)}/goal`, body),
  comment: (uid: string, body: Body) => post<T.TaskAnswer>(`/api/tasks/${seg(uid)}/comment`, body),
  note: (uid: string, body: Body) => post<T.TaskAnswer>(`/api/tasks/${seg(uid)}/note`, body),
  deleteNote: (uid: string, body: Body) => remove<T.TaskAnswer>(`/api/tasks/${seg(uid)}/note`, body),
  link: (uid: string, body: Body) => post<T.TaskAnswer>(`/api/tasks/${seg(uid)}/link`, body),
  unlink: (uid: string, body: Body) => remove<T.TaskAnswer>(`/api/tasks/${seg(uid)}/link`, body),
};

export const relations = {
  create: (body: Body) => post<T.RelationCreated>('/api/relations', body),
  delete: (id: Id) => remove<T.Ok>(`/api/relations/${seg(id)}`),
};

export const graph = (q: Query) => get<T.Graph>('/api/graph', q);

export const diagrams = {
  list: (q: Query) => get<T.DiagramPage>('/api/diagrams', q),
  get: (uid: string) => get<T.DiagramRecord>(`/api/diagrams/${seg(uid)}`),
  create: (body: Body) => post<T.DiagramCreated>('/api/diagrams', body),
  graph: (uid: string, body: Body) => post<T.Ok>(`/api/diagrams/${seg(uid)}/graph`, body),
  meta: (uid: string, body: Body) => post<T.Ok>(`/api/diagrams/${seg(uid)}/meta`, body),
  node: (uid: string, body: Body) => post<T.NodeSaved>(`/api/diagrams/${seg(uid)}/node`, body),
  edge: (uid: string, body: Body) => post<T.Ok>(`/api/diagrams/${seg(uid)}/edge`, body),
  layout: (uid: string, body: Body) => post<T.LayoutSaved>(`/api/diagrams/${seg(uid)}/layout`, body),
  relayout: (uid: string, body: Body = {}) =>
    post<T.LayoutSaved>(`/api/diagrams/${seg(uid)}/relayout`, body),
  link: (uid: string, body: Body) => post<T.Ok>(`/api/diagrams/${seg(uid)}/link`, body),
  jump: (uid: string, body: Body) => post<T.Ok>(`/api/diagrams/${seg(uid)}/jump`, body),
  mermaid: (uid: string) => get<T.Mermaid>(`/api/diagrams/${seg(uid)}/mermaid`),
};

export const changelog = (): Promise<T.Changelog> => get('/api/changelog');

export const update = {
  state: () => get<T.UpdateState>('/api/update'),
  check: (body: Body = {}) => post<T.UpdateState>('/api/update/check', body),
  interval: (body: Body) => post<T.UpdateState>('/api/update/interval', body),
};

export const config = {
  get: () => get<T.Config>('/api/config'),
  set: (body: Body) => post<T.ConfigSaved>('/api/config', body),
};

export const domains = {
  tree: () => get<T.DomainTree>('/api/domains'),
  detail: (q: Query) => get<T.DomainDetail>('/api/domains/detail', q),
  rename: (body: Body) => post<T.DomainRenamed>('/api/domains/rename', body),
  normalize: (body: Body) => post<T.NormalizePlan | T.NormalizeDone>('/api/domains/normalize', body),
  status: (body: Body) => post<T.DomainStatusSaved>('/api/domains/status', body),
  delete: (body: Body) => post<T.DomainDeleted>('/api/domains/delete', body),
};

export const maintenance = {
  health: () => get<T.Health>('/api/maintenance/health'),
  ftsRebuild: (body: Body = {}) => post<T.FtsRebuilt>('/api/maintenance/fts-rebuild', body),
  cleanOrphans: (body: Body = {}) => post<T.OrphansCleaned>('/api/maintenance/clean-orphans', body),
  pruneRenders: (body: Body = {}) => post<T.RendersPruned>('/api/maintenance/prune-renders', body),
  vacuum: (body: Body = {}) => post<T.Vacuumed>('/api/maintenance/vacuum', body),
  backup: (body: Body = {}) => post<T.BackupTaken>('/api/maintenance/backup', body),
  backups: () => get<T.Backups>('/api/maintenance/backups'),
  archive: (body: Body) => post<T.ArchivePlan | T.Archived>('/api/maintenance/archive', body),
  unarchive: (body: Body) => post<T.Unarchived>('/api/maintenance/unarchive', body),
  archiveDelete: (body: Body) => post<T.ArchiveDeleted>('/api/maintenance/archive-delete', body),
  archiveRename: (body: Body) => post<T.Renamed>('/api/maintenance/archive-rename', body),
  backupName: (body: Body) => post<T.BackupNamed>('/api/maintenance/backup-name', body),
  backupPin: (body: Body) => post<T.BackupPinned>('/api/maintenance/backup-pin', body),
  backupDelete: (body: Body) => post<T.BackupsDeleted>('/api/maintenance/backup-delete', body),
  backupRestore: (body: Body) => post<T.BackupRestored>('/api/maintenance/backup-restore', body),
  dedup: (q: Query) => get<T.DedupPairs>('/api/maintenance/dedup', q),
  sectionize: (body: Body = {}) => post<T.Sectionized>('/api/maintenance/sectionize', body),
  sectionsQueue: () => get<T.SectionQueue>('/api/maintenance/sections-queue'),
};

export const projects = {
  list: () => get<T.Projects>('/api/projects'),
  create: (body: Body) => post<T.ProjectCreated>('/api/projects', body),
  activate: (body: Body) => post<T.ProjectActivated>('/api/projects/active', body),
  move: (body: Body) => post<T.ProjectMove>('/api/projects/move', body),
  delete: (name: string) => remove<T.ProjectDeleted>(`/api/projects/${seg(name)}`),
};

export const optimization = {
  runs: () => get<T.OptimizationRuns>('/api/optimization/runs'),
  deleteRun: (id: Id) => remove<T.Ok>(`/api/optimization/runs/${seg(id)}`),
  suggestions: (q: Query) => get<T.Suggestions>('/api/optimization/suggestions', q),
  summary: (q: Query) => get<T.OptimizationSummary>('/api/optimization/summary', q),
  apply: (body: Body) => post<T.Applied>('/api/optimization/apply', body),
  applyAll: (body: Body) => post<T.AppliedAll>('/api/optimization/apply-all', body),
  reject: (body: Body) => post<T.Ok>('/api/optimization/reject', body),
  rejectAll: (body: Body) => post<T.RejectedAll>('/api/optimization/reject-all', body),
  revert: (body: Body) => post<T.Ok>('/api/optimization/revert', body),
};

export const audit = (q: Query) => get<T.AuditLog>('/api/audit', q);

export const lookup = (q: Query) => get<T.Lookup>('/api/lookup', q);
