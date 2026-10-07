<script setup lang="ts">
/* A menu of actions at a point or under its button: no scrim and no trap, so whatever comes next
   dismisses it. Dropped from a button it takes the keyboard, and Escape or Tab hands focus back. */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

export interface MenuItem {
  label?: string;
  run?: () => void;
  danger?: boolean;
  /* shown but not runnable: the visible reason, and the entry's description */
  note?: string;
  sep?: boolean;
}

const props = defineProps<{
  items: Array<MenuItem | null | false | undefined>;
  x?: number;
  y?: number;
  btn?: HTMLElement | null;
  align?: 'left' | 'right';
}>();
const emit = defineEmits<{ close: [] }>();

const live = computed(() => props.items.filter((i): i is MenuItem => !!i));
const el = ref<HTMLElement | null>(null);
/* where focus returns: the button that opened it, else whatever held it before */
const prior = document.activeElement;

const holds = () => !!el.value?.contains(document.activeElement);
const restore = () => [props.btn, prior]
  .find((n): n is HTMLElement => n instanceof HTMLElement && n !== document.body && document.contains(n))
  ?.focus();
const entries = () => [...(el.value?.querySelectorAll<HTMLElement>('.ctx-item') ?? [])];

function choose(item: MenuItem) {
  if (item.note) return;
  const held = holds();
  emit('close');
  if (held) restore();
  item.run?.();
}

const away = (e: Event) => { if (!el.value?.contains(e.target as Node)) emit('close'); };
const wheel = () => {
  const held = holds();
  emit('close');
  if (held) restore();
};
const key = (e: KeyboardEvent) => {
  if (e.key === 'Escape') {
    const held = holds();
    emit('close');
    if (held || props.btn) restore();
    return;
  }
  if (!props.btn) return;
  const list = entries();
  const i = list.indexOf(document.activeElement as HTMLElement);
  const to = ({ ArrowDown: i + 1, ArrowUp: i < 0 ? list.length - 1 : i - 1,
                Home: 0, End: list.length - 1 } as Record<string, number>)[e.key];
  if (to !== undefined) {
    e.preventDefault();
    list[(to + list.length) % list.length]?.focus();
  } else if (e.key === 'Tab') {
    e.preventDefault();
    emit('close');
    restore();
  }
};

/* Measured once in the document, then clamped both ends. A dropped menu sits below its button
   unless there is more room above, 4px off, since it lists actions and not the control's value. */
function place(menu: HTMLElement) {
  const box = menu.getBoundingClientRect();
  let at = { x: props.x ?? 0, y: props.y ?? 0 };
  if (props.btn) {
    const r = props.btn.getBoundingClientRect();
    const room = innerHeight - r.bottom - 8;
    const below = box.height <= room || r.top - 8 < room;
    at = { x: props.align === 'right' ? r.right - box.width : r.left,
           y: below ? r.bottom + 4 : r.top - box.height - 4 };
  }
  menu.style.left = `${Math.max(8, Math.min(at.x, innerWidth - box.width - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(at.y, innerHeight - box.height - 8))}px`;
}

onMounted(() => {
  if (!el.value) return;
  place(el.value);
  addEventListener('mousedown', away, true);
  addEventListener('keydown', key, true);
  addEventListener('wheel', wheel, true);
  if (props.btn) entries()[0]?.focus();
});
onBeforeUnmount(() => {
  removeEventListener('mousedown', away, true);
  removeEventListener('keydown', key, true);
  removeEventListener('wheel', wheel, true);
});
</script>

<template>
  <Teleport to="body">
    <div ref="el" class="ctx-menu">
      <template v-for="(item, i) in live" :key="i">
        <div v-if="item.sep" class="ctx-sep"></div>
        <button v-else-if="item.note" class="ctx-item" :class="{ danger: item.danger }" :data-i="i"
                aria-disabled="true" :aria-labelledby="`ctxL${i}`" :aria-describedby="`ctxN${i}`"
                :title="item.note" @click="choose(item)"><span :id="`ctxL${i}`">{{ item.label }}</span><span
                class="ctx-note" :id="`ctxN${i}`">{{ item.note }}</span></button>
        <button v-else class="ctx-item" :class="{ danger: item.danger }" :data-i="i"
                @click="choose(item)">{{ item.label }}</button>
      </template>
    </div>
  </Teleport>
</template>
