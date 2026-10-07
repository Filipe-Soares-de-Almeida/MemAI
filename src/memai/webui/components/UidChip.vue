<script setup lang="ts">
/* A memory's uid as a button that copies it, labelled for what pressing does. */
import { copyUid } from '../core/copy.ts';
import { t } from '../i18n.ts';

defineProps<{ uid: string }>();
</script>

<template>
  <button type="button" class="uid-chip" :data-copy="uid" :title="t('uid.copyTitle')"
          :aria-label="t('a11y.copyUid', { uid })" @click.stop="copyUid(uid)">{{ uid }}</button>
</template>
