<script setup lang="ts">
/* Settings of this browser's dashboard rather than of the store: how much it moves. */
import { ref } from 'vue';
import { MOTION_MODES, getMotion, setMotion } from '../../core/motion.ts';
import { t } from '../../i18n.ts';
import Picker from '../../components/Picker.vue';

const modeItems = MOTION_MODES.map(m => ({ value: m, label: t(`mn.ui.${m}`) }));
const motion = ref<string>(getMotion());
</script>

<template>
  <section class="panel mnt-short">
    <h3 class="panel-title">{{ t('mn.ui.title') }}
      <span class="panel-aside">{{ t('mn.ui.aside') }}</span></h3>
    <div class="list-toolbar toolbar-sm">
      <label class="inline-label">{{ t('mn.ui.motion') }}
        <Picker id="uiMotion" v-model="motion" :items="modeItems" :aria-label="t('mn.ui.motion')" @pick="setMotion" /></label>
    </div>
    <p class="hint">{{ t('mn.ui.body') }}</p>
  </section>
</template>
