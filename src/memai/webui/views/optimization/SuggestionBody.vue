<script setup lang="ts">
/* A suggestion's change, drawn in the shape it has: two memories tied, sources folded into one, a
   body rewritten, a field cleaned, a flag moved, a set changed, one value replaced, or the raw payload. */
import { computed } from 'vue';
import { fmtInt } from '../../core/dom.ts';
import { relLabel, relTypeTitle } from '../../core/shared.js';
import { t } from '../../i18n.ts';
import type { I18nKey } from '../../i18n.ts';
import type { Suggestion } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import ConfPill from '../../components/ConfPill.vue';
import RichText from '../../components/RichText.vue';
import PeerBody from './PeerBody.vue';
import { leakField, linePair, setPair, shapeOf } from './suggestion.ts';

const props = defineProps<{ s: Suggestion }>();

const shape = computed(() => shapeOf(props.s.kind));
const p = computed(() => props.s.payload);
const chars = (n?: number) => (n ? ` · ${t('op.chars', { n: fmtInt(n) })}` : '');

/* merge carries no type: the edge it writes is always a supersedes */
const rel = computed(() => {
  const peers = props.s.peers || {};
  const link = props.s.kind === 'link';
  return {
    pair: link ? [[t('op.role.from'), peers.from_uid], [t('op.role.to'), peers.to_uid]] as const
               : [[t('op.role.keep'), peers.keep_uid], [t('op.role.drop'), peers.drop_uid]] as const,
    type: link ? String(p.value.relation_type || 'relates_to') : 'supersedes',
  };
});
const set = computed(() => setPair(props.s));
const line = computed(() => linePair(props.s));
const field = computed(() => leakField(props.s));
const fieldLabel = (side: 'op.before' | 'op.after', n?: number) =>
  `${t(side)} · ${t(`op.field.${field.value}` as I18nKey)}${chars(n)}`;
</script>

