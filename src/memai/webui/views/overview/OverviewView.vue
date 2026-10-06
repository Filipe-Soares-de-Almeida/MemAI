<script setup lang="ts">
/* Health, the landing view: what state the store is in and what to do about it, every number as
   /api/overview serves it. */
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { ViewProps } from '../../core/vue.ts';
import ConfidencePanel from './ConfidencePanel.vue';
import SymptomList from './SymptomList.vue';
import OpenTasks from './OpenTasks.vue';
import ActivityCalendar from './ActivityCalendar.vue';
import TypeConfidence from './TypeConfidence.vue';

defineProps<ViewProps>();

const o = await client.overview();
</script>

<template>
  <div class="anim">
    <h2 class="sr-only">{{ t('ov.title') }}</h2>
    <div class="hx-top">
      <ConfidencePanel :health="o.health" :by-confidence="o.by_confidence" />
      <div class="hx-right">
        <SymptomList :symptoms="o.symptoms" :active="o.health.active" />
      </div>
    </div>
    <OpenTasks :count="o.open_tasks" />
    <div class="grid grid-3232">
      <ActivityCalendar :activity="o.activity" />
      <TypeConfidence :by-type="o.by_type" :by-type-confidence="o.by_type_confidence" />
    </div>
  </div>
</template>
