<script setup lang="ts">
/* The record's bar: the way back, the domain, the stepper through the list that opened it, and the
   record-wide actions. */
import { go } from '../../core/router.ts';
import { recordSequence } from '../../core/nav.ts';
import { t } from '../../i18n.ts';
import type { MemoryRecord } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import { backTarget, goBack, step, stepPos } from './walk.ts';

const props = defineProps<{ m: MemoryRecord; editingAll: boolean }>();
const emit = defineEmits<{ toggleAll: []; more: [e: MouseEvent] }>();

const seq = recordSequence();
const pos = stepPos(props.m.uid);
const stepper = pos && !(pos.prev < 0 && pos.next >= seq.length) ? pos : null;
const back = backTarget();
</script>

<template>
  <div class="rec-bar">
    <button id="dBack" type="button" class="btn btn-sm rec-back" :title="t('dr.back.title', { label: back.label })"
            @click="goBack"><AppIcon name="chevron-left" /><span class="rec-back-text">{{ back.label }}</span></button>
    <button v-if="m.domain" type="button" class="rec-crumb" :data-fdomain="m.domain"
            :aria-label="t('a11y.filterDomain', { domain: m.domain })"
            @click="go('memories', { domain: m.domain })">{{ m.domain }}</button>
    <span class="rec-bar-end">
      <span v-if="stepper" class="rec-step" role="group" :aria-label="t('dr.step.aria')"
            :title="stepper.gone ? t('dr.step.gone') : undefined">
        <button id="dPrev" type="button" class="icon-btn" :disabled="stepper.prev < 0" :title="t('dr.step.prev')"
                :aria-label="t('dr.step.prev')" @click="step(m.uid, -1)"><AppIcon name="chevron-left" /></button>
        <span class="rec-step-at">{{ stepper.gone ? t('dr.step.atGone', { n: seq.length })
          : t('dr.step.at', { i: stepper.at + 1, n: seq.length }) }}</span>
        <button id="dNext" type="button" class="icon-btn" :disabled="stepper.next >= seq.length"
                :title="t('dr.step.next')" :aria-label="t('dr.step.next')" @click="step(m.uid, 1)"><AppIcon
                name="chevron-right" /></button>
      </span>
      <button v-if="m.type !== 'diagram' && m.type !== 'task'" id="dEditAll" type="button" class="btn btn-sm"
              :aria-pressed="editingAll" @click="emit('toggleAll')"><AppIcon name="pencil" />{{
        t('dr.editAll') }}</button>
      <button id="dMore" type="button" class="icon-btn" :title="t('dr.more')" :aria-label="t('dr.more')"
              @click="emit('more', $event)"><AppIcon name="maintenance" /></button>
    </span>
  </div>
</template>
