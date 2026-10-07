<script setup lang="ts">
/* A flow's title and summary. */
import { ref } from 'vue';
import { t } from '../../i18n.ts';
import AppModal from '../../components/AppModal.vue';

const props = defineProps<{ title: string; summary: string }>();
const emit = defineEmits<{ done: [meta: { title: string; summary: string } | null] }>();
const name = ref(props.title);
const summary = ref(props.summary);
</script>

<template>
  <AppModal :title="t('dg.meta')" @close="emit('done', null)">
    <div class="field"><label for="dgmTitle">{{ t('dg.meta.name') }}</label>
      <input v-model="name" type="text" id="dgmTitle"></div>
    <div class="field"><label for="dgmSummary">{{ t('dg.meta.summary') }}</label>
      <textarea v-model="summary" id="dgmSummary" rows="6" :placeholder="t('dg.meta.summaryPh')"></textarea></div>
    <template #foot>
      <button class="btn" data-x @click="emit('done', null)">{{ t('common.cancel') }}</button>
      <button class="btn btn-solid" data-ok @click="emit('done', { title: name, summary })">{{ t('common.save') }}</button>
    </template>
  </AppModal>
</template>
