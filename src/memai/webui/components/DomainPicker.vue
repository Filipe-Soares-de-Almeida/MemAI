<script setup lang="ts">
/* The domain filter: a Picker over the domain tree with the Domains table's rails, the filter
   field always on, and '' meaning `anyLabel` ("All domains" unless told otherwise). */
import { computed } from 'vue';
import Picker from './Picker.vue';
import { domainRows } from '../core/domain-picker.js';
import { t } from '../i18n.ts';

const props = withDefaults(defineProps<{
  id: string;
  modelValue: string;
  domains: Array<{ domain: string; implicit?: boolean }>;
  ariaLabel?: string;
  anyLabel?: string;
  cls?: string;
}>(), { ariaLabel: '', anyLabel: '', cls: '' });
const emit = defineEmits<{ 'update:modelValue': [value: string] }>();

const none = computed(() => props.anyLabel || t('common.allDomains'));
const rows = (query: string) => domainRows(props.domains, query, props.anyLabel);
</script>

<template>
  <Picker :id="id" :model-value="modelValue" :items="rows" :aria-label="ariaLabel || none" :cls="cls"
          search :min-width="280" plain-face @update:model-value="emit('update:modelValue', $event)" />
</template>
