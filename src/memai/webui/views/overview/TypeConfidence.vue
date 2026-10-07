<script setup lang="ts">
/* Each type's active memories split by confidence; a row opens that type's list. */
import { computed } from 'vue';
import { fmtInt } from '../../core/dom.ts';
import { CONF, TYPE_ORDER } from '../../core/shared.js';
import { go } from '../../core/router.ts';
import { t } from '../../i18n.ts';
import TypeTag from '../../components/TypeTag.vue';
import { CONF_ORDER, confColor } from './health.ts';

const props = defineProps<{
  byType: Record<string, number>;
  byTypeConfidence: Record<string, Record<string, number>>;
}>();

/* the known types in their usual order, then any this build does not know */
const present = computed(() => [
  ...TYPE_ORDER.filter(tp => tp in props.byType),
  ...Object.keys(props.byType).filter(tp => !TYPE_ORDER.includes(tp)),
]);

const segments = (tp: string) => CONF_ORDER
  .map(c => ({ c, n: props.byTypeConfidence[tp]?.[c] || 0 }))
  .filter(s => s.n);
</script>

<template>
  <div class="panel">
    <h3 class="panel-title">{{ t('ov.byType.title') }}
      <span class="panel-aside">{{ t('ov.aside.active') }}</span></h3>
    <div class="hx-types">
      <button v-for="tp in present" :key="tp" type="button" class="hx-type" :data-type="tp"
              :title="t('ov.byType.open', { type: tp })"
              @click="go('memories', { type: tp, status: 'active' })">
        <TypeTag :type="tp" />
        <span class="hx-type-bar"><div v-for="s in segments(tp)" :key="s.c"
             :style="{ flex: String(s.n), background: confColor(s.c) }"
             :title="`${CONF[s.c].label}: ${fmtInt(s.n)}`"></div></span>
        <span class="hx-type-n">{{ fmtInt(byType[tp]) }}</span>
      </button>
      <div v-if="!present.length" class="empty">{{ t('ov.types.empty') }}</div>
    </div>
  </div>
</template>
