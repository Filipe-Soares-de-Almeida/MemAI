<script setup lang="ts">
/* A keyboard shortcut spelled out as keycaps, then what it does; `save` is Ctrl or ⌘ with Enter. */
import { computed } from 'vue';
import { MOD_KEY } from '../core/ui.js';

const props = withDefaults(defineProps<{ action: string; keys?: string[]; save?: boolean }>(),
                           { keys: () => [], save: false });
const caps = computed(() => (props.save ? [MOD_KEY, 'Enter'] : props.keys));
</script>

<template>
  <span class="key-hint"><template v-for="(k, i) in caps" :key="i"><span v-if="i" class="key-plus"
        aria-hidden="true">+</span><kbd>{{ k }}</kbd></template><span class="key-what">{{ action }}</span></span>
</template>
