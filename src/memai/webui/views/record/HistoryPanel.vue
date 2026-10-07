<script setup lang="ts">
/* The edit history, newest first; a version that changed the text opens its line diff on demand. */
import { computed, reactive, watch } from 'vue';
import { fmtDate } from '../../core/dom.ts';
import { t } from '../../i18n.ts';
import type { EditEntry } from '../../api/types.ts';
import { diffLines } from './record.ts';

const props = defineProps<{ history: EditEntry[] }>();

const rows = computed(() => props.history.slice().reverse());
const shown = reactive(new Set<number>());
watch(() => props.history, () => shown.clear());

const changed = (e: EditEntry) => e.prev_content !== e.new_content;
const toggle = (i: number) => { if (!shown.delete(i)) shown.add(i); };
</script>

<template>
  <div class="rs-field" data-rs="history">
    <div class="rs-field-head"><span class="mg-label">{{ t('dr.history') }}</span><span class="rs-n">{{
      history.length }}</span></div>
    <div v-for="(e, i) in rows" :key="e.id" class="rs-hist">
      <span class="rs-hist-when" :title="e.edited_at">{{ fmtDate(e.edited_at) }}</span> <span class="rs-hist-note">{{
        e.note || (changed(e) ? t('dr.hist.contentEdited') : t('dr.hist.entry')) }}</span>
      <template v-if="changed(e)">
        <button type="button" class="rs-hist-diff" :data-diff="i" :aria-expanded="shown.has(i)"
                :aria-controls="`histDiff${i}`" @click="toggle(i)">{{
          shown.has(i) ? t('dr.hist.hide') : t('dr.hist.show') }}</button>
        <div :id="`histDiff${i}`" class="hist-diff" :data-diffbody="i" :hidden="!shown.has(i)"><template
             v-if="shown.has(i)"><span v-for="(l, k) in diffLines(e.prev_content, e.new_content)" :key="k"
             :class="l.cls">{{ l.text }}</span></template></div>
      </template>
    </div>
    <div v-if="!rows.length" class="hint-sm">{{ t('dr.hist.empty') }}</div>
  </div>
</template>
