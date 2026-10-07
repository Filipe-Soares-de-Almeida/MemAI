<script setup lang="ts">
/* A bar of segments sized as shares of their own total, with an optional legend of names and sizes. */
import { computed } from 'vue';
import { fmtBytes } from '../../core/dom.ts';
import { segmentWidths } from './maintenance.ts';
import type { Segment } from './maintenance.ts';

const props = withDefaults(defineProps<{ parts: Segment[]; legend?: boolean }>(), { legend: false });
const widths = computed(() => segmentWidths(props.parts));
</script>

<template>
  <div class="mnt-bar"><span v-for="(p, i) in parts" :key="i"
                             :style="{ width: widths[i], background: p.fill }"></span></div>
  <div v-if="legend" class="mnt-legend"><span v-for="(p, i) in parts" :key="i"><span class="dot"
    :style="{ '--c': p.fill }"></span>{{ p.name }} · {{ fmtBytes(p.value) }}</span></div>
</template>
