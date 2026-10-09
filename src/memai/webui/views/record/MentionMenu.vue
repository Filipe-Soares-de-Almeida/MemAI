<script setup lang="ts">
/* The items a task's text can mention, under the field typed in: # or @ in a [data-mention] field writes
   [[#id]]; any text in a data-mention="bare" field offers them and hands the choice back. */
import { computed, nextTick, onBeforeUnmount, reactive, watch } from 'vue';
import { t } from '../../i18n.ts';
import type { TaskItem } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import { MARK } from './checklist.ts';
import { findTrigger, insertToken, matchItems } from './mention.ts';

type Field = HTMLTextAreaElement | HTMLInputElement;

const props = defineProps<{ host: HTMLElement | null; items: TaskItem[] }>();
const emit = defineEmits<{ chose: [id: number] }>();

const uid = `tkm-${Math.random().toString(36).slice(2, 9)}`;
const state = reactive({ field: null as Field | null, start: 0, query: '', active: 0, picked: false,
                         top: 0, left: 0, width: 0 });

const shown = computed(() => (state.field ? matchItems(props.items, state.query) : []));
const optionId = (at: number) => `${uid}-${at}`;

const isBare = (el: Field) => el.dataset.mention === 'bare';
const fieldOf = (target: EventTarget | null): Field | null => {
  const el = target as Field | null;
  return el?.matches?.('[data-mention]') && el.closest('[data-mention-host]') === props.host ? el : null;
};

function mark(el: Field | null, open: boolean) {
  if (!el) return;
  el.setAttribute('aria-autocomplete', 'list');
  el.setAttribute('aria-expanded', String(open));
  if (open) {
    el.setAttribute('aria-controls', uid);
    el.setAttribute('aria-activedescendant', optionId(state.active));
  } else {
    el.removeAttribute('aria-controls');
    el.removeAttribute('aria-activedescendant');
  }
}

function close() {
  const el = state.field;
  state.field = null;
  mark(el, false);
}

function place(el: Field) {
  const box = el.getBoundingClientRect();
  state.top = box.bottom + 4;
  state.left = box.left;
  state.width = box.width;
}

function read(el: Field) {
  const caret = el.selectionStart ?? el.value.length;
  const found = isBare(el) ? { start: 0, query: el.value } : findTrigger(el.value, caret);
  if (!found || !matchItems(props.items, found.query).length) { close(); return; }
  state.field = el;
  state.start = found.start;
  state.query = found.query;
  state.active = 0;
  state.picked = false;
  place(el);
  nextTick(() => mark(el, Boolean(state.field)));
}

function choose(at: number) {
  const el = state.field;
  const picked = shown.value[at];
  if (!el || !picked) return;
  close();
  if (isBare(el)) {
    el.value = '';
    el.dispatchEvent(new Event('input', { bubbles: true }));
    emit('chose', picked.id);
  } else {
    insertToken(el, state.start, el.selectionStart ?? el.value.length, picked.id);
  }
}

function onInput(e: Event) {
  const el = fieldOf(e.target);
  if (el && !(e as InputEvent).isComposing) read(el);
}

/* Captured on the document, so an open menu reads its keys before the editor. Enter chooses only after an
   arrow or the pointer, so "#2" and Enter stay text; Ctrl, Cmd, Alt and Shift+Tab close it and pass on. */
function onKey(e: KeyboardEvent) {
  const el = state.field;
  if (!el || e.target !== el || e.isComposing) return;
  if (e.ctrlKey || e.metaKey || e.altKey || (e.key === 'Tab' && e.shiftKey)) { close(); return; }
  const bare = isBare(el);
  const count = shown.value.length;
  const move = (step: number) => {
    state.active = (state.active + step + count) % count;
    state.picked = true;
    mark(el, true);
  };
  if (e.key === 'ArrowDown') move(1);
  else if (e.key === 'ArrowUp') move(-1);
  else if (e.key === 'Enter' && (bare || state.picked)) choose(state.active);
  else if (e.key === 'Tab' && (!bare || state.picked || el.value.trim())) choose(state.active);
  else if (e.key === 'Escape') close();
  else {
    if (e.key === 'Enter' || e.key === 'Tab') close();
    return;
  }
  e.preventDefault();
  e.stopPropagation();
}

function onLeave(e: FocusEvent) {
  if (fieldOf(e.target) === state.field) close();
}

function onFocus(e: FocusEvent) {
  const el = fieldOf(e.target);
  if (el && isBare(el)) read(el);
}

let bound: HTMLElement | null = null;
function bind(host: HTMLElement | null) {
  bound?.removeEventListener('input', onInput);
  bound?.removeEventListener('focusout', onLeave);
  bound?.removeEventListener('focusin', onFocus);
  bound?.removeAttribute('data-mention-host');
  bound = host;
  host?.setAttribute('data-mention-host', '');
  host?.addEventListener('input', onInput);
  host?.addEventListener('focusout', onLeave);
  host?.addEventListener('focusin', onFocus);
}
watch(() => props.host, bind, { immediate: true });
document.addEventListener('keydown', onKey, true);
onBeforeUnmount(() => { bind(null); document.removeEventListener('keydown', onKey, true); });
</script>

<template>
  <Teleport to="body">
    <div v-if="state.field && shown.length" :id="uid" class="ctx-menu tk-mention" role="listbox" data-mention-menu
         :aria-label="t('task.mention.menu')"
         :style="{ top: `${state.top}px`, left: `${state.left}px`, minWidth: `${Math.min(state.width, 320)}px` }">
      <div v-for="(i, at) in shown" :id="optionId(at)" :key="i.id" class="ctx-item tk-mention-opt" role="option"
           :aria-selected="at === state.active" :class="{ 'is-active': at === state.active }"
           @mousedown.prevent @click="choose(at)" @mousemove="state.active = at; state.picked = true"><span class="tk-dep-mark"
           :data-s="i.state" aria-hidden="true"><span class="tk-ring"><AppIcon v-if="MARK[i.state]"
           :name="MARK[i.state]" /></span></span>{{ i.n }} · {{ i.text }}</div>
    </div>
  </Teleport>
</template>
