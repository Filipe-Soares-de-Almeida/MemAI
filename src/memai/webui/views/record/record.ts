/* What the record view derives from a memory: its editable fields, the body a save sends, and the
   line diff the edit history shows. */

import { inject } from 'vue';
import type { InjectionKey } from 'vue';
import { sectionLabel } from '../../core/shared.js';
import { refreshBehind } from '../../core/router.ts';
import { t } from '../../i18n.ts';
import type { MemoryRecord } from '../../api/types.ts';

/* How a part of the record has it read again after a write; outside a record, the route reruns. */
export const REFRESH: InjectionKey<() => void> = Symbol('refresh');
export const useRefresh = (): (() => void) => inject(REFRESH, refreshBehind);

/* One editable field; a type with no spec has exactly one, keyed '', so a sectioned body is not a
   second code path. `raw` is the label stored in the body, shown as the translated label's title. */
export interface Field { key: string; max: number; label: string; raw?: string; text: string; present: boolean }

export function fieldsOf(m: MemoryRecord): Field[] {
  const spec = m.type === 'diagram' ? [] : m.spec;
  const text = new Map(m.sections.map(s => [s.key, s.text]));
  return spec.length && !m.section_problem
    ? spec.map(s => ({ key: s.key, max: s.max_len, label: sectionLabel(m.type, s), raw: s.label,
                       text: text.get(s.key) ?? '', present: text.has(s.key) }))
    : [{ key: '', max: 0, label: t('dr.content'), text: m.content, present: true }];
}

/* A block's opening for its row in the index, its own line breaks collapsed. */
export const peekOf = (text: string): string => text.replace(/\s+/g, ' ').trim().slice(0, 180);

export const tagList = (tags: string): string[] => tags.split(',').map(x => x.trim()).filter(Boolean);

/* A sectioned body is sent whole, built from its fields: one open field still sends the rest as read. */
export function saveBody(fields: Field[], drafts: Record<string, string>, note: string):
    { sections: Record<string, string>; note: string } | { content: string; note: string } {
  const sectioned = fields.length > 1 || fields[0]?.key !== '';
  if (!sectioned) return { content: drafts[''] ?? fields[0].text, note };
  return { sections: Object.fromEntries(fields.map(f => [f.key, drafts[f.key] ?? f.text])), note };
}

export interface DiffLine { cls: 'diff-ctx' | 'diff-del' | 'diff-add'; text: string }

/* A plain LCS line diff, which memory-sized bodies afford; past that size, both ends cut short. */
export function diffLines(a: string, b: string): DiffLine[] {
  const A = a.split('\n'), B = b.split('\n');
  if (A.length * B.length > 250000)
    return [{ cls: 'diff-del', text: `− ${a.slice(0, 800)}…` }, { cls: 'diff-add', text: `+ ${b.slice(0, 800)}…` }];
  const dp = Array.from({ length: A.length + 1 }, () => new Uint16Array(B.length + 1));
  for (let i = A.length - 1; i >= 0; i--)
    for (let j = B.length - 1; j >= 0; j--)
      dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: DiffLine[] = [];
  let i = 0, j = 0;
  while (i < A.length && j < B.length) {
    if (A[i] === B[j]) { out.push({ cls: 'diff-ctx', text: `  ${A[i]}` }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ cls: 'diff-del', text: `− ${A[i]}` }); i++; }
    else { out.push({ cls: 'diff-add', text: `+ ${B[j]}` }); j++; }
  }
  while (i < A.length) out.push({ cls: 'diff-del', text: `− ${A[i++]}` });
  while (j < B.length) out.push({ cls: 'diff-add', text: `+ ${B[j++]}` });
  return out;
}
