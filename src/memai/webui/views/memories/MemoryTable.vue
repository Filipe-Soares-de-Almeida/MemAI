<script setup lang="ts">
/* The rows as a grid with one tab stop: arrows move the caret, Space ticks, Shift ticks a run,
   Enter opens, Ctrl+A ticks the page and Escape clears; a ticked page offers every matching row. */
import { computed, ref } from 'vue';
import { fmtAgo, fmtInt } from '../../core/dom.ts';
import { failed } from '../../core/toasts.ts';
import { inDomainPath } from '../../core/shared.js';
import { openRecord } from '../../core/nav.ts';
import { t } from '../../i18n.ts';
import { ADMIN } from '../../contract.ts';
import * as client from '../../api/client.ts';
import type { MemoryRow } from '../../api/types.ts';
import ConfPill from '../../components/ConfPill.vue';
import PinMark from '../../components/PinMark.vue';
import StatusTag from '../../components/StatusTag.vue';
import TypeTag from '../../components/TypeTag.vue';
import TaskMark from './TaskMark.vue';
import type { MemoriesState } from './state.ts';

const props = defineProps<{ state: MemoriesState; items: MemoryRow[]; scope: string }>();
const s = props.state;

const listEl = ref<HTMLElement | null>(null);
const rowEls = () => [...(listEl.value?.querySelectorAll<HTMLElement>('.mem-row') ?? [])];
/* the row holding the list's one tab stop */
const cursor = ref(0);
/* where a Shift range starts: the last row ticked or clicked on purpose */
let anchor = 0;

const selected = (uid: string) => s.selection.has(uid);
function select(i: number, on: boolean) {
  const uid = props.items[i].uid;
  if (on) s.selection.add(uid);
  else s.selection.delete(uid);
}

function setCursor(i: number) {
  if (i < 0 || i >= props.items.length) return;
  cursor.value = i;
  const uid = props.items[i].uid;
  if (uid === s.caretUid.value) return;
  s.caretUid.value = uid;
  /* The pane follows the caret only while nothing is ticked, and staged edits stay with the row
     they were made for. */
  if (!s.selection.size) s.clearStaged();
}

function moveTo(i: number) {
  if (i < 0 || i >= props.items.length) return;
  setCursor(i);
  rowEls()[i]?.focus();
}

function onFocusIn(e: FocusEvent) {
  const row = (e.target as HTMLElement).closest<HTMLElement>('.mem-row');
  if (row) setCursor(rowEls().indexOf(row));
}

function toggle(i: number, on = !selected(props.items[i].uid)) {
  select(i, on);
  anchor = i;
}

/* A range writes only the run it covers, so picks made before it survive. */
function range(to: number, on: boolean) {
  const [a, b] = anchor <= to ? [anchor, to] : [to, anchor];
  for (let i = a; i <= b; i++) select(i, on);
}

function setAll(on: boolean) {
  props.items.forEach((_, i) => select(i, on));
  if (!on) s.selection.clear();
  anchor = 0;
}

/* A click moves the caret; ticking belongs to the box and to Space. The second click of a double
   click is left to dblclick. */
function onRowClick(e: MouseEvent, i: number) {
  if (e.detail > 1 || (e.target as HTMLElement).closest('input[type=checkbox]')) return;
  if (e.shiftKey) range(i, true);
  else anchor = i;
  moveTo(i);
}

/* the box has already flipped, so its state is the one applied, to the run when Shift is held */
function onBoxClick(e: MouseEvent, i: number) {
  e.stopPropagation();
  const on = (e.target as HTMLInputElement).checked;
  if (e.shiftKey) range(i, on);
  else toggle(i, on);
}

const STEP: Record<string, number> = { ArrowDown: 1, ArrowUp: -1 };

function onKey(e: KeyboardEvent) {
  const row = (e.target as HTMLElement).closest<HTMLElement>('.mem-row');
  if (!row) return;
  const i = rowEls().indexOf(row);
  const step = STEP[e.key];
  if (step !== undefined) {
    const to = Math.min(props.items.length - 1, Math.max(0, i + step));
    e.preventDefault();
    if (e.shiftKey) { select(i, true); range(to, true); }
    moveTo(to);
  } else if (e.key === 'Home' || e.key === 'End') {
    e.preventDefault();
    moveTo(e.key === 'Home' ? 0 : props.items.length - 1);
  } else if (e.key === ' ') {
    /* the checkbox has its own Space when the caret is on the box itself */
    if ((e.target as HTMLElement).tagName === 'INPUT') return;
    e.preventDefault();
    if (e.shiftKey) range(i, true);
    else toggle(i);
  } else if (e.key === 'Enter') {
    e.preventDefault();
    openRecord(props.items[i].uid);
  } else if ((e.ctrlKey || e.metaKey) && (e.key === 'a' || e.key === 'A')) {
    e.preventDefault();
    setAll(true);
  } else if (e.key === 'Escape' && s.selection.size) {
    /* with nothing ticked, Escape stays the app's key for closing what is layered over the view */
    setAll(false);
  }
}

const onPage = computed(() => props.items.filter(m => selected(m.uid)).length);
const selLabel = computed(() => {
  const n = s.ticked.value.length;
  const page = s.pageUids.length;
  if (!n) return t('mem.selectAll', { n: page });
  return n > page ? t('mem.selectedAcross', { n }) : t('mem.selectedOf', { n, all: page });
});

