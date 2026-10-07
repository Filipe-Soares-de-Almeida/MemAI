<script setup lang="ts">
/* The flow the caret is on: its faults, its counts, its summary, where it is filed, and the two
   things to do to it. */
import { computed } from 'vue';
import { fmtInt } from '../../core/dom.ts';
import { openRecord } from '../../core/nav.ts';
import { go } from '../../core/router.ts';
import { t } from '../../i18n.ts';
import type { DiagramRow } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import UidChip from '../../components/UidChip.vue';
import IssueChip from './IssueChip.vue';
import { sortIssues } from './diagrams.ts';

const props = defineProps<{ d: DiagramRow | null }>();
const issues = computed(() => props.d ? sortIssues(props.d) : []);
const stats = computed(() => props.d ? [
  { key: 'dgl.stat.steps', n: props.d.nodes, why: '' },
  { key: 'dgl.stat.conns', n: props.d.edges, why: '' },
  { key: 'dgl.stat.linked', n: props.d.links, why: '' },
  { key: 'dgl.stat.jumps', n: props.d.jumps, why: t('dgl.jumpsWhy') },
] as const : []);
</script>

<!-- No aria-live: the row announces itself as it takes focus, and a live pane would read each flow twice. -->
<template>
  <aside class="dgl-ins" :class="{ 'is-empty': !d }" id="dglIns">
    <div v-if="!d" class="dgl-ins-empty">{{ t('dgl.pickHint') }}</div>
    <template v-else>
      <div class="dgl-ins-head">
        <span class="dgl-ins-title">{{ d.title || '—' }}</span>
        <div class="dgl-ins-marks">
          <template v-if="issues.length"><IssueChip v-for="(i, n) in issues" :key="n" :issue="i" /></template>
          <span v-else class="dgl-sound"><AppIcon name="confirmed" />{{ t('dgl.sound') }}</span>
          <UidChip :uid="d.uid" />
        </div>
      </div>
      <div class="dgl-ins-body">
        <div class="dgl-ins-stats">
          <div v-for="s in stats" :key="s.key" class="dgl-stat" :title="s.why || undefined">
            <span class="mg-label">{{ t(s.key) }}</span>
            <span class="dgl-stat-n">{{ fmtInt(s.n) }}</span>
          </div>
        </div>
        <div class="dgl-ins-field">
          <span class="mg-label">{{ t('dgl.summary') }}</span>
          <p v-if="d.summary" class="dgl-ins-text">{{ d.summary }}</p>
          <span v-else class="hint">{{ t('dgl.noSummary') }}</span>
        </div>
        <div class="dgl-ins-field">
          <span class="mg-label">{{ t('dgl.filed') }}</span>
          <span class="dgl-path">{{ d.domain || t('dgl.noDomain') }}</span>
        </div>
      </div>
      <div class="dgl-ins-foot">
        <button class="btn btn-sm" :data-record="d.uid" @click="openRecord(d.uid)">{{ t('dg.record') }}</button>
        <button class="btn btn-sm btn-solid" :data-edit="d.uid" @click="go('diagram', { uid: d.uid })">{{
          t('dr.openEditor') }}</button>
      </div>
    </template>
  </aside>
</template>
