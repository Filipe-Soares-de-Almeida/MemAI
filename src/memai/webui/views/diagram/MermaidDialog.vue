<script setup lang="ts">
/* The whole flow as Mermaid source, to read or copy. */
import { toast } from '../../core/ui.js';
import { t } from '../../i18n.ts';
import AppModal from '../../components/AppModal.vue';

const props = defineProps<{ source: string }>();
const emit = defineEmits<{ done: [] }>();

function copy() {
  navigator.clipboard?.writeText(props.source).then(() => toast(t('dg.mermaid.copied'), 'ok'));
  emit('done');
}
</script>

<template>
  <AppModal :title="t('dg.mermaid.title')" @close="emit('done')">
    <div class="dg-empty" style="margin-bottom:9px">{{ t('dg.mermaid.hint') }}</div>
    <pre class="content-pre" style="max-height:340px">{{ source }}</pre>
    <template #foot>
      <button class="btn" data-x @click="emit('done')">{{ t('common.close') }}</button>
      <button class="btn btn-solid" data-ok @click="copy">{{ t('dg.mermaid.copy') }}</button>
    </template>
  </AppModal>
</template>
