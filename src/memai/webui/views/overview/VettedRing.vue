<script setup lang="ts">
/* The confidence split as dash segments on one circle, so the segments cannot drift apart. */
import { computed } from 'vue';
import { CONF } from '../../core/shared.js';
import { t } from '../../i18n.ts';
import { CONF_ORDER, confColor } from './health.ts';

const props = defineProps<{ counts: Record<string, number>; total: number; pct: number }>();

const R = 90;
const C = 2 * Math.PI * R;

const arcs = computed(() => {
  let at = 0;
  return CONF_ORDER.flatMap(c => {
    const value = props.counts[c] || 0;
    if (!value || !props.total) return [];
    const len = (value / props.total) * C;
    const arc = { c, dash: `${len.toFixed(1)} ${(C - len).toFixed(1)}`, offset: (-at).toFixed(1) };
    at += len;
    return [arc];
  });
});
</script>

<!-- The graphic is decorative: a screen reader gets the figure in the middle and the legend. -->
<template>
  <div class="hx-ring" :title="t('ov.conf.ringLabel', { pct, label: CONF.confirmed.label })">
    <svg viewBox="0 0 200 200" aria-hidden="true">
      <circle cx="100" cy="100" :r="R" fill="none" stroke="var(--inset)" stroke-width="20"></circle>
      <circle v-for="arc in arcs" :key="arc.c" cx="100" cy="100" :r="R" fill="none"
              :stroke="confColor(arc.c)" stroke-width="20" :stroke-dasharray="arc.dash"
              :stroke-dashoffset="arc.offset"></circle>
    </svg>
    <div class="hx-ring-mid">
      <div class="hx-ring-pct">{{ pct }}%</div>
      <div class="hx-ring-cap">{{ CONF.confirmed.label }}</div>
    </div>
  </div>
</template>
