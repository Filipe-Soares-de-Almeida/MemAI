<script setup lang="ts">
/* A backup's name being typed: Enter writes it, Escape or leaving the field keeps the old one, since
   a half-typed name saved because the reader clicked elsewhere is a name nobody chose. */
import { onMounted, ref } from 'vue';
import { t } from '../../i18n.ts';

const props = defineProps<{ value: string }>();
const emit = defineEmits<{ save: [label: string]; cancel: [] }>();

const field = ref<HTMLInputElement | null>(null);
let done = false;
onMounted(() => { field.value?.focus(); field.value?.select(); });

function finish(save: boolean) {
  if (done) return;
  done = true;
  if (save) emit('save', field.value?.value ?? props.value); else emit('cancel');
}
</script>

<template>
  <input ref="field" type="text" class="mnt-file-rename" data-renaming :value="value"
         :placeholder="t('mn.bk.renameHint')" :aria-label="t('mn.bk.rename')"
         @keydown.enter.prevent="finish(true)" @keydown.esc.prevent="finish(false)" @blur="finish(false)">
  <div class="mnt-file-sub"><span class="hint-sm">{{ t('mn.bk.renameKeys') }}</span></div>
</template>
