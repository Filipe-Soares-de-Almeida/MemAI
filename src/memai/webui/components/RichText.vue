<script setup lang="ts">
/* A memory body drawn by core/richtext: [[uid]] links open their record, code blocks copy and colour. */
import { computed, onMounted, ref, watch } from 'vue';
import { renderRich, wireRich } from '../core/richtext.js';
import { highlightIn } from '../core/highlight.js';
import { copyCode } from '../core/copy.ts';
import { openRecord } from '../core/nav.ts';
import type { BodyLink } from '../api/types.ts';

const props = defineProps<{ text: string; links?: Record<string, BodyLink> }>();

const el = ref<HTMLElement | null>(null);
const html = computed(() => renderRich(props.text, props.links));

/* v-html swaps every node when the markup changes, so each new set is wired once */
function wire() {
  if (!el.value) return;
  wireRich(el.value, { open: openRecord, copy: copyCode });
  highlightIn(el.value).catch(() => {});
}
onMounted(wire);
watch(html, wire, { flush: 'post' });
</script>

<!-- v-html carries core/richtext's output, which escapes the body before it adds any tag. -->
<template>
  <div ref="el" class="content-prose rt" v-html="html"></div>
</template>
