<script setup lang="ts">
/* A new step, or an existing one retyped: its key (fixed once it exists), shape and label. */
import { ref } from 'vue';
import { NODE_SHAPES } from '../../engines/diagram-engine.ts';
import { t } from '../../i18n.ts';
import type { I18nKey } from '../../i18n.ts';
import AppModal from '../../components/AppModal.vue';
import Picker from '../../components/Picker.vue';

export interface StepFields { key: string; label: string; shape: string }

const props = withDefaults(defineProps<{ title: string; stepKey?: string; label?: string; shape?: string;
                                        lockKey?: boolean }>(),
                           { stepKey: '', label: '', shape: 'step', lockKey: false });
const emit = defineEmits<{ done: [step: StepFields | null] }>();

const shapes = (NODE_SHAPES as readonly string[]).map(s => ({ value: s, label: t(`dg.shape.${s}` as I18nKey) }));
const key = ref(props.stepKey);
const label = ref(props.label);
const shape = ref(props.shape);

const save = () => emit('done', { key: (props.lockKey ? props.stepKey : key.value).trim(), label: label.value.trim(),
                                  shape: shape.value });
</script>

<template>
  <AppModal :title="title" @close="emit('done', null)">
    <div class="field-pair">
      <div class="field"><label for="dgsKey">{{ t('dg.key') }}</label>
        <input v-model="key" type="text" id="dgsKey" :placeholder="t('dg.keyPh')" :disabled="lockKey"
               autocomplete="off"></div>
      <div class="field"><label for="dgsShape">{{ t('dg.shape') }}</label>
        <Picker id="dgsShape" v-model="shape" :items="shapes" :aria-label="t('dg.shape')" /></div>
    </div>
    <div class="field"><label for="dgsLabel">{{ t('dg.label') }}</label>
      <input v-model="label" type="text" id="dgsLabel" :placeholder="t('dg.labelPh')"></div>
    <div class="dg-empty">{{ t('dg.labelHint') }}</div>
    <template #foot>
      <button class="btn" data-x @click="emit('done', null)">{{ t('common.cancel') }}</button>
      <button class="btn btn-solid" data-ok @click="save">{{ t('common.save') }}</button>
    </template>
  </AppModal>
</template>
