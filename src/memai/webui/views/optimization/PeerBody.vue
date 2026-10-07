<script setup lang="ts">
/* A memory a suggestion ties or folds, named and then read: its marks, its title, and its body under it. */
import { computed } from 'vue';
import { peerName } from '../../core/shared.js';
import { t } from '../../i18n.ts';
import type { MissingPeer, PeerCard } from '../../api/types.ts';
import StatusTag from '../../components/StatusTag.vue';
import TypeTag from '../../components/TypeTag.vue';
import UidChip from '../../components/UidChip.vue';

const props = defineProps<{ peer: PeerCard | MissingPeer | null | undefined }>();

const card = computed(() => (props.peer && !('missing' in props.peer) ? props.peer : null));
const name = computed(() => peerName(card.value));
</script>

<template>
  <div class="opt-peer-body">
    <template v-if="card">
      <div class="opt-peer-meta"><TypeTag :type="card.type" /> <UidChip :uid="card.uid" /> <StatusTag
           :status="card.status" /></div>
      <div v-if="!name.named" class="snippet">{{ name.text }}</div>
      <div v-else class="snippet"><span class="opt-peer-name">{{ name.text }}</span> <span v-if="name.hover"
           class="opt-peer-body-text">{{ name.hover }}</span></div>
    </template>
    <div v-else class="snippet">{{ t('op.missing') }}</div>
  </div>
</template>
