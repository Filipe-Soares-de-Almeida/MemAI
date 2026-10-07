<script setup lang="ts">
/* A core/icons.js icon as an inline <svg>: decorative beside text, or named by `title` when it is a
   control's only content. */
import { computed } from 'vue';
import { iconParts } from '../core/icons.js';

const props = withDefaults(defineProps<{ name: string; cls?: string; title?: string }>(), { cls: '', title: '' });
const parts = computed(() => iconParts(props.name));
</script>

<!-- v-html carries only the path data core/icons.js defines. -->
<template>
  <svg v-if="parts" class="ico" :class="cls || undefined" :viewBox="parts.viewBox" :role="title ? 'img' : undefined"
       :aria-label="title || undefined" :aria-hidden="title ? undefined : 'true'" v-html="parts.body"></svg>
</template>
