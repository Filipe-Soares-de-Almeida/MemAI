<script setup lang="ts">
/* A brief's DEPENDS ON as chips: each item it waits for with an optional reason, added from the items
   the note neither applies to nor already waits for. */
import { computed } from 'vue';
import { t } from '../../i18n.ts';
import AppIcon from '../../components/AppIcon.vue';
import { MARK } from './checklist.ts';
import type { Checklist, NoteDraft } from './checklist.ts';

const props = defineProps<{ c: Checklist; draft: NoteDraft; label: string; issue: string; issueId: string }>();
const { current } = props.c;

const numbered = computed(() => current.value.items.map((i, at) => ({ ...i, n: at + 1 })));
const rows = computed(() => props.draft.depends.map(d => ({
  d, it: d.item == null ? null : numbered.value.find(i => i.id === d.item) ?? null,
})));
const eligible = computed(() => numbered.value.filter(i => !props.draft.items.includes(i.id)
  && !props.draft.depends.some(d => d.item === i.id)));

function add(e: Event) {
  const box = e.target as HTMLSelectElement;
  const id = Number(box.value);
  box.value = '';
  if (id) props.draft.depends.push({ item: id, text: '', reason: '' });
}

const remove = (at: number) => { props.draft.depends.splice(at, 1); };
</script>

<template>
  <div class="tk-brief-in tk-deps-edit" role="group" :aria-label="label"
       :aria-describedby="issue ? issueId : undefined">
    <span class="rf-sub">{{ label }}</span>
    <p v-if="draft.legacy" class="tk-dep-legacy" data-dep-legacy>{{ t('task.depends.legacy', { text: draft.legacy }) }}</p>
    <ul v-if="rows.length" class="tk-deps">
      <li v-for="({ d, it }, at) in rows" :key="d.item ?? `gone-${at}`" class="tk-dep tk-dep-row" data-dep-row>
        <span v-if="it" class="tk-dep-chip is-static"><span class="tk-dep-mark" :data-s="it.state"
              aria-hidden="true"><span class="tk-ring"><AppIcon v-if="MARK[it.state]" :name="MARK[it.state]" /></span></span>{{
          it.n }} · {{ it.text }}</span>
        <span v-else class="tk-dep-gone"><s>{{ d.text || d.item }}</s><span class="sr-only"> ({{
          t('task.depends.deleted') }})</span></span>
        <input v-model="d.reason" class="tk-dep-why-box" type="text" data-dep-reason spellcheck="false"
               :placeholder="t('task.depends.reason')" :aria-label="t('task.depends.reason')">
        <button type="button" class="icon-btn danger tk-dep-x" data-dep-remove
                :aria-label="t('task.depends.remove', { text: it ? `${it.n} · ${it.text}` : d.text })"
                :title="t('task.depends.remove', { text: it ? `${it.n} · ${it.text}` : d.text })"
                @click="remove(at)"><AppIcon name="close" /></button>
      </li>
    </ul>
    <p v-else class="tk-dep-none">{{ t('task.depends.none') }}</p>
    <select v-if="eligible.length" class="pick tk-dep-add" data-dep-add :aria-label="t('task.depends.add')" @change="add">
      <option value="">{{ t('task.depends.add') }}</option>
      <option v-for="i in eligible" :key="i.id" :value="i.id">{{ i.n }} · {{ i.text }}</option>
    </select>
    <p v-if="issue" :id="issueId" class="field-error tk-brief-err" data-depends-error>{{ issue }}</p>
  </div>
</template>
