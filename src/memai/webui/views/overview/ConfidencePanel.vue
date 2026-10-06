<script setup lang="ts">
/* How much of the store a human has vetted, as a ring and its legend, over the health index. */
import { computed } from 'vue';
import { fmtInt } from '../../core/dom.ts';
import { CONF } from '../../core/shared.js';
import { go } from '../../core/router.ts';
import { t } from '../../i18n.ts';
import type { HealthScore } from '../../api/types.ts';
import { CONF_ORDER, confColor } from './health.ts';
import VettedRing from './VettedRing.vue';
import HealthIndex from './HealthIndex.vue';

const props = defineProps<{ health: HealthScore; byConfidence: Record<string, number> }>();

const count = (c: string) => props.byConfidence[c] || 0;
const total = computed(() => CONF_ORDER.reduce((sum, c) => sum + count(c), 0));
/* the same reading as the curation axis, confirmed over active, so the two always agree */
const pct = computed(() => total.value ? Math.round(count('confirmed') * 100 / total.value) : 0);
</script>

<template>
  <div class="panel hx-ring-panel">
    <h3 class="panel-title">{{ t('ov.conf.title') }}
      <span class="panel-aside">{{ t('ov.aside.activeN', { n: fmtInt(total) }) }}</span></h3>
    <VettedRing :counts="byConfidence" :total="total" :pct="pct" />
    <div class="hx-legend">
      <button v-for="c in CONF_ORDER" :key="c" type="button" class="hx-legend-row" :data-conf="c"
              :title="t('ov.conf.open', { label: CONF[c].label })"
              @click="go('memories', { confidence: c, status: 'active' })">
        <span class="hx-swatch" :style="{ background: confColor(c) }"></span>
        <span>{{ CONF[c].label }}</span>
        <b :class="{ 'hx-bad': c === 'contradicted' }">{{ fmtInt(count(c)) }}</b>
      </button>
    </div>
    <HealthIndex :health="health" />
  </div>
</template>
