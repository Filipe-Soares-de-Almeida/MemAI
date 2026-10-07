<script setup lang="ts">
/* The long half of a step: its note, written where there is room for it. */
import { ref } from 'vue';
import { t } from '../../i18n.ts';
import AppModal from '../../components/AppModal.vue';

const props = defineProps<{ stepKey: string; note: string }>();
const emit = defineEmits<{ done: [note: string | null] }>();
const note = ref(props.note);
</script>

<template>
  <AppModal :title="t('dg.note.title', { key: stepKey })" @close="emit('done', null)">
    <div class="field"><label for="dgnNote">{{ t('dg.note') }}</label>
      <textarea v-model="note" id="dgnNote" rows="9" style="min-height:210px" :placeholder="t('dg.notePh')"></textarea></div>
    <div class="dg-empty" style="margin-top:9px">{{ t('dg.note.hint') }}</div>
    <template #foot>
      <button class="btn" data-x @click="emit('done', null)">{{ t('common.cancel') }}</button>
      <button class="btn btn-solid" data-ok @click="emit('done', note)">{{ t('common.save') }}</button>
    </template>
  </AppModal>
</template>
