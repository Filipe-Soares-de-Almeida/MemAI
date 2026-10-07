<script setup lang="ts">
/* A relation type: a picker of the suggested types, whose "other" entry opens a free-text field.
   The model is the type to send, '' until one is chosen. */
import { computed, nextTick, ref } from 'vue';
import { REL_OTHER, relItems } from '../core/shared.js';
import { t } from '../i18n.ts';
import Picker from './Picker.vue';

const props = withDefaults(defineProps<{ id: string; customId: string; options: string[]; modelValue: string;
                                        ariaLabel?: string }>(), { ariaLabel: '' });
const emit = defineEmits<{ 'update:modelValue': [value: string] }>();

const items = relItems(props.options);
const known = !props.modelValue || props.options.includes(props.modelValue);
const picked = ref(known ? props.modelValue : REL_OTHER);
const custom = ref(known ? '' : props.modelValue);
const other = computed(() => picked.value === REL_OTHER);
const field = ref<HTMLInputElement | null>(null);

const report = () => emit('update:modelValue', other.value ? custom.value.trim() : picked.value);

async function pick(value: string) {
  picked.value = value;
  report();
  if (value === REL_OTHER) { await nextTick(); field.value?.focus(); }
}
</script>

<template>
  <Picker :id="id" :model-value="picked" :items="items" :aria-label="ariaLabel" cls="rel-type-sel" @pick="pick" />
  <input :id="customId" ref="field" :value="custom" type="text" class="rel-type-custom" :hidden="!other"
         :placeholder="t('dr.rel.type.customPlaceholder')" :aria-label="t('dr.rel.type.customPlaceholder')"
         autocomplete="off" @input="custom = ($event.target as HTMLInputElement).value; report()">
</template>
