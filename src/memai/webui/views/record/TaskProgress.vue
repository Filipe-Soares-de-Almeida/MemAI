<script setup lang="ts">
/* How far the task has got: done over all items, and the dropped share hatched after it. */
import { computed, reactive, watch } from 'vue';
import { fmtInt } from '../../core/dom.ts';
import { t } from '../../i18n.ts';
import type { TaskRecord } from '../../api/types.ts';
import { progressOf } from './checklist.ts';

const props = defineProps<{ task: TaskRecord }>();

const p = computed(() => progressOf(props.task));
const frac = (n: number) => (p.value.total ? n / p.value.total : 0);

/* drawn at the old shares for a frame, so a part that just appeared eases in like the rest */
const shown = reactive({ done: frac(p.value.done), dropped: frac(p.value.dropped) });
watch(p, () => requestAnimationFrame(() => requestAnimationFrame(() => {
  shown.done = frac(p.value.done);
  shown.dropped = frac(p.value.dropped);
})));

const valueText = computed(() => t('task.progress.text', { done: p.value.done, total: p.value.total })
  + (p.value.dropped ? `, ${t('task.dropped', { n: p.value.dropped })}` : ''));
</script>

<!-- The catalog marks the count up; the values are formatted numbers. -->
<template>
  <div class="tk-prog">
    <div class="tk-prog-line">
      <span v-if="p.total" class="tk-prog-n"
            v-html="t('task.progress', { done: fmtInt(p.done), total: fmtInt(p.total) })"></span>
      <span v-else class="tk-prog-n">{{ t('task.progress.none') }}</span>
      <span v-if="p.dropped" class="chip" :title="t('task.dropped.why')">{{
        t('task.dropped', { n: fmtInt(p.dropped) }) }}</span>
    </div>
    <div class="bar-track tk-bar" role="progressbar" aria-valuemin="0" :aria-valuemax="p.total"
         :aria-valuenow="p.done" :aria-valuetext="valueText">
      <div class="bar-fill" :style="{ '--v': shown.done }"></div>
      <div v-if="p.dropped" class="tk-bar-drop" :style="{ '--v0': shown.done, '--v': shown.dropped }"></div>
    </div>
  </div>
</template>
