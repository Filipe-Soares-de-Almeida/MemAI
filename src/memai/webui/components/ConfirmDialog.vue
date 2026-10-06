<script setup lang="ts">
/* Asks to go ahead with an act: `done` reports true for the button, false for Cancel or a close. */
import AppModal from './AppModal.vue';
import { t } from '../i18n.ts';

withDefaults(defineProps<{ title: string; body: string; okLabel?: string; danger?: boolean }>(),
             { okLabel: () => t('common.confirm'), danger: false });
const emit = defineEmits<{ done: [ok: boolean] }>();
</script>

<!-- title and body are markup the caller built, with its values escaped. -->
<template>
  <AppModal :title="title" :head-html="title" @close="emit('done', false)">
    <div v-html="body"></div>
    <template #foot>
      <button class="btn" data-x @click="emit('done', false)">{{ t('common.cancel') }}</button>
      <button class="btn" :class="danger ? 'btn-danger' : 'btn-solid'" data-ok
              @click="emit('done', true)">{{ okLabel }}</button>
    </template>
  </AppModal>
</template>
