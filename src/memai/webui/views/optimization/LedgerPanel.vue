<script setup lang="ts">
/* What the undecided half of a run would do to the store, counted off its staged payloads; a row
   whose figure is zero is left out. */
import { computed } from 'vue';
import { fmtInt } from '../../core/dom.ts';
import { t } from '../../i18n.ts';
import type { OptimizationSummary } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';

const props = defineProps<{ summary: OptimizationSummary }>();

const rows = computed(() => {
  const L = props.summary.ledger;
  return [
    { k: t('op.lg.memories'), v: t('op.lg.ofActive', { n: fmtInt(L.memories), all: fmtInt(L.active) }), cls: '', on: 1 },
    { k: t('op.lg.relations'), v: `+${fmtInt(L.relations)}`, cls: 'pos', on: L.relations },
    { k: t('op.lg.confirmed'), v: `+${fmtInt(L.confirmed)}`, cls: 'pos', on: L.confirmed },
    /* a rewrite normally removes text; the row swaps its wording when a run adds */
    { k: L.chars > 0 ? t('op.lg.charsAdded') : t('op.lg.chars'),
      v: t(L.chars > 0 ? 'op.lg.charsUp' : 'op.lg.charsDown', { n: fmtInt(Math.abs(L.chars)) }),
      cls: L.chars > 0 ? 'pos' : 'neg', on: L.chars },
    { k: t('op.lg.archived'), v: fmtInt(L.archived), cls: '', on: L.archived },
    { k: t('op.lg.domains'), v: fmtInt(L.domains), cls: '', on: L.domains },
  ].filter(r => r.on);
});
/* the file name is what a restore needs, and longer than the rail, so it sits on the hover */
const backup = computed(() => (props.summary.run.backup_path || '').split(/[\\/]/).pop() || '');
</script>

<template>
  <div class="panel opt-lg">
    <h3 class="panel-title">{{ t('op.lg.title') }}</h3>
    <dl class="opt-ledger"><template v-for="r in rows" :key="r.k"><dt>{{ r.k }}</dt><dd v-bind="r.cls ? { class: r.cls } : {}">{{
      r.v }}</dd></template></dl>
    <div v-if="backup" class="opt-lg-backup" :title="t('op.backupNote', { name: backup })"><AppIcon
         name="confirmed" /><span class="opt-lg-backup-name">{{ backup }}</span></div>
  </div>
</template>
