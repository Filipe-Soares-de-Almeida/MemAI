<script setup lang="ts">
/* A selected domain is a place, not a record: how much of the store it holds, and the one thing a
   place can do, narrow the view to it. */
import { computed } from 'vue';
import { fmtInt } from '../../core/dom.ts';
import { t } from '../../i18n.ts';
import AppIcon from '../../components/AppIcon.vue';

const props = defineProps<{ hit: { domain: string; count: number } }>();
const emit = defineEmits<{ close: []; scope: [domain: string] }>();

const parts = computed(() => props.hit.domain.split('/'));
const parent = computed(() => parts.value.slice(0, -1).join('/'));
</script>

<template>
  <div class="gc-head">
    <div class="gc-tags">
      <span class="chip">{{ t('g.domainHolds', { n: fmtInt(hit.count) }) }}</span>
      <span v-if="parent" class="chip">{{ parent }}</span>
    </div>
    <button type="button" class="icon-btn" data-shut :aria-label="t('common.close')" :title="t('common.close')"
            @click="emit('close')"><AppIcon name="close" :title="t('common.close')" /></button>
  </div>
  <div class="gc-name">{{ parts[parts.length - 1] }}</div>
  <div class="act-row">
    <button class="btn btn-sm btn-solid" data-scope @click="emit('scope', hit.domain)">{{ t('g.domainScope') }}</button>
  </div>
</template>
