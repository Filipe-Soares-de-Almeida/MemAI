<script setup lang="ts">
/* One run of a day, as a card that opens it: its number, what it settled against what it still
   holds, and the kinds of curation waiting inside. */
import { computed } from 'vue';
import { fmtInt } from '../../core/dom.ts';
import { go } from '../../core/router.ts';
import { kindColor, kindLabel } from '../../core/shared.js';
import { t } from '../../i18n.ts';
import type { RunRow } from '../../api/types.ts';
import { fmtTime } from './calendar.ts';

const props = defineProps<{ run: RunRow }>();

const open = computed(() => props.run.pending);
const kinds = computed(() => [...props.run.kinds].sort((a, b) => b.total - a.total));
const segs = computed(() => [
  { n: props.run.applied, c: '--ok', label: t('op.applied') },
  { n: props.run.rejected, c: '--bad', label: t('op.rejected') },
  { n: open.value, c: '--warn', label: t('op.cal.lot.open') },
].filter(s => s.n));
/* the catalog marks the count up; the values are formatted numbers */
const counts = computed(() => (open.value
  ? t('op.cal.lot.openOf', { n: fmtInt(open.value), all: fmtInt(props.run.total) })
  : t('op.cal.lot.appliedOf', { n: fmtInt(props.run.applied), all: fmtInt(props.run.total) })
    + (props.run.rejected ? ` · ${t('op.cal.lot.rejectedN', { n: fmtInt(props.run.rejected) })}` : '')));
</script>

<template>
  <button type="button" class="opt-lot" :class="open ? 'is-open' : 'is-settled'" :data-openrun="run.id"
          :title="t('op.cal.openRun', { id: run.id })" @click="go('optimization', { run: String(run.id) })">
    <span class="opt-lot-top">
      <span class="opt-lot-id">#{{ run.id }}</span>
      <span class="opt-lot-at">{{ fmtTime(run.created_at) }}</span>
      <span class="opt-lot-what">{{ open ? t('op.cal.lot.open') : t('op.cal.lot.settled') }}</span>
    </span>
    <span v-if="run.note" class="opt-lot-note">{{ run.note }}</span>
    <span class="meter opt-lot-meter"><div v-for="s in segs" :key="s.c" class="meter-seg"
          :style="{ flex: s.n, background: `var(${s.c})` }" :title="s.label"></div></span>
    <span class="opt-lot-count" v-html="counts"></span>
    <span class="opt-lot-kinds"><span v-for="k in kinds.slice(0, 4)" :key="k.kind" class="opt-lot-kind"
          :title="t('op.cal.lot.kindTitle', { kind: kindLabel(k.kind), n: k.total })"><span class="opt-lot-dot"
          :style="{ background: kindColor(k.kind) }"></span><span>{{ kindLabel(k.kind) }}</span></span><span
          v-if="kinds.length > 4" class="opt-lot-more">{{ t('op.cal.lot.more', { n: kinds.length - 4 }) }}</span></span>
  </button>
</template>
