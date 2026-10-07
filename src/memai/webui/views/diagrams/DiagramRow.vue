<script setup lang="ts">
/* One flow in the list: whether its shape is sound, its name, its size or what is wrong with it,
   and how long ago it changed. */
import { computed } from 'vue';
import { fmtAgo, fmtInt } from '../../core/dom.ts';
import { t } from '../../i18n.ts';
import type { I18nKey } from '../../i18n.ts';
import type { DiagramRow } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import StatusTag from '../../components/StatusTag.vue';
import { sortIssues } from './diagrams.ts';

const props = defineProps<{ d: DiagramRow; picked: boolean }>();
const issues = computed(() => sortIssues(props.d));
const size = computed(() => issues.value.length
  ? issues.value.map(i => t(`dg.issue.${i.kind}` as I18nKey)).join(' · ')
  : `${t('dgl.steps', { n: fmtInt(props.d.nodes) })} · ${t('dgl.conns', { n: fmtInt(props.d.edges) })}`);
</script>

<template>
  <div class="dgl-row" role="option" :aria-selected="picked ? 'true' : 'false'" :tabindex="picked ? 0 : -1"
       :data-uid="d.uid">
    <span class="dgl-state" :class="{ 'is-broken': issues.length }"><AppIcon
      :name="issues.length ? 'unverified' : 'confirmed'" /><span v-if="!issues.length"
      class="sr-only">{{ t('dgl.sound') }}</span></span>
    <span class="dgl-main">
      <span class="dgl-name" :title="d.title || ''">{{ d.title || '—' }}</span>
      <!-- a broken flow says what is wrong instead of how big it is -->
      <span class="dgl-sub" :class="{ 'is-broken': issues.length }">{{ `${size} · ` }}<span
        :title="t('dgl.documentedWhy')">{{ t('dgl.documented', { n: fmtInt(d.documented), total: fmtInt(d.nodes) }) }}</span></span>
    </span>
    <span class="dgl-right"><StatusTag :status="d.status" /><span class="dgl-when" :title="d.updated_at">{{
      fmtAgo(d.updated_at) }}</span></span>
  </div>
</template>
