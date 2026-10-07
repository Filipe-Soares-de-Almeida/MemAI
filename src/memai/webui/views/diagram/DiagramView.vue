<script setup lang="ts">
/* The diagram editor's address: ?uid, and, when arriving from a jump, ?node to land on and
   ?from/?fromNode for the way back. */
import type { ViewProps } from '../../core/vue.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import FlowEditor from './FlowEditor.vue';

const props = defineProps<ViewProps>();
const uid = props.params.get('uid') || '';
const came = { from: props.params.get('from') || '', fromNode: props.params.get('fromNode') || '' };
const mem = uid ? await client.memories.get(uid) : null;
const diagram = mem?.type === 'diagram' ? mem.diagram ?? null : null;
</script>

<template>
  <div v-if="!uid" class="empty">{{ t('dg.noUid') }}</div>
  <div v-else-if="!diagram" class="empty">{{ t('dg.notDiagram') }}</div>
  <FlowEditor v-else :uid="uid" :diagram="diagram" :land-on="params.get('node') || ''" :came="came" />
</template>
