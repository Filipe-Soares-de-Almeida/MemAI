<script setup lang="ts">
/* The health index: one figure, the mean of the four axis bars under it, and its change. */
import { computed } from 'vue';
import { t } from '../../i18n.ts';
import type { HealthScore } from '../../api/types.ts';
import { AXES, band } from './health.ts';

const props = defineProps<{ health: HealthScore }>();

/* null until a snapshot as old as the window exists (db.health_since) */
const delta = computed(() => {
  const { delta: d, delta_days: days } = props.health;
  return d == null ? null : { down: d < 0, text: `${d > 0 ? '+' : ''}${d} ${t('ov.hx.inDays', { n: days })}` };
});
</script>

<template>
  <div class="hx-second">
    <div class="hx-second-head">
      <span class="hx-second-name">
        <span class="mg-label">{{ t('ov.hx.title') }}</span>
        <span class="hx-score" :style="{ color: band(health.score) }">{{ health.score }}<span
              class="hx-score-of">&nbsp;/ 100</span></span>
      </span>
      <span v-if="delta" class="hx-delta" :class="{ down: delta.down }">{{ delta.text }}</span>
    </div>
    <div class="hx-axes">
      <div v-for="a in AXES" :key="a" class="hx-axis">
        <span class="hx-axis-name" :title="t(`ov.axis.${a}.why`)">{{ t(`ov.axis.${a}`) }}</span>
        <div class="bar-track"><div class="bar-fill"
             :style="{ '--v': (health.axes[a] / 100).toFixed(4), background: band(health.axes[a]) }"></div></div>
        <span class="hx-axis-val">{{ health.axes[a] }}</span>
      </div>
    </div>
  </div>
</template>
