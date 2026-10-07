<script setup lang="ts">
/* One jump in the inspector, from whichever end this flow is on: the arrow is its direction, the
   link its address, and the step at the far end is named. */
import { t } from '../../i18n.ts';
import type { DiagramJump } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import { jumpHref } from './diagram.ts';

defineProps<{ j: DiagramJump; index: number; uid: string; editing: boolean }>();
const emit = defineEmits<{ remove: [] }>();
</script>

<template>
  <div class="dg-jump">
    <span class="dg-arrow" :title="j.direction === 'out' ? t('dg.jump.out') : t('dg.jump.in')"><AppIcon
      :name="j.direction === 'out' ? 'arrow-right' : 'arrow-left'" /></span>
    <a class="dg-jump-to" :href="jumpHref(j, uid)"
       :title="t(j.direction === 'out' ? 'dg.jump.open' : 'dg.jump.openFrom', { title: j.peer_title })">
      <span class="dg-jump-title">{{ j.peer_title }}</span>
      <span v-if="j.peer_node" class="dg-key">{{ j.peer_node }}</span>
      <span v-else class="dg-jump-whole">{{ t('dg.jump.wholeDiagram') }}</span>
      <span v-if="j.peer_node_label" class="dg-label">{{ j.peer_node_label }}</span>
    </a>
    <span v-if="j.label" class="dg-label" :title="j.label">{{ j.label }}</span>
    <button v-if="editing" class="icon-btn danger" :data-deljump="index" :title="t('dg.jump.remove')"
            @click="emit('remove')"><AppIcon name="close" /></button>
  </div>
</template>
