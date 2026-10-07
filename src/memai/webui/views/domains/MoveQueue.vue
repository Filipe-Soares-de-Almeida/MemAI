<script setup lang="ts">
/* The moves dropped but not written: a re-home reindexes the whole subtree, so a drop only queues
   it and Apply runs the queue. */
import { computed } from 'vue';
import { fmtInt } from '../../core/dom.ts';
import { t } from '../../i18n.ts';
import { applyQueue } from './actions.ts';
import { queue } from './store.ts';

const memories = computed(() => queue.value.reduce((sum, m) => sum + m.memories, 0));
const discard = () => { queue.value = []; };
</script>

<template>
  <div id="domQueue" class="dom-queue" :hidden="!queue.length">
    <template v-if="queue.length">
      <span class="dom-queue-text">
        <b>{{ t('do.q.moves', { n: queue.length }) }}</b> ·
        {{ t('do.q.reindexed', { n: fmtInt(memories) }) }}
      </span>
      <span class="dom-queue-list"><span v-for="m in queue" :key="m.from" class="dom-queue-item"><code>{{
        m.from }}</code> → <code>{{ m.to }}</code></span></span>
      <span class="dom-queue-end">
        <button class="btn btn-sm" data-discard @click="discard">{{ t('do.q.discard') }}</button>
        <button class="btn btn-solid btn-sm" data-apply @click="applyQueue">{{ t('do.q.apply', { n: queue.length }) }}</button>
      </span>
    </template>
  </div>
</template>
