<script setup lang="ts">
/* The listbox picker as a component, bound with v-model: the button is drawn here, the panel and
   its keyboard model come from core/pick.js, so every picker in the dashboard behaves the same. */
import { computed, onBeforeUnmount, ref } from 'vue';
import { closePicker, openPanel, pickerOpenOn } from '../core/pick.js';
import { esc } from '../core/dom.ts';

export interface PickItem {
  value: string;
  label: string;
  html?: string;
  title?: string;
  cls?: string;
  style?: string;
}

const props = withDefaults(defineProps<{
  id: string;
  modelValue: string;
  items: PickItem[] | ((query: string) => PickItem[]);
  ariaLabel?: string;
  cls?: string;
  disabled?: boolean;
  /* boolean before 'auto': Vue casts a bare `search` attribute to true only when Boolean leads String */
  search?: boolean | 'auto';
  minWidth?: number;
  panelCls?: string;
  /* an action picker keeps its verb on the button instead of showing what was picked */
  keepLabel?: boolean;
  anchor?: string;
  align?: 'left' | 'right';
  /* the button always shows the label, never a row's markup */
  plainFace?: boolean;
}>(), {
  ariaLabel: '', cls: '', disabled: false, search: 'auto', minWidth: 180, panelCls: '',
  keepLabel: false, anchor: '', align: 'left', plainFace: false,
});
const emit = defineEmits<{ 'update:modelValue': [value: string]; pick: [value: string, item: PickItem] }>();

const btn = ref<HTMLButtonElement | null>(null);
const list = (query: string): PickItem[] =>
  typeof props.items === 'function' ? props.items(query) : props.items.filter(it =>
    !query.trim() || (it.label || '').toLowerCase().includes(query.trim().toLowerCase()));

const face = computed<PickItem>(() =>
  list('').find(it => it.value === props.modelValue) || { value: props.modelValue, label: props.modelValue });
const faceHtml = computed(() =>
  !props.plainFace && face.value.html ? face.value.html : esc(face.value.label));

function open() {
  if (!btn.value) return;
  openPanel(btn.value, {
    items: list, search: props.search, minWidth: props.minWidth, panelCls: props.panelCls,
    keepLabel: true, anchor: props.anchor, align: props.align,
    onPick: (value: string, item: PickItem) => {
      if (!props.keepLabel) emit('update:modelValue', value);
      emit('pick', value, item);
    },
  });
}

function toggle() {
  if (btn.value && pickerOpenOn(btn.value)) closePicker();
  else open();
}

/* the two keys that open a listbox without choosing anything */
function onKey(e: KeyboardEvent) {
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
  e.preventDefault();
  if (btn.value && !pickerOpenOn(btn.value)) open();
}

onBeforeUnmount(() => { if (btn.value && pickerOpenOn(btn.value)) closePicker(); });

defineExpose({ button: btn });
</script>

<!-- faceHtml is a row's own markup, which the item lists build with their values escaped. -->
<template>
  <button ref="btn" type="button" class="pick" :class="cls || undefined" :id="id" :data-v="face.value"
          aria-haspopup="listbox" aria-expanded="false" :disabled="disabled"
          :aria-label="ariaLabel || face.label" :title="face.title || face.label"
          @click="toggle" @keydown="onKey"><span class="pick-value" v-html="faceHtml"></span></button>
</template>
