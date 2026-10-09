<script setup lang="ts">
/* A line of a thread: the writer's mark (a round person, a square prompt for an agent), who and when,
   then the message. */
import { fmtAgo } from '../../core/dom.ts';
import { t } from '../../i18n.ts';
import type { BodyLink, ItemRef, TaskComment } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import RichText from '../../components/RichText.vue';

defineProps<{ comment: TaskComment; fresh?: boolean; refs?: Record<string, ItemRef>; links?: Record<string, BodyLink>;
              onItem?: (id: number) => void }>();
</script>

<template>
  <article class="tk-c" :class="[comment.author === 'person' ? 'is-person' : 'is-agent', { 'is-new': fresh }]">
    <span class="tk-c-av" aria-hidden="true"><AppIcon :name="comment.author === 'person' ? 'person' : 'agent'" /></span>
    <div class="tk-c-main">
      <header class="tk-c-head">
        <span class="tk-c-who">{{ t(comment.author === 'person' ? 'task.author.person' : 'task.author.agent') }}</span>
        <span v-if="comment.session && comment.author !== 'person'" class="tk-c-session" :title="comment.session">{{
          comment.session.slice(0, 14) }}</span>
        <time class="tk-c-when" :datetime="comment.created_at" :title="comment.created_at">{{
          fmtAgo(comment.created_at) }}</time>
      </header>
      <div class="tk-c-body"><RichText :text="comment.body" :prose="false" :highlight="false" :links="links" :items="refs"
           :on-item="onItem" /></div>
    </div>
  </article>
</template>
