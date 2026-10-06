<script setup lang="ts">
/* A core/icons.js icon as an inline, decorative <svg>, for markup that sets it beside text. */
import { computed } from 'vue';
import { iconParts } from '../core/icons.js';

const props = withDefaults(defineProps<{ name: string; cls?: string }>(), { cls: '' });
const parts = computed(() => iconParts(props.name));
</script>

<!-- v-html carries only the path data core/icons.js defines. -->
<template>
  <svg v-if="parts" class="ico" :class="cls || undefined" :viewBox="parts.viewBox" aria-hidden="true" v-html="parts.body"></svg>
</template>
