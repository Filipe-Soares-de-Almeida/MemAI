<script setup lang="ts">
/* The evidence for one suggestion: what it targets, why, the change itself with what differs marked,
   whether the agent checked it, and -- outside a day review -- its own Apply and Reject. */
import { computed, onMounted, ref } from 'vue';
import { markPair } from '../../core/textdiff.ts';
import { kindLabel, kindTitle } from '../../core/shared.js';
import { openRecord } from '../../core/nav.ts';
import { t } from '../../i18n.ts';
import type { Suggestion } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import RichText from '../../components/RichText.vue';
import TypeTag from '../../components/TypeTag.vue';
import UidChip from '../../components/UidChip.vue';
import SuggestionBody from './SuggestionBody.vue';
import { showsPreview } from './suggestion.ts';

const props = defineProps<{ s: Suggestion; at: number; total: number; decideHere: boolean; busy: boolean }>();
const emit = defineEmits<{ apply: []; reject: []; revert: [] }>();

const tg = computed(() => props.s.target);
/* a distill opens the memory it created, once applied */
const openUid = computed(() => props.s.target_uid || props.s.new_uid || '');

/* the marks are taken from the panes as drawn, so the words marked are the words shown; the parent
   keys this pane by suggestion and status, so it is marked once per drawing */
const root = ref<HTMLElement | null>(null);
onMounted(() => {
  const host = root.value?.parentElement;
  markPair(host?.querySelector('.opt-diff-b') ?? null, host?.querySelector('.opt-diff-a') ?? null);
});
</script>

<template>
  <div ref="root" class="opt-detail-head">
    <span class="opt-kind" :title="kindTitle(s.kind)">{{ kindLabel(s.kind) }}</span>
    <TypeTag v-if="tg?.type" :type="tg.type" />
    <UidChip v-if="s.target_uid" :uid="s.target_uid" />
    <span v-if="tg?.domain" class="chip">{{ tg.domain }}</span>
    <span class="opt-foot-gap"></span>
    <span class="opt-detail-at">{{ t('op.detail.at', { i: at + 1, n: total }) }}</span>
  </div>
  <div v-if="tg?.title" class="opt-detail-title">{{ tg.title }}</div>
  <div v-if="s.rationale" class="opt-why">
    <span class="opt-label">{{ t('op.why') }}</span>
    <RichText class="opt-why-body" :text="s.rationale" :links="s.body_links" :prose="false" :highlight="false" />
  </div>
  <div v-if="showsPreview(s)" class="opt-preview">
    <span class="opt-label">{{ t('op.underReview') }}</span>
    <div class="snippet">{{ tg?.snippet || '' }}</div>
  </div>
  <SuggestionBody :s="s" />
  <div class="opt-detail-foot">
    <span v-if="s.verified" class="opt-verified" :title="s.verified"><AppIcon name="confirmed" /><span
          class="opt-verified-text">{{ t('op.verified', { v: s.verified }) }}</span></span>
    <span v-else class="opt-verified muted"><AppIcon name="unverified" /><span class="opt-verified-text">{{
      t('op.noVerified') }}</span></span>
    <button v-if="openUid" type="button" class="btn btn-sm btn-ghost" :data-openopt="openUid"
            @click="openRecord(openUid)">{{ t('common.openRecord') }}</button>
    <button v-if="s.status === 'applied'" type="button" class="btn btn-sm" :data-revert="s.id" :disabled="busy"
            @click="emit('revert')">{{ t('common.undo') }}</button>
    <span v-else-if="s.status !== 'pending'" class="status-tag archived">{{ t('op.rejected') }}</span>
    <template v-else-if="decideHere">
      <button type="button" class="btn btn-sm" :data-reject="s.id" :disabled="busy" @click="emit('reject')">{{
        t('common.reject') }}</button>
      <button type="button" class="btn btn-solid btn-sm" :data-apply="s.id" :disabled="busy" @click="emit('apply')">{{
        t('common.apply') }}</button>
    </template>
  </div>
</template>
