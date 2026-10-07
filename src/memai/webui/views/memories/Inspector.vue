<script setup lang="ts">
/* The pane beside the list: the caret's memory, or, once rows are ticked, the batch they make. */
import { computed } from 'vue';
import { fmtDate } from '../../core/dom.ts';
import { CONF } from '../../core/shared.js';
import { openRecord } from '../../core/nav.ts';
import { t } from '../../i18n.ts';
import type { DomainEntry } from '../../api/types.ts';
import StatusTag from '../../components/StatusTag.vue';
import TypeTag from '../../components/TypeTag.vue';
import UidChip from '../../components/UidChip.vue';
import BatchEditor from './BatchEditor.vue';
import type { MemoriesState } from './state.ts';

const props = defineProps<{ state: MemoriesState; domains: DomainEntry[] }>();
const picked = props.state.picked;
const one = computed(() => picked.value[0]);

/* A batch is described by how its rows differ, which decides whether one change suits them all. */
const spread = computed(() => {
  const confs = new Set(picked.value.map(m => m.confidence));
  return {
    domains: new Set(picked.value.map(m => m.domain || '')).size,
    types: new Set(picked.value.map(m => m.type)).size,
    only: confs.size === 1 ? CONF[[...confs][0]]?.label : '',
  };
});
</script>

<template>
  <aside id="memInspect" class="mem-inspect" :class="{ 'is-empty': !picked.length }" aria-live="polite">
    <div v-if="!picked.length" class="mi-empty">
      <div class="mi-empty-title">{{ t('mem.mi.emptyTitle') }}</div>
      <p class="hint">{{ t('mem.mi.emptyBody') }}</p>
      <ul class="mi-keys">
        <li><kbd>Space</kbd> {{ t('mem.mi.keySpace') }}</li>
        <li><kbd>Shift</kbd> {{ t('mem.mi.keyShift') }}</li>
        <li><kbd>Enter</kbd> {{ t('mem.mi.keyEnter') }}</li>
      </ul>
    </div>
    <template v-else>
      <div v-if="picked.length === 1" class="mi-head">
        <div class="mi-head-row"><TypeTag :type="one.type" /><UidChip :uid="one.uid" /><StatusTag :status="one.status" /></div>
        <div class="mi-title">{{ one.title || one.content.split('\n', 1)[0] }}</div>
        <div class="mi-facts">
          <span>{{ one.domain || t('mem.mi.noDomain') }}</span>
          <span>{{ t('mem.mi.written', { when: fmtDate(one.created_at) }) }}</span>
        </div>
        <button type="button" class="btn btn-solid btn-sm" data-open @click="openRecord(one.uid)">{{
          t('mem.mi.open') }}</button>
      </div>
      <div v-else class="mi-head">
        <div class="mg-label">{{ t('mem.mi.bulkTitle') }}</div>
        <div class="mi-count">{{ t('mem.mi.nMemories', { n: picked.length }) }}</div>
        <div class="mi-facts">
          <span>{{ t('mem.mi.nDomains', { n: spread.domains }) }}</span>
          <span>{{ t('mem.mi.nTypes', { n: spread.types }) }}</span>
          <span>{{ spread.only ? t('mem.mi.allConf', { label: spread.only }) : t('mem.mi.mixedConf') }}</span>
        </div>
      </div>
      <BatchEditor :state="state" :domains="domains" />
    </template>
  </aside>
</template>