/* A cross-listed row is not filed in the domain filtered on, and says so. */
const away = (m: MemoryRow) => Boolean(props.scope) && !inDomainPath(m.domain, props.scope)
  && (m.also || []).some(p => inDomainPath(p, props.scope));

const banner = computed(() => {
  const whole = s.matching.size > 0 && s.selection.size === s.matching.size
    && [...s.matching].every(uid => s.selection.has(uid));
  if (whole) return 'all';
  const pageFull = s.pageUids.length > 0 && s.pageUids.every(uid => s.selection.has(uid));
  return pageFull && s.total > s.pageUids.length ? 'offer' : '';
});
const offerLabel = computed(() => {
  const n = Math.min(s.total, ADMIN.BULK_MAX);
  return s.total > ADMIN.BULK_MAX
    ? t('mem.selectMatchingCapped', { n: fmtInt(n), all: fmtInt(s.total) })
    : t('mem.selectMatching', { n: fmtInt(n) });
});

const loading = ref(false);
/* every row the filter matches, up to BULK_MAX, in one request */
async function selectMatching() {
  loading.value = true;
  try {
    const r = await client.memories.list(s.matchQuery);
    s.matching.clear();
    for (const m of r.items) {
      s.matching.add(m.uid);
      s.rows.set(m.uid, m);
      s.selection.add(m.uid);
    }
  } catch (err) {
    failed('err.bulk', err);
  } finally {
    loading.value = false;
  }
}

defineExpose({ focusCaret: () => rowEls()[cursor.value]?.focus() });
</script>

<!-- The select-all strip sits over the grid, not in it, so it is never a row to arrow onto. -->
<template>
  <div class="mem-list">
    <template v-if="items.length">
      <div class="mem-head">
        <div class="mem-check"><input id="memAll" type="checkbox" :aria-label="t('mem.selectAll.aria')"
             :checked="onPage > 0 && onPage === items.length" :indeterminate="onPage > 0 && onPage < items.length"
             @change="setAll(($event.target as HTMLInputElement).checked)"></div>
        <!-- the catalog marks the count up; every value in it is a number -->
        <label class="mem-head-label" for="memAll" data-selcount v-html="selLabel"></label>
        <div class="mem-head-keys" aria-hidden="true">{{ t('mem.keys.hint') }}</div>
      </div>
      <div id="memBanner" class="mem-banner" role="status" :hidden="!banner">
        <template v-if="banner === 'all'"><span>{{ t('mem.allMatching', { n: fmtInt(s.matching.size) }) }}</span>
          <button type="button" class="btn btn-sm btn-ghost" data-bn-clear
                  @click="s.selection.clear()">{{ t('mem.selectionClear') }}</button></template>
        <template v-else-if="banner === 'offer'"><span>{{ t('mem.pageSelected', { n: s.pageUids.length }) }}</span>
          <button type="button" class="btn btn-sm" data-bn-all :disabled="loading"
                  @click="selectMatching">{{ offerLabel }}</button></template>
      </div>
    </template>
    <!-- a grid, not a listbox: a row holds a checkbox, which an option may not contain -->
    <div id="memList" ref="listEl" :role="items.length ? 'grid' : undefined"
         :aria-multiselectable="items.length ? 'true' : undefined" :aria-label="items.length ? t('mem.title') : undefined"
         @focusin="onFocusIn" @keydown="onKey">
      <div v-if="!items.length" class="empty">{{ t('mem.empty') }}</div>
      <template v-else>
        <div v-for="(m, i) in items" :key="m.uid" class="mem-row"
             :class="{ selected: selected(m.uid), 'is-cursor': m.uid === s.caretUid.value }" role="row"
             :aria-selected="selected(m.uid) ? 'true' : 'false'" :tabindex="i === cursor ? 0 : -1" :data-uid="m.uid"
             :title="m.fts_rank != null ? `bm25 ${Number(m.fts_rank).toFixed(2)}` : undefined"
             @click="onRowClick($event, i)" @dblclick="openRecord(m.uid)">
          <!-- out of the tab order: the row's own keys reach them, and fifty rows of stops would not -->
          <div class="mem-check" role="gridcell"><input type="checkbox" tabindex="-1"
               :aria-label="t('mem.select.aria', { uid: m.uid })" :checked="selected(m.uid)"
               @click="onBoxClick($event, i)"></div>
          <div class="mem-col-type" role="gridcell"><span class="pin-slot"><PinMark :pin="m.pin" /></span><ConfPill
               :confidence="m.confidence" compact /><TypeTag :type="m.type" /></div>
          <div class="mem-main" role="gridcell">
            <!-- a titled row shows its title with the body on hover; an untitled one is its body -->
            <span class="mem-snippet" :class="{ 'mem-named': m.title }"
                  :title="m.title ? m.content : undefined">{{ m.title || m.content }}</span>
          </div>
          <div class="mem-right" role="gridcell">
            <!-- only the row a pasted uid pinned: every other row is a keyword hit -->
            <span v-if="m.match_source === 'uid'" class="match-badge"
                  :title="t('badge.uidMatchWhy')">{{ t('badge.uidMatch') }}</span>
            <TaskMark v-if="m.type === 'task'" :m="m" />
            <StatusTag v-else :status="m.status" />
            <span v-if="away(m)" class="chip" :title="t('mem.alsoWhy', { domain: m.domain })">{{ t('mem.also') }}</span>
            <span v-if="m.domain" class="mem-domain">{{ m.domain }}</span>
            <span :title="m.created_at">{{ fmtAgo(m.created_at) }}</span>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>
