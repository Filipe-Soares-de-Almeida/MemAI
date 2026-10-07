<script setup lang="ts">
/* Asks for one line of text: `done` reports it, or null for Cancel or a close. Enter submits. */
import { onMounted, ref } from 'vue';
import AppModal from './AppModal.vue';
import { t } from '../i18n.ts';

const props = withDefaults(defineProps<{
  title: string; body?: string; label: string; placeholder?: string; value?: string;
  okLabel?: string; danger?: boolean;
}>(), { body: '', placeholder: '', value: '', okLabel: () => t('common.confirm'), danger: false });
const emit = defineEmits<{ done: [value: string | null] }>();

const text = ref(props.value);
const field = ref<HTMLInputElement | null>(null);
onMounted(() => field.value?.focus());
</script>

<!-- title and body are markup the caller built, with its values escaped. -->
<template>
  <AppModal :title="title" :head-html="title" @close="emit('done', null)">
    <div v-if="body" v-html="body"></div>
    <div class="field"><label>{{ label }}</label>
      <input ref="field" v-model="text" type="text" data-in :placeholder="placeholder"
             @keydown.enter="emit('done', text)"></div>
    <template #foot>
      <button class="btn" data-x @click="emit('done', null)">{{ t('common.cancel') }}</button>
      <button class="btn" :class="danger ? 'btn-danger' : 'btn-solid'" data-ok
              @click="emit('done', text)">{{ okLabel }}</button>
    </template>
  </AppModal>
</template>
