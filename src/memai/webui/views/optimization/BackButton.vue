<script setup lang="ts">
/* The way out of a level, as the same control the record's bar carries. */
import AppIcon from '../../components/AppIcon.vue';

defineProps<{ label: string }>();
</script>

<template>
  <button id="optBack" type="button" class="btn btn-sm opt-back"><AppIcon name="chevron-left" /><span
          class="opt-back-text">{{ label }}</span></button>
</template>
