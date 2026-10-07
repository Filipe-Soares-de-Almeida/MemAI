<script setup lang="ts">
/* The store's casing policy for new domains. Save writes it; Normalize is what touches the domains
   already stored. */
import { ref } from 'vue';
import { failed, toast } from '../../core/toasts.ts';
import { t } from '../../i18n.ts';
import type { I18nKey } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import AppModal from '../../components/AppModal.vue';
import Picker from '../../components/Picker.vue';

const props = defineProps<{ mode: string }>();
const emit = defineEmits<{ done: [saved: boolean] }>();

const MODES = ['preserve', 'lower', 'upper'];
const items = MODES.map(value => ({ value, label: t(`do.case.mode.${value}` as I18nKey) }));
const mode = ref(props.mode);

async function save() {
  try {
    await client.config.set({ domain_case: mode.value });
    emit('done', true);
    toast(t('do.case.saved'), 'ok');
  } catch (err) { failed('err.save', err); }
}
</script>

<template>
  <AppModal :title="t('do.case.title')" @close="emit('done', false)">
    <div class="intro">{{ t('do.case.desc') }}</div>
    <Picker id="caseMode" v-model="mode" :items="items" :aria-label="t('do.case.title')" />
    <template #foot>
      <button class="btn" data-x @click="emit('done', false)">{{ t('common.cancel') }}</button>
      <button class="btn btn-solid" data-ok @click="save">{{ t('do.case.save') }}</button>
    </template>
  </AppModal>
</template>
