<script setup lang="ts">
/* A confidence state as its ringed mark and label; `compact` keeps only the mark, labelled by title. */
import { computed } from 'vue';
import { CONF } from '../core/shared.js';
import { esc } from '../core/dom.ts';
import { icon } from '../core/icons.js';

const props = withDefaults(defineProps<{ confidence: string; compact?: boolean }>(), { compact: false });
const meta = computed(() => CONF[props.confidence]);
const label = computed(() => meta.value ? meta.value.label : props.confidence);
const inner = computed(() => (meta.value ? icon(meta.value.icon) : '') + (props.compact ? '' : esc(label.value)));
</script>

<!-- v-html carries the SVG core/icons.js builds and the escaped label. -->
<template>
  <span class="conf-pill" :class="[`c-${confidence}`, { compact }]" :title="compact ? label : undefined"
        v-html="inner"></span>
</template>
