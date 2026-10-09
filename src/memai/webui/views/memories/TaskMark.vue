<script setup lang="ts">
/* A task row's mark: how it ended once closed, and how far it is, done out of its items not dropped. */
import { t } from '../../i18n.ts';
import type { MemoryRow } from '../../api/types.ts';
import StatusTag from '../../components/StatusTag.vue';

defineProps<{ m: MemoryRow }>();
</script>

<template>
  <span v-if="m.task_state === 'completed'" class="status-tag completed">{{ t('task.state.completed') }}</span>
  <span v-else-if="m.task_state === 'cancelled'" class="status-tag">{{ t('task.state.cancelled') }}</span>
  <StatusTag v-else :status="m.status" />
  <span v-if="m.progress" class="mem-prog"
        :title="t('task.progress.text', { done: m.progress.done, total: m.progress.total })"><span
        class="bar-track"><span class="bar-fill"
        :style="{ '--v': String(m.progress.total ? m.progress.done / m.progress.total : 0) }"></span></span>
    <span class="mem-prog-n">{{ m.progress.done }}/{{ m.progress.total }}</span></span>
</template>
