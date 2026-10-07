<script setup lang="ts">
/* A pin as its pushpin: filled for every domain, stuck into a base line for one; nothing unpinned. */
import { computed } from 'vue';
import { PIN } from '../core/shared.js';
import { icon } from '../core/icons.js';

const props = defineProps<{ pin: string }>();
const meta = computed(() => PIN[props.pin]);
</script>

<!-- v-html carries only the SVG strings core/icons.js builds. -->
<template>
  <span v-if="meta" class="pin-mark" :class="`pin-${pin}`" role="img" :title="meta.label"
        :aria-label="meta.label" v-html="icon(meta.icon)"></span>
</template>
