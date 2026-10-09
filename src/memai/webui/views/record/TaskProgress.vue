<script setup lang="ts">
/* How far the task has got: the done share of the items still in play as a percentage, the count under it, and the same share on the bar. */
import { computed, reactive, watch } from 'vue';
import { fmtInt } from '../../core/dom.ts';
import { t } from '../../i18n.ts';
import type { TaskRecord } from '../../api/types.ts';
import { progressOf } from './checklist.ts';

const props = defineProps<{ task: TaskRecord }>();

const p = computed(() => progressOf(props.task));
const frac = () => (p.value.total ? p.value.done / p.value.total : 0);

/* drawn at the old share for a frame, so the fill eases in */
const shown = reactive({ done: frac() });
watch(p, () => requestAnimationFrame(() => requestAnimationFrame(() => { shown.done = frac(); })));

const valueText = computed(() => `${t('task.progress.pct', { n: p.value.pct })}, `
  + t('task.progress.text', { done: p.value.done, total: p.value.total }));
</script>

<!-- The catalog marks the count up; the values are formatted numbers. -->
<template>
  <div class="tk-prog">
    <span class="tk-prog-pct" data-progress-pct>{{ t('task.progress.pct', { n: p.pct }) }}</span>
    <div class="tk-prog-line">
      <span v-if="task.items.length" class="tk-prog-n"
            v-html="t('task.progress', { done: fmtInt(p.done), total: fmtInt(p.total) })"></span>
      <span v-else class="tk-prog-n">{{ t('task.progress.none') }}</span>
    </div>
    <div class="bar-track tk-bar" role="progressbar" :aria-label="t('task.progress.label')" aria-valuemin="0"
         :aria-valuemax="p.total"
         :aria-valuenow="p.done" :aria-valuetext="valueText">
      <div class="bar-fill" :style="{ '--v': shown.done }"></div>
    </div>
  </div>
</template>
