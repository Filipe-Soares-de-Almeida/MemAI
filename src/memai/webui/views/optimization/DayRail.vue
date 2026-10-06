<script setup lang="ts">
/* The selected day as a launcher: its runs as cards that open them, since a decision is taken on
   the run's own page where its evidence is. */
import { fromKey } from '../../core/dom.ts';
import { go } from '../../core/router.ts';
import { t } from '../../i18n.ts';
import AppIcon from '../../components/AppIcon.vue';
import LotCard from './LotCard.vue';
import { longDate } from './calendar.ts';
import type { DaySlot } from './calendar.ts';

defineProps<{ day: string; slot?: DaySlot; today: string }>();
</script>

<template>
  <div id="optDay" class="opt-day panel">
    <div class="opt-day-head">
      <div class="opt-day-title">
        <span class="opt-day-when">{{ longDate(fromKey(day), { weekday: 'long', day: 'numeric', month: 'long' }) }}</span>
        <span v-if="day === today" class="opt-day-today">{{ t('op.cal.today') }}</span>
        <span class="opt-foot-gap"></span>
        <button v-if="slot?.pending" type="button" class="btn btn-sm opt-day-review" :data-seeday="day"
                :title="t('op.cal.reviewDayTitle')" @click="go('optimization', { review: day })">{{
          t('op.cal.reviewDay') }}<AppIcon name="chevron-right" /></button>
      </div>
      <div class="opt-day-sub">{{ slot ? t('op.cal.daySub', { n: slot.runs.length, s: slot.total })
        : t('op.cal.dayNone') }}</div>
    </div>
    <div v-if="slot" class="opt-lots"><LotCard v-for="r in slot.runs" :key="r.id" :run="r" /></div>
    <div v-else class="empty opt-day-empty">
      <AppIcon name="db-file" cls="opt-empty-mark" />
      <p class="opt-empty-msg">{{ t('op.cal.emptyDay', { day: longDate(fromKey(day), { day: 'numeric', month: 'long' }) }) }}</p>
      <p class="hint-sm">{{ t('op.cal.emptyHint') }}</p>
    </div>
  </div>
</template>
