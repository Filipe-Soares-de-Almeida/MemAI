<script setup lang="ts">
/* The second half of adding a jump: which step of the chosen flow to arrive on, the whole flow
   included, and why the flow continues there. */
import { ref } from 'vue';
import { esc } from '../../core/dom.ts';
import { t } from '../../i18n.ts';
import type { DiagramRecord } from '../../api/types.ts';
import AppModal from '../../components/AppModal.vue';
import Picker from '../../components/Picker.vue';

const props = defineProps<{ target: DiagramRecord }>();
const emit = defineEmits<{ done: [step: { node: string; label: string } | null] }>();

const whole = t('dg.jump.wholeDiagram');
const nodeItems = [
  { value: '', label: whole, html: `<span class="pick-any">${esc(whole)}</span>` },
  ...props.target.nodes.map(n => ({ value: n.key, label: `${n.key} · ${n.label}` })),
];
const node = ref('');
const label = ref('');
</script>

<template>
  <AppModal :title="t('dg.jump.step.title', { title: target.title })" @close="emit('done', null)">
    <div class="field"><label for="dgjNode">{{ t('dg.jump.step.label') }}</label>
      <Picker id="dgjNode" v-model="node" :items="nodeItems" :aria-label="t('dg.jump.step.label')" /></div>
    <div class="field"><label for="dgjLabel">{{ t('dg.jump.label') }}</label>
      <input v-model="label" type="text" id="dgjLabel" :placeholder="t('dg.jump.labelPh')" autocomplete="off"></div>
    <div class="dg-empty">{{ t('dg.jump.step.hint') }}</div>
    <template #foot>
      <button class="btn" data-x @click="emit('done', null)">{{ t('common.cancel') }}</button>
      <button class="btn btn-solid" data-ok @click="emit('done', { node, label: label.trim() })">{{
        t('dg.jump.add') }}</button>
    </template>
  </AppModal>
</template>
