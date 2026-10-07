<script setup lang="ts">
/* A thread's last line: your reply, its draft kept per scope, the send control inside its box. */
import { computed } from 'vue';
import { t } from '../../i18n.ts';
import AppIcon from '../../components/AppIcon.vue';
import KeyHint from '../../components/KeyHint.vue';
import type { Checklist } from './checklist.ts';

const props = defineProps<{ c: Checklist; scope: string; reply: boolean }>();
const { ui } = props.c;

const draft = computed({
  get: () => ui.drafts.get(props.scope) || '',
  set: value => { ui.drafts.set(props.scope, value); },
});
const ph = computed(() => t(props.reply ? 'task.comment.reply' : 'task.comment.placeholder'));
</script>

<template>
  <div class="tk-c tk-compose is-person">
    <span class="tk-c-av" aria-hidden="true"><AppIcon name="person" /></span>
    <div class="tk-reply">
      <textarea v-model="draft" class="tk-box" rows="2" :data-draft="scope" :placeholder="ph" :aria-label="ph"
                @keydown.ctrl.enter.prevent="c.post(scope)" @keydown.meta.enter.prevent="c.post(scope)"></textarea>
      <div class="tk-reply-foot">
        <span class="tk-hint"><KeyHint save :action="t('task.comment.hint')" /></span>
        <button type="button" class="btn btn-sm btn-solid" :data-send="scope" :disabled="!draft.trim()"
                @click="c.post(scope)">{{ t('task.comment.send') }}</button>
      </div>
    </div>
  </div>
</template>
