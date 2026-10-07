<script setup lang="ts">
/* A structural fault named, with the steps it names and why it matters; a note role and a tab stop
   so a keyboard and a screen reader get the explanation a mouse gets from the title. */
import { computed } from 'vue';
import { t } from '../../i18n.ts';
import type { I18nKey } from '../../i18n.ts';
import type { DiagramIssue } from '../../api/types.ts';

const props = defineProps<{ issue: DiagramIssue }>();
const label = computed(() => t(`dg.issue.${props.issue.kind}` as I18nKey));
const keys = computed(() => props.issue.keys.length ? `: ${props.issue.keys.join(', ')}` : '');
const why = computed(() => t(`dg.issueWhy.${props.issue.kind}` as I18nKey));
</script>

<template>
  <span class="dgl-issue" role="note" tabindex="0" :title="why" :aria-label="`${label}${keys} — ${why}`">{{
    label }}{{ keys }}</span>
</template>
