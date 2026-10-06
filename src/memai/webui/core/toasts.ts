/* Toasts as data that components/ToastHost.vue draws. Kind '' is neutral, 'ok', 'warn' (partial,
   `detail` says what), 'bad' (a sticky alert); an `action` adds a button and a longer timer. */

import { reactive } from '@vue/reactivity';
import { t } from '../i18n.ts';

export type ToastKind = '' | 'ok' | 'warn' | 'bad';
export interface ToastAction { label: string; run?: () => void }
export interface ToastOptions { detail?: string; action?: ToastAction; sticky?: boolean }

export interface Toast {
  id: number;
  msg: string;
  kind: ToastKind;
  detail: string;
  action: ToastAction | null;
  sticky: boolean;
  count: number;
  going: boolean;
  timer: number;
}

const LIFE: Record<string, number> = { '': 3200, ok: 3200, warn: 5000 };
const VISIBLE_MAX = 3;

export const toasts = reactive<Toast[]>([]);

/* arrivals past VISIBLE_MAX wait here rather than pushing the oldest off a stack that does not
   scroll */
const waiting: Array<[string, ToastKind, ToastOptions]> = [];
let nextId = 1;

export function toast(msg: string, kind: ToastKind = '', opts: ToastOptions = {}): void {
  /* a repeat collapses into a count: a batch of applies fires identical toasts */
  const newest = toasts[toasts.length - 1];
  if (newest && !newest.going && !opts.action && newest.msg === msg && newest.kind === kind) {
    newest.count += 1;
    armToast(newest);
    return;
  }
  if (toasts.length >= VISIBLE_MAX) { waiting.push([msg, kind, opts]); return; }
  show(msg, kind, opts);
}

function show(msg: string, kind: ToastKind, opts: ToastOptions): void {
  toasts.push({
    id: nextId++, msg, kind, detail: opts.detail || '', action: opts.action || null,
    sticky: opts.sticky ?? kind === 'bad', count: 1, going: false, timer: 0,
  });
  armToast(toasts[toasts.length - 1]);
}

/* The headline names the action that failed; err.message goes to the detail line. */
export const failed = (key: Parameters<typeof t>[0], err: unknown, opts: ToastOptions = {}): void =>
  toast(t(key), 'bad', { detail: (err as { message?: string } | null)?.message || '', ...opts });

export function holdToast(item: Toast): void { clearTimeout(item.timer); }

export function armToast(item: Toast): void {
  holdToast(item);
  if (item.sticky || item.going) return;
  const ms = item.action ? 6000 : (LIFE[item.kind] ?? 3200);
  item.timer = window.setTimeout(() => dropToast(item), ms);
}

/* Fades it out, then lets the first waiting toast in. */
export function dropToast(item: Toast): void {
  if (item.going) return;
  holdToast(item);
  item.going = true;
  setTimeout(() => {
    const at = toasts.indexOf(item);
    if (at >= 0) toasts.splice(at, 1);
    const next = waiting.shift();
    if (next) show(...next);
  }, 300);
}

/* Forgets every toast, shown or waiting: a fresh shell starts with none. */
export function resetToasts(): void {
  for (const item of toasts) holdToast(item);
  toasts.splice(0);
  waiting.length = 0;
}
