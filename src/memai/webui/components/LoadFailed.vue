<script setup lang="ts">
/* A load that failed, said in place with its reason and a Retry, since a toast fades and leaves the
   space looking empty rather than broken. */
import { t } from '../i18n.ts';
import AppIcon from './AppIcon.vue';

defineProps<{ message?: string }>();
const emit = defineEmits<{ retry: [] }>();
</script>

<template>
  <div class="failed" role="alert">
    <span class="failed-mark"><AppIcon name="contradicted" /></span>
    <div class="failed-text">
      <div>{{ t('err.load') }}</div>
      <div v-if="message" class="failed-detail">{{ message }}</div>
    </div>
    <button type="button" class="btn btn-sm" data-retry @click="emit('retry')">{{ t('common.retry') }}</button>
  </div>
</template>
