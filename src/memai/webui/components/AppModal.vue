<script setup lang="ts">
/* A dialog on the shared modal stack, teleported to #modalRoot. Head, body and foot are slots, or
   markup strings for callers that still build HTML. */
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { modalDepth, pushModal, removeModal } from '../core/modal-stack.ts';

const props = defineProps<{
  title?: string;
  ariaLabel?: string;
  wide?: boolean;
  tall?: boolean;
  headHtml?: string;
  bodyHtml?: string;
  footHtml?: string;
}>();
const emit = defineEmits<{ close: [] }>();

const scrim = ref<HTMLElement | null>(null);
const opener = document.activeElement;
const stacked = modalDepth() > 0;

onMounted(() => { if (scrim.value) pushModal(scrim.value, () => emit('close'), opener); });
onBeforeUnmount(() => { if (scrim.value) removeModal(scrim.value); });

defineExpose({ scrim });
</script>

<!-- `title` may carry markup, so the accessible name prefers ariaLabel. -->
<template>
  <Teleport to="#modalRoot">
    <div ref="scrim" class="modal-scrim" :class="{ stacked }" @mousedown.self="emit('close')">
      <div class="modal" :class="{ 'modal-wide': props.wide, 'modal-tall': props.tall }" role="dialog"
           aria-modal="true" :aria-label="props.ariaLabel || props.title" tabindex="-1">
        <div v-if="props.headHtml !== undefined" class="modal-head" v-html="props.headHtml"></div>
        <div v-else class="modal-head"><slot name="head">{{ props.title }}</slot></div>
        <div v-if="props.bodyHtml !== undefined" class="modal-body" v-html="props.bodyHtml"></div>
        <div v-else class="modal-body"><slot /></div>
        <div v-if="props.footHtml !== undefined" class="modal-foot" v-html="props.footHtml"></div>
        <div v-else class="modal-foot"><slot name="foot" /></div>
      </div>
    </div>
  </Teleport>
</template>
