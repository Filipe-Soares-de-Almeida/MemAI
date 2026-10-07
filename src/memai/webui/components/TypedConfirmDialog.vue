<script setup lang="ts">
/* Confirms an irreversible act: the button stays disabled until the field holds `phrase` exactly.
   `done` reports true for the button, false for Cancel or a close. */
import { computed, onMounted, ref } from 'vue';
import AppModal from './AppModal.vue';
import { esc } from '../core/dom.ts';
import { t } from '../i18n.ts';

const props = withDefaults(defineProps<{ title: string; bodyHtml?: string; phrase: string; okLabel: string }>(),
                           { bodyHtml: '' });
const emit = defineEmits<{ done: [ok: boolean] }>();

const typed = ref('');
const field = ref<HTMLInputElement | null>(null);
const armed = computed(() => typed.value === props.phrase);
const stateText = computed(() => armed.value ? t('dz.armed') : typed.value ? t('dz.mismatch') : '');
onMounted(() => field.value?.focus());
</script>

<!-- title and bodyHtml are markup the caller built; the phrase is escaped into its sentence here. -->
<template>
  <AppModal :title="title" :head-html="title" @close="emit('done', false)">
    <div v-if="bodyHtml" v-html="bodyHtml"></div>
    <div class="dz-type" v-html="t('dz.typeThis', { phrase: `<code>${esc(phrase)}</code>` })"></div>
    <div class="dz-row">
      <input ref="field" v-model="typed" type="text" data-phrase :aria-label="t('dz.phrase.aria')"
             autocomplete="off">
    </div>
    <div class="dz-state" :class="{ armed }" data-state role="status">{{ stateText }}</div>
    <template #foot>
      <button class="btn" data-x @click="emit('done', false)">{{ t('common.cancel') }}</button>
      <button class="btn btn-danger" data-ok :disabled="!armed" @click="emit('done', true)">{{ okLabel }}</button>
    </template>
  </AppModal>
</template>
