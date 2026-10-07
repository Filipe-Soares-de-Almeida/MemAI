<script setup lang="ts">
/* Where an edit is written: a note on the version, then Save or Cancel. */
import { t } from '../../i18n.ts';

defineProps<{ id: string; label: string }>();
const note = defineModel<string>('note', { required: true });
const emit = defineEmits<{ save: []; cancel: [] }>();
</script>

<template>
  <div class="rf-save">
    <span class="hint-sm">{{ t('dr.prevKept') }}</span>
    <input v-model="note" type="text" data-note :placeholder="t('dr.editNote.placeholder')"
           :aria-label="t('dr.editNote.placeholder')">
    <button :id="id" type="button" class="btn btn-solid btn-sm" @click="emit('save')">{{ label }}</button>
    <button type="button" class="btn btn-sm" data-cancel @click="emit('cancel')">{{ t('common.cancel') }}</button>
  </div>
</template>
