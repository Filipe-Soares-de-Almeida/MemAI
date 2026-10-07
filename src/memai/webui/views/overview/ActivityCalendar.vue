<script setup lang="ts">
/* New memories per day over the last weeks: a calendar, a weekday profile and three figures. */
import { computed } from 'vue';
import { fmtInt } from '../../core/dom.ts';
import { tipHide, tipShow } from '../../core/tip.ts';
import { I18N, t } from '../../i18n.ts';
import type { DayCount } from '../../api/types.ts';
import { calendar, intoWeeks, level, weekLabel, weekdaySums } from './calendar.ts';
import type { CalendarDay } from './calendar.ts';

const props = defineProps<{ activity: DayCount[] }>();

const days = computed(() => calendar(props.activity));
const weeks = computed(() => intoWeeks(days.value));
const peak = computed(() => Math.max(1, ...days.value.map(d => d.count)));
const total = computed(() => days.value.reduce((sum, d) => sum + d.count, 0));
const activeDays = computed(() => days.value.filter(d => d.count > 0).length);
const sums = computed(() => weekdaySums(days.value));
const sumPeak = computed(() => Math.max(1, ...sums.value));

const tip = (e: MouseEvent, d: CalendarDay) =>
  tipShow(t('ov.tip.onDay', { n: d.count, day: d.key }), e.clientX, e.clientY);
</script>

<template>
  <div class="panel">
    <h3 class="panel-title">{{ t('ov.activity.title') }}
      <span class="panel-aside">{{ t('ov.activity.aside', { n: fmtInt(total), days: days.length }) }}</span></h3>
    <div class="hx-cal">
      <div>
        <div class="hx-grid" role="img" :aria-label="t('ov.spark.aria', {
          n: fmtInt(total), peak: fmtInt(peak), days: days.length, active: activeDays })">
          <span></span>
          <span v-for="w in I18N.weekdays" :key="w" class="hx-cal-dow">{{ w }}</span>
          <template v-for="(row, r) in weeks" :key="r">
            <span class="hx-cal-week">{{ weekLabel(row) }}</span>
            <template v-for="(d, i) in row" :key="i">
              <span v-if="d" class="hx-cell" :class="[`l${level(d.count, peak)}`, { today: d.today }]"
                    :data-day="d.key" @mousemove="tip($event, d)" @mouseleave="tipHide"></span>
              <span v-else class="hx-cell future"></span>
            </template>
          </template>
        </div>
        <div class="hx-scale">
          <span>0</span>
          <span v-for="l in 5" :key="l" class="hx-cell" :class="`l${l - 1}`"></span>
          <span>{{ fmtInt(peak) }}</span>
        </div>
      </div>
      <div class="hx-dow">
        <div class="mg-label">{{ t('ov.activity.byWeekday') }}</div>
        <div v-for="(w, i) in I18N.weekdays" :key="w" class="hx-dow-row">
          <span>{{ w }}</span>
          <div class="bar-track"><div class="bar-fill"
               :style="{ '--v': (sums[i] / sumPeak).toFixed(4), background: 'var(--accent)' }"></div></div>
          <span class="hx-dow-n">{{ fmtInt(sums[i]) }}</span>
        </div>
      </div>
      <div class="hx-cal-stats">
        <div><div class="mg-label">{{ t('ov.activity.avg') }}</div>
          <div class="spark-stat">{{ (total / days.length).toFixed(1) }}</div></div>
        <div><div class="mg-label">{{ t('ov.activity.peak') }}</div>
          <div class="spark-stat">{{ fmtInt(peak) }}</div></div>
        <div><div class="mg-label">{{ t('ov.activity.daysWith') }}</div>
          <div class="spark-stat">{{ t('ov.activity.ofDays', { n: activeDays, all: days.length }) }}</div></div>
      </div>
    </div>
  </div>
</template>