<template>
  <div v-if="shape === 'distill'" class="opt-distill">
    <span class="opt-label">{{ t('op.distill.sources', { n: (s.sources || []).length }) }}</span>
    <PeerBody v-for="(m, i) in s.sources || []" :key="i" :peer="m" />
    <div class="opt-arrow" :title="relTypeTitle('supersedes') || t('op.relType.title')">{{
      relLabel('supersedes') }}<AppIcon name="arrow-right" /></div>
    <span class="opt-label">{{ t('op.distill.new') }}{{ p.new_type ? ` · ${p.new_type}` : '' }}{{
      p.domain ? ` · ${p.domain}` : '' }}</span>
    <div v-if="p.title" class="snippet"><strong>{{ p.title }}</strong></div>
    <RichText class="snippet" :text="String(p.new_content ?? '')" :links="s.body_links" :prose="false"
              :highlight="false" />
  </div>

  <div v-else-if="shape === 'rel'" class="opt-rel">
    <template v-for="([role, m], i) in rel.pair" :key="i">
      <span class="opt-label" :style="{ gridArea: ['l1', 'l2'][i] }">{{ role }}</span>
      <PeerBody :peer="m" :style="{ gridArea: ['b1', 'b2'][i] }" />
    </template>
    <div class="opt-arrow" :title="relTypeTitle(rel.type) || t('op.relType.title')">{{
      relLabel(rel.type) }}<AppIcon name="arrow-right" /></div>
  </div>

  <div v-else-if="shape === 'text' && field !== 'content'" class="opt-line">
    <span class="opt-label">{{ fieldLabel('op.before') }}</span>
    <span class="opt-label">{{ fieldLabel('op.after') }}</span>
    <div class="opt-lval opt-diff-b">{{ s.text_before || '—' }}</div>
    <div class="opt-lval opt-diff-a">{{ p.new_text || '—' }}</div>
  </div>

  <div v-else-if="shape === 'text' || shape === 'prose'" class="opt-diff">
    <span class="opt-label opt-diff-bl">{{ shape === 'text' ? fieldLabel('op.before', s.chars_before)
      : t('op.before') + chars(s.chars_before) }}</span>
    <span class="opt-label opt-diff-al">{{ shape === 'text' ? fieldLabel('op.after', s.chars_after)
      : t('op.after') + chars(s.chars_after) }}</span>
    <RichText class="snippet opt-diff-b" :prose="false" :highlight="false" :links="s.body_links"
              :text="shape === 'text' ? s.text_before ?? '' : s.content_before ?? s.target?.snippet ?? ''" />
    <div class="opt-arrow"><AppIcon name="arrow-right" /></div>
    <RichText class="snippet opt-diff-a" :prose="false" :highlight="false" :links="s.body_links"
              :text="String((shape === 'text' ? p.new_text : p.new_content) ?? '')" />
  </div>

  <!-- both states in the mark the rest of the dashboard draws them with -->
  <div v-else-if="shape === 'flag'" class="opt-flag">
    <span class="opt-label">{{ t('op.before') }}</span>
    <span class="opt-flag-was"><ConfPill v-if="s.kind === 'set_confidence'"
          :confidence="s.target?.confidence || 'unverified'" /><span v-else class="status-tag"
          :class="s.target?.status === 'archived' ? 'archived' : 'active'">{{
          t(s.target?.status === 'archived' ? 'status.archived' : 'status.active') }}</span></span>
    <span class="opt-flag-arrow"><AppIcon name="arrow-right" /></span>
    <span class="opt-label">{{ t('op.after') }}</span>
    <span class="opt-flag-now"><ConfPill v-if="s.kind === 'set_confidence'" :confidence="String(p.confidence)" /><span
          v-else class="status-tag archived">{{ t('status.archived') }}</span></span>
    <span v-if="s.kind === 'archive' && p.reason" class="opt-flag-why">{{ p.reason }}</span>
  </div>

  <div v-else-if="shape === 'set'" class="opt-set">
    <span class="opt-label">{{ t('op.before') }} · {{ t('op.set.n', { n: set.was.length }) }}</span>
    <span class="opt-label">{{ t('op.after') }} · {{ t('op.set.n', { n: set.now.length }) }}</span>
    <div class="opt-chips"><span v-for="x in set.was" :key="x" class="opt-schip"
         :class="{ 'is-gone': !set.now.includes(x) }">{{ x }}</span><span v-if="!set.was.length"
         class="opt-schip is-none">{{ t('op.set.none') }}</span></div>
    <div class="opt-chips"><span v-for="x in set.now" :key="x" class="opt-schip"
         :class="{ 'is-new': !set.was.includes(x) }">{{ x }}</span><span v-if="!set.now.length"
         class="opt-schip is-none">{{ t('op.set.none') }}</span></div>
    <div class="opt-set-sum"><span v-if="set.gone.length" class="opt-set-out">{{
      t('op.set.dropped', { n: set.gone.length }) }}</span><span v-if="set.born.length" class="opt-set-in">{{
      t('op.set.added', { n: set.born.length }) }}</span><span v-if="!set.gone.length && !set.born.length">{{
      t('op.set.same') }}</span></div>
  </div>

  <!-- one short value each; the words that differ are still marked -->
  <div v-else-if="shape === 'line'" class="opt-line">
    <span class="opt-label">{{ t('op.before') }}</span>
    <span class="opt-label">{{ t('op.after') }}</span>
    <div class="opt-lval opt-diff-b" :class="{ 'is-path': s.kind === 'redomain' }">{{ line[0] || '—' }}</div>
    <div class="opt-lval opt-diff-a" :class="{ 'is-path': s.kind === 'redomain' }">{{ line[1] || '—' }}</div>
  </div>

  <!-- a kind with no pane of its own is still readable, and asks for no decision about nothing -->
  <div v-else class="opt-unknown">
    <span class="opt-label">{{ t('op.unknownKind') }}</span>
    <pre class="snippet">{{ JSON.stringify(s.payload ?? {}, null, 2) }}</pre>
  </div>
</template>
