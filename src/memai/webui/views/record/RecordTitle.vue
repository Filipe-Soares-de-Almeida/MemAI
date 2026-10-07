<script setup lang="ts">
/* The record's name, edited where it is read: a box that grows with its text, saved on blur. */
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { failed, toast } from '../../core/ui.js';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import { useRefresh } from './record.ts';

const props = defineProps<{ uid: string; title: string }>();
const refresh = useRefresh();

const value = ref(props.title);
const box = ref<HTMLTextAreaElement | null>(null);
watch(() => props.title, now => { value.value = now; nextTick(grow); });

function grow() {
  const el = box.value;
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = `${el.scrollHeight}px`;
}

/* only a change of width measures again: setting the height fires the observer too */
let watcher: ResizeObserver | null = null;
onMounted(() => {
  const el = box.value;
  if (!el) return;
  grow();
  let width = el.clientWidth;
  watcher = new ResizeObserver(() => {
    if (el.clientWidth === width) return;
    width = el.clientWidth;
    grow();
  });
  watcher.observe(el);
});
onBeforeUnmount(() => watcher?.disconnect());

async function save() {
  const next = value.value.trim();
  if (next === props.title) return;
  /* the API refuses an empty title */
  if (!next) { value.value = props.title; return; }
  try {
    await client.memories.meta(props.uid, { title: next });
    toast(t('dr.titleUpdated'), 'ok');
    refresh();
  } catch (err) {
    value.value = props.title;
    failed('err.save', err);
  }
}

function key(e: KeyboardEvent) {
  if (e.key === 'Enter') { e.preventDefault(); box.value?.blur(); }
  if (e.key === 'Escape') { value.value = props.title; box.value?.blur(); }
}
</script>

<template>
  <h2 class="rec-title"><textarea id="dTitle" ref="box" v-model="value" rows="1"
      :placeholder="t('dr.title.placeholder')" :aria-label="t('mm.name.label')" spellcheck="false"
      @input="grow" @blur="save" @keydown="key"></textarea></h2>
</template>
