<script setup lang="ts">
/* Open tasks left for the next session: work, not a defect, so absent at zero. */
import { fmtInt } from '../../core/dom.ts';
import { go } from '../../core/router.ts';
import { t } from '../../i18n.ts';
import TypeTag from '../../components/TypeTag.vue';

defineProps<{ count: number }>();
</script>

<template>
  <div v-if="count" class="panel hx-tasks">
    <TypeTag type="task" />
    <div class="hx-tasks-text">
      <span class="hx-tasks-title">{{ t('ov.tasks.open', { n: fmtInt(count) }) }}</span>
      <span class="hx-tasks-aside">{{ t('ov.tasks.aside') }}</span>
    </div>
    <button type="button" class="btn btn-sm"
            @click="go('memories', { type: 'task', task_state: 'open', status: 'active' })">{{
      t('ov.tasks.act') }}</button>
  </div>
</template>
