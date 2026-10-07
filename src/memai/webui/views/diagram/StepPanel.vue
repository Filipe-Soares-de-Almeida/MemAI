<script setup lang="ts">
/* The selected step's fields, read-only until editing is on. */
import { ref } from 'vue';
import { NODE_SHAPES } from '../../engines/diagram-engine.ts';
import { t } from '../../i18n.ts';
import type { I18nKey } from '../../i18n.ts';
import type { DiagramNodeRow } from '../../api/types.ts';
import Picker from '../../components/Picker.vue';
import { useActions } from './actions.ts';

const props = defineProps<{ node: DiagramNodeRow; editing: boolean }>();
const actions = useActions();

const shapes = (NODE_SHAPES as readonly string[]).map(s => ({ value: s, label: t(`dg.shape.${s}` as I18nKey) }));
const label = ref(props.node.label);
const shape = ref(props.node.shape);
const note = ref(props.node.note);
</script>

<template>
  <div class="dg-panel">
    <h3>{{ t('dg.step') }} <span class="dg-key">{{ node.key }}</span></h3>
    <div v-if="!editing" class="dg-empty">{{ t('dg.readOnlyNote') }}</div>
    <div class="field"><label for="dgLabel">{{ t('dg.label') }}</label>
      <input v-model="label" type="text" id="dgLabel" :disabled="!editing"></div>
    <div class="field"><label for="dgShape">{{ t('dg.shape') }}</label>
      <Picker id="dgShape" v-model="shape" :items="shapes" :aria-label="t('dg.shape')" :disabled="!editing" /></div>
    <div class="field"><label for="dgNote">{{ t('dg.note') }}</label>
      <textarea v-model="note" id="dgNote" rows="5" :placeholder="t('dg.notePh')" :disabled="!editing"></textarea></div>
    <div v-if="editing" class="act-row">
      <button class="btn btn-solid btn-sm" id="dgSave" @click="actions.saveStep(node.key, { label, shape, note })">{{
        t('common.save') }}</button>
      <button class="btn btn-danger btn-sm" id="dgDel" @click="actions.deleteStep(node.key)">{{ t('dg.deleteStep') }}</button>
    </div>
  </div>
</template>
