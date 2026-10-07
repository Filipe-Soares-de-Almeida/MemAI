<script setup lang="ts">
/* How much of what is still pending the agent checked against the store as it stands: the reading
   that separates "apply the group" from "open them one at a time". */
import { computed } from 'vue';
import { fmtInt } from '../../core/dom.ts';
import { t } from '../../i18n.ts';

const props = defineProps<{ verified: number; pending: number }>();
const unchecked = computed(() => props.pending - props.verified);
</script>

<template>
  <div class="panel opt-vf">
    <h3 class="panel-title">{{ t('op.vf.title') }} <span class="panel-aside">{{
      t('op.vf.pendingN', { n: fmtInt(pending) }) }}</span></h3>
    <template v-if="pending">
      <div class="opt-hero">
        <span class="opt-hero-n">{{ fmtInt(verified) }}</span> <span class="opt-hero-of">{{
          t('op.vf.ofN', { n: fmtInt(pending) }) }}</span>
      </div>
      <div class="meter opt-vf-meter">
        <div v-if="verified" class="meter-seg" :style="{ flex: verified, background: 'var(--ok)' }"
             :title="`${t('op.vf.checked')}: ${verified}`"></div>
        <div v-if="unchecked" class="meter-seg" :style="{ flex: unchecked, background: 'var(--warn)' }"
             :title="`${t('op.vf.unchecked')}: ${unchecked}`"></div>
      </div>
      <div class="legend">
        <span class="legend-item"><span class="dot" style="--c:var(--ok)"></span>{{ t('op.vf.checked') }} <b>{{
          fmtInt(verified) }}</b></span>
        <span class="legend-item"><span class="dot" style="--c:var(--warn)"></span>{{ t('op.vf.unchecked') }} <b>{{
          fmtInt(unchecked) }}</b></span>
      </div>
      <p class="hint-sm">{{ unchecked ? t('op.vf.hintSome', { n: fmtInt(unchecked) }) : t('op.vf.hintAll') }}</p>
    </template>
    <p v-else class="hint-sm">{{ t('op.vf.hintNone') }}</p>
  </div>
</template>
