<script setup lang="ts">
/* The dashboard's listbox picker (core/pick.js) placed in a Vue template; `pick` reports a choice. */
import { onMounted, ref, watch } from 'vue';
import { fixedItems, pickerFor, setPickerValue, wirePicker } from '../core/pick.js';

export interface PickItem { value: string; label: string; html?: string; title?: string }

const props = defineProps<{ id: string; value: string; items: PickItem[]; ariaLabel?: string }>();
const emit = defineEmits<{ pick: [value: string] }>();
const host = ref<HTMLElement | null>(null);

onMounted(() => {
  const el = host.value;
  if (!el) return;
  el.innerHTML = pickerFor({ id: props.id, value: props.value, items: props.items,
                             ariaLabel: props.ariaLabel ?? '' });
  wirePicker(el, { id: props.id, items: (q: string) => fixedItems(props.items)(q),
                   onPick: (value: string) => emit('pick', value) });
});

watch(() => props.value, value => {
  const item = props.items.find(i => i.value === value);
  if (item) setPickerValue(host.value?.querySelector(`#${props.id}`) ?? null, item);
});
</script>

<template>
  <span ref="host" style="display: contents"></span>
</template>
