<script setup lang="ts">
/* The selected memory over the drawing: what it is, where it is filed, and up to five of its
   relations as hops to travel along. */
import { computed } from 'vue';
import { typeColor } from '../../core/shared.js';
import { openRecord } from '../../core/nav.ts';
import { t } from '../../i18n.ts';
import type { GraphNode } from '../../api/types.ts';
import type { GraphNode as Drawn } from '../../engines/graph-arrange.ts';
import AppIcon from '../../components/AppIcon.vue';
import ConfPill from '../../components/ConfPill.vue';
import StatusTag from '../../components/StatusTag.vue';
import TypeTag from '../../components/TypeTag.vue';
import UidChip from '../../components/UidChip.vue';
import { clip } from './graph.ts';

const TRAVEL_MAX = 5;
const TRAVEL_CLIP = 16;

const props = defineProps<{ node: Drawn & GraphNode; peers: Drawn[] }>();
const emit = defineEmits<{ close: []; hop: [uid: string] }>();

const shown = computed(() => props.peers.slice(0, TRAVEL_MAX));
/* the body's opening line, unless it already is the name */
const body = computed(() => props.node.label && props.node.label !== props.node.name ? props.node.label : '');
const also = computed(() => props.node.also || []);
</script>

<template>
  <div class="gc-head">
    <div class="gc-tags">
      <TypeTag :type="node.type" /> <UidChip :uid="node.uid" /> <StatusTag :status="node.status" /> <ConfPill
        :confidence="node.confidence" />
    </div>
    <!-- the selection dims the rest of the store, and clicking empty space to clear it is not on screen -->
    <button type="button" class="icon-btn" data-shut :aria-label="t('common.close')" :title="t('common.close')"
            @click="emit('close')"><AppIcon name="close" :title="t('common.close')" /></button>
  </div>
  <div class="gc-name">{{ node.name }}</div>
  <div v-if="body" class="snippet">{{ body }}</div>
  <div class="act-row">
    <button class="btn btn-sm btn-solid" data-openrec @click="openRecord(node.uid)">{{ t('common.openRecord') }}</button>
    <span v-if="node.domain" class="chip">{{ node.domain }}</span>
    <span v-if="also.length" class="chip" :title="also.join(' · ')">{{ t('g.alsoIn', { n: also.length }) }}</span>
  </div>
  <div v-if="shown.length" class="gc-travel">
    <span class="gc-travel-head">{{ t('g.travel') }} <span class="gc-walk">{{ t('g.walk') }}</span></span>
    <div class="gc-hops">
      <button v-for="p in shown" :key="p.uid" type="button" class="gc-hop" :data-hop="p.uid" :title="p.name"
              @click="emit('hop', p.uid)"><span class="dot" :style="{ '--c': typeColor(p.type) }"></span>{{
        clip(p.name, TRAVEL_CLIP) }}</button>
      <span v-if="peers.length > shown.length" class="chip">{{ t('g.travelMore', { n: peers.length - shown.length }) }}</span>
    </div>
  </div>
  <div v-else class="gc-travel"><span class="gc-travel-head">{{ t('g.noLinks') }}</span></div>
</template>
