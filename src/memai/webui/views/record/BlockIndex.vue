<script setup lang="ts">
/* The blocks a memory is made of, as a column beside the one on screen; under the selected row, the
   headings its body opens, the only way through a long block. */
import { computed, nextTick, ref } from 'vue';
import { headings } from '../../core/richtext.js';
import { sectionHue } from '../../core/shared.js';
import { t } from '../../i18n.ts';
import BlockPick from './BlockPick.vue';
import type { Field } from './record.ts';

const props = defineProps<{ fields: Field[]; type: string; sel: number }>();
const emit = defineEmits<{ pick: [key: string]; mark: [index: number] }>();

const marks = computed(() => headings(props.fields[props.sel].text) as string[]);
const root = ref<HTMLElement | null>(null);

/* a tablist is walked with the arrows, and the tab picked takes the focus with it */
function walk(delta: number) {
  const to = props.sel + delta;
  if (to < 0 || to >= props.fields.length) return;
  emit('pick', props.fields[to].key);
  nextTick(() => root.value?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus());
}
</script>

<template>
  <div ref="root" class="rec-index" role="tablist" :aria-label="t('dr.blocks.aria')">
    <template v-for="(f, i) in fields" :key="f.key">
      <div v-if="i === sel && marks.length" class="rec-group" :style="sectionHue(type, f.key)">
        <BlockPick :field="f" :type="type" selected @pick="emit('pick', f.key)" @walk="walk" />
        <button v-for="(h, j) in marks" :key="j" type="button" class="rec-mark" :data-mark="j" :title="h"
                @click="emit('mark', j)"><span class="rec-bullet" aria-hidden="true"></span><span
                class="rec-mark-text">{{ h }}</span></button>
      </div>
      <BlockPick v-else :field="f" :type="type" :selected="i === sel" @pick="emit('pick', f.key)" @walk="walk" />
    </template>
  </div>
</template>
