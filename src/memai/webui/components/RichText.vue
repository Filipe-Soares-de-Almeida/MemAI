<script setup lang="ts">
/* A memory body drawn by core/richtext: [[uid]] opens its record, [[#id]] its item through `onItem`, code
   blocks copy and colour; `prose` adds the reading measure, `highlight` off leaves code plain. */
import { computed, onMounted, ref, watch } from 'vue';
import { renderRich, wireRich } from '../core/richtext.js';
import { highlightIn } from '../core/highlight.js';
import { copyCode } from '../core/copy.ts';
import { openRecord } from '../core/nav.ts';
import type { BodyLink, ItemRef } from '../api/types.ts';

const props = withDefaults(defineProps<{ text: string; links?: Record<string, BodyLink>; prose?: boolean;
                                         highlight?: boolean; items?: Record<string, ItemRef>;
                                         onItem?: (id: number) => void }>(),
                           { links: undefined, prose: true, highlight: true, items: undefined, onItem: undefined });

const el = ref<HTMLElement | null>(null);
const html = computed(() => renderRich(props.text, props.links, props.items));

/* v-html swaps every node when the markup changes, so each new set is wired once */
function wire() {
  if (!el.value) return;
  wireRich(el.value, { open: openRecord, copy: copyCode, item: (id: number) => props.onItem?.(id) });
  if (props.highlight) highlightIn(el.value).catch(() => {});
}
onMounted(wire);
watch(html, wire, { flush: 'post' });
</script>

<!-- v-html carries core/richtext's output, which escapes the body before it adds any tag. -->
<template>
  <div ref="el" class="rt" :class="{ 'content-prose': prose }" v-html="html"></div>
</template>
