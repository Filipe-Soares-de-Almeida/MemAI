<script setup lang="ts">
/* What the task is for, edited in place. */
import { nextTick, ref, watch } from 'vue';
import { t } from '../../i18n.ts';
import AppIcon from '../../components/AppIcon.vue';
import RichText from '../../components/RichText.vue';
import type { Checklist } from './checklist.ts';

const props = defineProps<{ c: Checklist }>();
const { current, ui } = props.c;

const text = ref('');
const box = ref<HTMLTextAreaElement | null>(null);
watch(() => ui.goalEditing, open => {
  if (!open) return;
  text.value = current.value.goal;
  nextTick(() => {
    box.value?.focus();
    box.value?.setSelectionRange(text.value.length, text.value.length);
  });
});
</script>

<template>
  <div v-if="ui.goalEditing" class="tk-goal is-editing">
    <textarea id="tkGoalBox" ref="box" v-model="text" class="tk-box tk-goal-box" rows="3"
              :aria-label="t('task.goal.aria')" @keydown.esc="c.leaveGoal()"
              @keydown.ctrl.enter.prevent="c.saveGoal(text)" @keydown.meta.enter.prevent="c.saveGoal(text)"></textarea>
    <div class="tk-actions">
      <button id="tkGoalSave" type="button" class="btn btn-sm btn-solid" @click="c.saveGoal(text)">{{
        t('common.save') }}</button>
      <button id="tkGoalCancel" type="button" class="btn btn-sm btn-ghost" @click="c.leaveGoal()">{{
        t('common.cancel') }}</button>
    </div>
  </div>
  <div v-else class="tk-goal">
    <div v-if="current.goal" class="tk-goal-text"><RichText :text="current.goal" :prose="false" :highlight="false"
         :items="current.refs" :on-item="c.flashItem" /></div>
    <p v-else class="tk-goal-text is-empty">{{ t('task.goal.empty') }}</p>
    <button id="tkGoalEdit" type="button" class="icon-btn" :title="t('task.goal.edit')" :aria-label="t('task.goal.edit')"
            @click="c.editGoal()"><AppIcon name="pencil" /></button>
  </div>
</template>
