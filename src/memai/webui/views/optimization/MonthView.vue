<script setup lang="ts">
/* The month of runs: a grid that says which days a pass ran on and which still hold a decision,
   and the rail that opens the selected day's runs. */
import { computed, ref } from 'vue';
import { dayKey, fmtInt, fromKey } from '../../core/dom.ts';
import { replaceParams } from '../../core/router.ts';
import { t } from '../../i18n.ts';
import type { RunRow } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import DayRail from './DayRail.vue';
import { byDay, landingDay, longDate, monthCells, monthTotals, stepMonth, weekdayNames } from './calendar.ts';

const props = defineProps<{ runs: RunRow[]; params: URLSearchParams }>();

const days = byDay(props.runs);
const today = dayKey(new Date());
const landing = landingDay(days, today);
const asked = props.params.get('day') || landing;
const selected = ref(days.has(asked) || asked === today || /^\d{4}-\d{2}-\d{2}$/.test(asked) ? asked : landing);
const month = ref(props.params.get('month') || selected.value.slice(0, 7));

/* the address carries the month and the day; replaced, since walking months is not a step for Back */
const remember = () => replaceParams('optimization', { month: month.value, day: selected.value });
function choose(day: string, inMonth = month.value) {
  month.value = inMonth;
  selected.value = day;
  remember();
}
function step(by: number) {
  const next = stepMonth(month.value, selected.value, by);
  choose(next.selected, next.month);
}

const cells = computed(() => monthCells(month.value).map(c => c && { ...c, slot: days.get(c.key) }));
const foot = computed(() => monthTotals(month.value, days));
const dateLabel = (key: string) => longDate(fromKey(key), { weekday: 'long', day: 'numeric', month: 'long' });

function cellAria(key: string) {
  const slot = days.get(key);
  return `${dateLabel(key)} · ${slot ? t('op.cal.aria.has', { n: slot.runs.length, s: slot.total, o: slot.pending })
                                      : t('op.cal.aria.none')}`;
}
</script>

<template>
  <div class="opt-shell">
    <h2 class="sr-only">{{ t('op.title') }}</h2>
    <div v-if="runs.length" class="opt-cal-work">
      <div id="optCal" class="opt-cal">
        <div class="opt-cal-bar">
          <h3 class="opt-cal-month">{{ longDate(fromKey(`${month}-01`), { month: 'long', year: 'numeric' }) }}</h3>
          <span class="opt-cal-step">
            <button id="optPrev" type="button" class="opt-step" :title="t('op.cal.prev')" :aria-label="t('op.cal.prev')"
                    @click="step(-1)"><AppIcon name="chevron-left" /></button>
            <button id="optNext" type="button" class="opt-step" :title="t('op.cal.next')" :aria-label="t('op.cal.next')"
                    @click="step(1)"><AppIcon name="chevron-right" /></button>
          </span>
          <button id="optToday" type="button" class="btn btn-sm" @click="choose(today, today.slice(0, 7))">{{
            t('op.cal.jumpToday') }}</button>
          <span class="opt-foot-gap"></span>
          <span class="opt-legend"><span class="opt-tick"></span>{{ t('op.cal.legend.done') }}</span>
          <span class="opt-legend"><span class="opt-tick is-open"></span>{{ t('op.cal.legend.open') }}</span>
          <span class="opt-legend"><span class="opt-tick is-none"></span>{{ t('op.cal.legend.idle') }}</span>
        </div>
        <div class="opt-grid-scroll">
          <div class="opt-grid" role="grid" :aria-label="t('op.cal.gridAria')">
            <span v-for="w in weekdayNames()" :key="w" class="opt-dow">{{ w }}</span>
            <template v-for="(c, i) in cells" :key="c ? c.key : `pad${i}`">
              <!-- the padding is cells too, so the month reads as one block -->
              <div v-if="!c" class="opt-cell opt-cell-out" aria-hidden="true"></div>
              <button v-else type="button" class="opt-cell" :class="{ 'has-runs': c.slot, 'is-sel': c.key === selected,
                      'has-open': c.slot?.pending, 'is-today': c.key === today }" :data-day="c.key"
                      :aria-pressed="c.key === selected" :aria-label="cellAria(c.key)" @click="choose(c.key)">
                <span class="opt-cell-top">
                  <span class="opt-cell-day">{{ c.day }}</span>
                  <span v-if="c.slot?.pending" class="opt-cell-open">{{ t('op.cal.openN', { n: c.slot.pending }) }}</span>
                </span>
                <!-- one tick per run, coloured by whether that run still holds a decision -->
                <span v-if="c.slot" class="opt-ticks"><span v-for="r in c.slot.runs.slice(0, 3)" :key="r.id"
                      class="opt-tick" :class="{ 'is-open': r.pending }"></span></span>
                <span class="opt-cell-n"><template v-if="c.slot">{{ c.slot.runs.length > 3
                  ? t('op.cal.runsAnd', { n: c.slot.runs.length, s: c.slot.total })
                  : t('op.cal.sugShort', { n: c.slot.total }) }}</template></span>
              </button>
            </template>
          </div>
        </div>
        <div class="opt-cal-foot">
          <template v-if="!foot.runs">
            <span>{{ t('op.cal.foot.noRuns') }}</span>
            <span class="opt-foot-gap"></span>
            <span>{{ t('op.cal.foot.noRunsHint') }}</span>
          </template>
          <template v-else>
            <span>{{ t('op.cal.foot.runs', { n: fmtInt(foot.runs) }) }}</span>
            <span>{{ t('op.cal.foot.sug', { n: fmtInt(foot.sug) }) }}</span>
            <span :class="foot.open ? 'opt-foot-open' : 'opt-foot-done'">{{ foot.open
              ? t('op.cal.foot.open', { n: fmtInt(foot.open) }) : t('op.cal.foot.allDone') }}</span>
            <span class="opt-foot-gap"></span>
            <span>{{ t('op.cal.foot.idle', { n: fmtInt(foot.idle) }) }}</span>
          </template>
        </div>
      </div>
      <DayRail :day="selected" :slot="days.get(selected)" :today="today" />
    </div>
    <div v-else class="empty">{{ t('op.emptyRuns') }}</div>
  </div>
</template>
