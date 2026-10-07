<script setup lang="ts">
/* One row of the block index: the block's name and the opening of its text, as a tab. */
import { sectionHue } from '../../core/shared.js';
import type { Field } from './record.ts';
import { peekOf } from './record.ts';

defineProps<{ field: Field; type: string; selected: boolean }>();
const emit = defineEmits<{ pick: []; walk: [delta: number] }>();
</script>

<template>
  <button type="button" class="rec-pick" role="tab" :data-pick="field.key" :style="sectionHue(type, field.key)"
          :aria-selected="selected" :tabindex="selected ? 0 : -1" @click="emit('pick')"
          @keydown.down.prevent="emit('walk', 1)" @keydown.up.prevent="emit('walk', -1)">
    <span class="rf-dot" aria-hidden="true"></span>
    <span class="rec-pick-text">
      <span class="rec-pick-name"><span v-if="field.raw !== undefined" class="sec-label-text"
            :title="field.raw">{{ field.label }}</span><template v-else>{{ field.label }}</template></span>
      <span class="rec-peek">{{ peekOf(field.text) }}</span>
    </span>
  </button>
</template>
