<script setup lang="ts">
/* A toolbar control under its name (.tb-labeled), or, with no label, level with the named ones. */
withDefaults(defineProps<{ label?: string; cls?: string }>(), { label: undefined, cls: '' });
</script>

<template>
  <div v-if="label !== undefined" class="tb-field" :class="cls || undefined"><span class="mg-label"
       aria-hidden="true">{{ label }}</span><slot /></div>
  <div v-else class="tb-bare"><slot /></div>
</template>
