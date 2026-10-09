<script setup lang="ts">
/* The notes on one scope -- the whole task, or one item -- drawn as record fields, with the editor in
   place of the note it edits. */
import { computed } from 'vue';
import { t } from '../../i18n.ts';
import type { I18nKey } from '../../i18n.ts';
import type { TaskNote } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import RichText from '../../components/RichText.vue';
import TaskNoteForm from './TaskNoteForm.vue';
import { MARK, briefOf, notePeek } from './checklist.ts';
import type { Checklist } from './checklist.ts';

const props = defineProps<{ c: Checklist; scope: string; notes: TaskNote[] }>();
const { current, ui } = props.c;

const itemOf = (id: number) => {
  const at = current.value.items.findIndex(i => i.id === id);
  if (at < 0) return null;
  const { text, state } = current.value.items[at];
  return { n: at + 1, text, state, stateName: t(`task.state.${state}` as I18nKey) };
};

const resolved = (depends: TaskNote['depends']) => (depends ?? []).map(d => ({ d, it: d.item != null ? itemOf(d.item) : null }));

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
                  v-if="!c.noteOpen(n.id, notes.length)" class="tk-note-peek">{{ notePeek(n.brief?.goal ?? n.body) }}</span></span>
          </button>
          <button type="button" class="rf-edit" :data-note-edit="n.id" :aria-label="t('task.note.edit')"
                  @click="c.editNote(String(n.id))"><AppIcon name="pencil" />{{ t('common.edit') }}</button>
        </header>
        <div v-if="c.noteOpen(n.id, notes.length)" :id="`tkn-${n.id}`" class="rf-body">
          <template v-if="n.brief">
            <section v-for="f in briefOf(n.brief)" :key="f.key" class="tk-brief-f" :data-brief="f.key">
              <h4 class="tk-brief-h rf-sub" :title="f.raw">{{ f.label }}</h4>
              <ul v-if="f.key === 'depends_on' && n.depends" class="tk-deps">
                <li v-if="!n.depends.length" class="tk-dep-none">{{ t('task.depends.none') }}</li>
                <li v-for="({ d, it }, at) in resolved(n.depends)" :key="at" class="tk-dep">
                  <template v-if="d.item != null">
                    <button v-if="it" type="button" class="tk-dep-chip" :data-dep="d.item"
                            :aria-label="t('task.depends.open', { n: it.n, text: it.text, state: it.stateName })"
                            @click="c.flashItem(d.item)"><span class="tk-dep-mark" :data-s="it.state"
                            aria-hidden="true"><span class="tk-ring"><AppIcon v-if="MARK[it.state]"
                            :name="MARK[it.state]" /></span></span>{{ it.n }} · {{ it.text }}</button>
                    <span v-else class="tk-dep-key">{{ d.item }}</span>
                  </template>
                  <span v-else class="tk-dep-gone"><s>{{ d.text }}</s><span class="sr-only"> ({{
                    t('task.depends.deleted') }})</span></span>
                  <span v-if="d.reason" class="tk-dep-why">{{ d.reason }}</span>
                </li>
              </ul>
              <RichText v-else :text="f.text" :links="n.body_links" :items="current.refs" :on-item="c.flashItem" />
            </section>
          </template>
          <RichText v-else :text="n.body" :links="n.body_links" :items="current.refs" :on-item="c.flashItem" />
        </div>
      </article>
    </template>
    <div v-if="!notes.length && !writing" class="hint-sm">{{ t('task.notes.empty') }}</div>
    <TaskNoteForm v-if="writing" :c="c" :scope="scope" />
  </div>
</template>
