<script setup lang="ts">
/* A task note's editor: the record field's card without its preview; picking an item turns the body
   into the brief's fields, picking none turns it back. */
import { computed } from 'vue';
import { sectionLabel } from '../../core/shared.js';
import { t } from '../../i18n.ts';
import { MEMORY, TASK } from '../../contract.ts';
import type { TaskNote } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import KeyHint from '../../components/KeyHint.vue';
import { BRIEF } from './checklist.ts';
import type { Checklist } from './checklist.ts';

const props = withDefaults(defineProps<{ c: Checklist; note?: TaskNote | null; scope?: string }>(),
                           { note: null, scope: '' });
const { current, ui } = props.c;
const { NOTE_MAX } = TASK;

const id = computed(() => (props.note ? String(props.note.id) : `new:${props.scope}`));
const d = computed(() => ui.noteDraft ?? { title: '', body: '', items: [], brief: {} });
const issue = computed(() => props.c.dependsIssue());
const sum = computed(() => (d.value.items.length ? t('task.note.scope.n', { n: d.value.items.length })
  : t('task.note.scope.all')));

function key(e: KeyboardEvent) {
  if (e.key === 'Escape') { e.preventDefault(); props.c.leaveNote(); }
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); props.c.submitNote(); }
}
</script>

<template>
  <section class="rf is-open tk-note" :data-note-form="id" @keydown="key">
    <header class="rf-head">
      <input v-model="d.title" class="tk-note-title-box" data-note-field="title" :maxlength="MEMORY.TITLE_MAX"
             spellcheck="false" :aria-label="t('task.note.title')" :placeholder="t('task.note.title')">
    </header>
    <div class="rf-split rf-solo">
      <div class="rf-pane">
        <template v-if="d.items.length">
          <template v-for="f in BRIEF" :key="f.key">
            <label class="tk-brief-in">
              <span class="rf-sub">{{ sectionLabel('task_note', f) }}<span v-if="f.optional" class="tk-opt"> · {{
                t('task.note.optional') }}</span></span>
              <textarea v-model="d.brief[f.key]" :data-note-field="f.key" rows="3" spellcheck="false"
                        :aria-required="f.optional ? undefined : 'true'"
                        :placeholder="f.key === 'depends_on' ? t('task.depends.placeholder') : undefined"
                        :aria-invalid="f.key === 'depends_on' && issue ? 'true' : undefined"
                        :aria-describedby="f.key === 'depends_on' && issue ? `${id}-dep` : undefined"></textarea>
            </label>
            <p v-if="f.key === 'depends_on' && issue" :id="`${id}-dep`" class="field-error tk-brief-err"
               data-depends-error>{{ issue }}</p>
          </template>
        </template>
        <textarea v-else v-model="d.body" data-note-field="body" rows="12" spellcheck="false" :maxlength="NOTE_MAX"
                  :aria-label="t('task.note.body')" :placeholder="t('task.note.body')"></textarea>
        <div class="tk-note-foot">
          <span class="tk-hint"><KeyHint save :action="t('dr.key.save')" /><KeyHint :keys="['Esc']"
                :action="t('dr.key.close')" /></span>
          <span class="rf-count" :class="{ over: c.noteLength() > NOTE_MAX }" data-note-count>{{ t('dr.sections.count', { n: c.noteLength(), max: NOTE_MAX }) }}</span>
        </div>
      </div>
    </div>
    <div v-if="current.items.length" class="tk-scope">
      <div class="tk-scope-head">
        <span class="rf-sub">{{ t('task.note.scope') }}</span>
        <span class="tk-scope-sum" data-note-scope-sum>{{ sum }}</span>
      </div>
      <div class="tk-scope-list" role="group" :aria-label="t('task.note.scope.aria')">
        <label v-for="(i, at) in current.items" :key="i.key" class="tk-scope-row" :title="i.text"><input type="checkbox"
               :data-note-scope="i.key" :aria-label="`${at + 1}. ${i.text}`" :checked="d.items.includes(i.key)"
               @change="c.scopeNote(i.key, ($event.target as HTMLInputElement).checked)"> <span
               class="tk-scope-text"><span class="tk-scope-n"
               aria-hidden="true">{{ at + 1 }}</span>{{ i.text }}</span></label>
      </div>
    </div>
    <div class="rf-save">
      <button type="button" class="btn btn-solid btn-sm" :data-note-save="id" :disabled="!c.noteReady()"
              @click="c.submitNote()">{{ t('common.save') }}</button>
      <button type="button" class="btn btn-sm" data-note-cancel @click="c.leaveNote()">{{ t('common.cancel') }}</button>
      <button v-if="note" type="button" class="btn btn-sm btn-danger tk-note-del" :data-note-del="note.id"
              @click="c.deleteNote()"><AppIcon name="trash" />{{ t('task.note.delete') }}</button>
    </div>
  </section>
</template>
