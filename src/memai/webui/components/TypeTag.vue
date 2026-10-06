<script setup lang="ts">
/* A memory type as a chip: the dot is the type's fill and the name its ink. */
import { typeClass } from '../core/shared.js';

defineProps<{ type: string }>();
</script>

<template>
  <span class="type-tag" :class="typeClass(type) || undefined"><span class="dot"></span>{{ type }}</span>
</template>
