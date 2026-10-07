<script setup lang="ts">
/* An archived memory's tag; an active one shows nothing. */
import { t } from '../i18n.ts';

defineProps<{ status: string }>();
</script>

<template>
  <span v-if="status === 'archived'" class="status-tag archived">{{ t('status.archived') }}</span>
</template>
