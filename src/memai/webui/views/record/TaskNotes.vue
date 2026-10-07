<script setup lang="ts">
/* The notes on one scope -- the whole task, or one item -- drawn as record fields, with the editor in
   place of the note it edits. */
import { computed } from 'vue';
import { t } from '../../i18n.ts';
import type { TaskNote } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import RichText from '../../components/RichText.vue';
import TaskNoteForm from './TaskNoteForm.vue';
import { notePeek } from './checklist.ts';
import type { Checklist } from './checklist.ts';

const props = defineProps<{ c: Checklist; scope: string; notes: TaskNote[] }>();
const { ui } = props.c;

const writing = computed(() => ui.noteEdit === `new:${props.scope}`);
</script>

<template>
  <div class="tk-sub">
    <h3 class="tk-sub-h">{{ t('task.notes') }}<span class="rs-n">{{ notes.length }}</span></h3>
    <button type="button" class="rs-more" :data-note-add="scope" @click="c.editNote(`new:${scope}`)">{{
      t('task.note.add') }}</button>
  </div>
  <div class="tk-notes">
    <template v-for="n in notes" :key="n.id">
      <TaskNoteForm v-if="String(n.id) === ui.noteEdit" :c="c" :note="n" />
      <article v-else class="rf tk-note" :class="{ 'is-shut': !c.noteOpen(n.id, notes.length) }">
        <header class="rf-head">
          <button type="button" class="tk-note-toggle" :data-note-open="n.id"
                  :aria-expanded="c.noteOpen(n.id, notes.length)"
                  :aria-controls="c.noteOpen(n.id, notes.length) ? `tkn-${n.id}` : undefined"
                  @click="c.toggleNote(n.id, c.noteOpen(n.id, notes.length))">
            <span class="tk-chev" aria-hidden="true"><AppIcon name="chevron-right" /></span>
            <span class="tk-note-name"><span class="tk-note-title">{{ n.title }}</span> <span
                  v-if="!c.noteOpen(n.id, notes.length)" class="tk-note-peek">{{ notePeek(n.body) }}</span></span>
          </button>
          <button type="button" class="rf-edit" :data-note-edit="n.id" :aria-label="t('task.note.edit')"
                  @click="c.editNote(String(n.id))"><AppIcon name="pencil" />{{ t('common.edit') }}</button>
        </header>
        <div v-if="c.noteOpen(n.id, notes.length)" :id="`tkn-${n.id}`" class="rf-body"><RichText :text="n.body"
             :links="n.body_links" /></div>
      </article>
    </template>
    <div v-if="!notes.length && !writing" class="hint-sm">{{ t('task.notes.empty') }}</div>
    <TaskNoteForm v-if="writing" :c="c" :scope="scope" />
  </div>
</template>
