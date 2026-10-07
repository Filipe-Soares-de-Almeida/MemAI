<script setup lang="ts">
/* The canvas's top-left corner: the way back to the flow that sent the reader here and the ways on
   from the selected step, three at a time with the rest folded behind +N. */
import { computed, ref, watch } from 'vue';
import { t } from '../../i18n.ts';
import AppIcon from '../../components/AppIcon.vue';
import { JUMP_CHIPS_SHOWN, chipLabel } from './diagram.ts';
import type { NavOffer } from './diagram.ts';

const props = defineProps<{ offers: NavOffer[] }>();
const open = ref(false);
watch(() => props.offers, () => { open.value = false; });
const folded = computed(() => Math.max(0, props.offers.length - JUMP_CHIPS_SHOWN));
</script>

<template>
  <div class="dg-jumpnav" :class="{ open }" id="dgJumpNav" :hidden="!offers.length">
    <a v-for="(o, i) in offers" :key="`${o.kind}:${o.href}`" class="dg-navchip"
       :class="{ back: o.kind === 'back', over: i >= JUMP_CHIPS_SHOWN }" :href="o.href" :title="o.hint">
      <span class="dg-navchip-mark"><AppIcon :name="o.kind === 'back' ? 'arrow-left' : 'arrow-right'" /></span>
      <span class="dg-navchip-text">{{ chipLabel(o.title) }}</span>
      <span v-if="o.step" class="dg-key">{{ o.step }}</span>
    </a>
    <button v-if="folded" type="button" class="dg-navchip dg-navmore"
            :title="open ? t('dg.jump.less') : t('dg.jump.more', { n: folded })" @click="open = !open">{{
      open ? '−' : `+${folded}` }}</button>
  </div>
</template>
