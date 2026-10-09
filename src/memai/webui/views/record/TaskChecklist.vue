<script setup lang="ts">
/* A task's checklist: its goal, the items, the notes on the whole task, and the thread. */
import { computed, watch } from 'vue';
import { fmtDate } from '../../core/dom.ts';
import { t } from '../../i18n.ts';
import type { I18nKey } from '../../i18n.ts';
import type { TaskAnswer, TaskRecord } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import TaskComment from './TaskComment.vue';
import TaskComposer from './TaskComposer.vue';
import TaskGoal from './TaskGoal.vue';
import TaskItemRow from './TaskItemRow.vue';
import TaskNotes from './TaskNotes.vue';
import { OLDER_SHOWN, useChecklist } from './checklist.ts';

const props = defineProps<{ uid: string; task: TaskRecord; status: string }>();
const emit = defineEmits<{ status: [status: string]; write: [answer: TaskAnswer] }>();

const c = useChecklist(props.uid, props.task, props.status, {
  onStatus: status => emit('status', status),
  onWrite: answer => emit('write', answer),
});
const { current, ui, enter, host } = c;
watch(() => props.task, task => c.sync(task));

const closed = computed(() => current.value.state !== 'open');
const done = computed(() => current.value.state === 'completed');
const thread = computed(() => current.value.comments.filter(cm => !cm.item));
const adding = computed({
  get: () => ui.drafts.get('+items') || '',
  set: value => { ui.drafts.set('+items', value); },
});
const hidden = computed(() => (ui.allComments ? 0 : Math.max(0, thread.value.length - OLDER_SHOWN)));
</script>

<template>
  <div ref="host" class="tk">
    <div class="tk-head tk-card" :class="{ 'is-closed': closed }">
      <div v-if="closed" class="tk-closed" :class="[done ? 'is-completed' : 'is-cancelled', { 'is-new': enter.closed }]"
           role="status">
        <span class="tk-closed-mark"><AppIcon :name="done ? 'check' : 'minus'" /></span>
        <span class="tk-closed-text"><b>{{ t(`task.state.${current.state}` as I18nKey) }}</b> <span
              v-if="current.completed_at" class="tk-closed-when" :title="current.completed_at">{{
              fmtDate(current.completed_at) }}</span> <span>{{ t(`task.closed.${current.state}` as I18nKey) }}</span></span>
      </div>
      <TaskGoal :c="c" />
    </div>
    <div class="tk-list tk-card">
      <ul class="tk-items"><TaskItemRow v-for="(item, at) in current.items" :key="item.key" :c="c" :item="item"
                                      :n="at + 1" /></ul>
      <div v-if="ui.adding" class="tk-add is-open">
        <textarea id="tkAddBox" v-model="adding" class="tk-box" rows="4" :placeholder="t('task.add.placeholder')"
                  :aria-label="t('task.add.placeholder')" @keydown.esc="c.closeAdd()" @keydown.ctrl.enter.prevent="c.sendAdd()"
                  @keydown.meta.enter.prevent="c.sendAdd()"></textarea>
        <div class="tk-actions">
          <button id="tkAddSend" type="button" class="btn btn-sm btn-solid" @click="c.sendAdd()">{{
            t('task.add.send') }}</button>
          <button id="tkAddCancel" type="button" class="btn btn-sm btn-ghost" @click="c.closeAdd()">{{
            t('common.cancel') }}</button>
        </div>
      </div>
      <div v-else class="tk-add"><button id="tkAddOpen" type="button" class="btn btn-sm btn-ghost"
           @click="c.openAdd()">{{ t('task.add.open') }}</button></div>
    </div>
    <section class="tk-tnotes tk-card" :aria-label="t('task.notes')">
      <TaskNotes :c="c" scope="" :notes="current.notes.filter(n => !n.items.length)" />
    </section>
    <section class="tk-thread tk-card" :aria-label="t('task.comments.aria')">
      <h3 class="tk-h">{{ t('task.comments') }}<span class="rs-n">{{ thread.length }}</span></h3>
      <button v-if="hidden" id="tkOlder" type="button" class="rs-more tk-older" @click="ui.allComments = true">{{
        t('task.comments.older', { n: hidden }) }}</button>
      <div class="tk-cs">
        <TaskComment v-for="cm in thread.slice(hidden)" :key="cm.id" :comment="cm" :fresh="enter.comments.has(cm.id)" />
        <div v-if="!thread.length" class="hint-sm tk-empty">{{ t('task.comments.empty') }}</div>
        <TaskComposer :c="c" scope="" :reply="thread.length > 0" />
      </div>
    </section>
  </div>
</template>
