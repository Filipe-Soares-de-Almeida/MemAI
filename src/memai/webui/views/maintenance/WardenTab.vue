<script setup lang="ts">
/* The reminders a session meets -- warden, open tasks, overdue checkpoints, confidence -- each
   switched on or off with its own span; a span stays on file while its reminder is off. */
import { onBeforeUnmount, ref } from 'vue';
import { failed, toast } from '../../core/ui.js';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { ConfigSaved } from '../../api/types.ts';
import Picker from '../../components/Picker.vue';
import { CHECKPOINT_DAYS, CONFIDENCE_MINUTES, UNVERIFIED_DAYS, WARDEN_MINUTES } from './maintenance.ts';

const onOffItems = [{ value: 'on', label: t('mn.wd.on') }, { value: 'off', label: t('mn.wd.off') }];
const minuteItems = (list: number[]) => list.map(n => ({ value: String(n), label: t('mn.wd.mins', { n }) }));
const dayItems = (list: number[]) => list.map(n => ({ value: String(n), label: t('mn.wd.days', { n }) }));
const everyItems = minuteItems(WARDEN_MINUTES);
const checkpointDayItems = dayItems(CHECKPOINT_DAYS);
const unverifiedDayItems = dayItems(UNVERIFIED_DAYS);
const confidenceEveryItems = minuteItems(CONFIDENCE_MINUTES);

/* the controls show the stored values; only a pick writes one */
const wdOn = ref(onOffItems[0].value);
const wdEvery = ref(everyItems[0].value);
const taOn = ref(onOffItems[0].value);
const taEvery = ref(everyItems[0].value);
const cbOn = ref(onOffItems[0].value);
const cbDays = ref('14');
const caOn = ref(onOffItems[0].value);
const caDays = ref('30');
const caEvery = ref('60');
let alive = true;
onBeforeUnmount(() => { alive = false; });

const known = (items: { value: string }[], value: string, into: { value: string }) => {
  if (items.some(it => it.value === value)) into.value = value;
};
const onOff = (on: boolean) => on ? 'on' : 'off';
client.config.get().then(cfg => {
  if (!alive) return;
  known(onOffItems, onOff(cfg.warden_enabled), wdOn);
  known(everyItems, String(cfg.warden_minutes), wdEvery);
  known(onOffItems, onOff(cfg.task_ask_enabled), taOn);
  known(everyItems, String(cfg.task_ask_minutes), taEvery);
  known(onOffItems, onOff(cfg.checkpoint_block_enabled), cbOn);
  known(checkpointDayItems, String(cfg.checkpoint_days), cbDays);
  known(onOffItems, onOff(cfg.confidence_ask_enabled), caOn);
  known(unverifiedDayItems, String(cfg.unverified_days), caDays);
  known(confidenceEveryItems, String(cfg.confidence_ask_minutes), caEvery);
}).catch(() => {});

async function save(body: Partial<ConfigSaved>, msg: (cfg: ConfigSaved) => string) {
  try {
    toast(msg(await client.config.set(body)), 'ok');
  } catch (err) { failed('err.maintenance', err); }
}

const pickWarden = (v: string) => save({ warden_enabled: v === 'on' },
  cfg => v === 'on' ? t('mn.msg.wardenOn', { n: cfg.warden_minutes }) : t('mn.msg.wardenOff'));
const pickWardenEvery = (v: string) => save({ warden_minutes: Number(v) }, () => t('mn.msg.wardenEvery', { n: v }));
const pickTasks = (v: string) => save({ task_ask_enabled: v === 'on' },
  cfg => v === 'on' ? t('mn.msg.taskOn', { n: cfg.task_ask_minutes }) : t('mn.msg.taskOff'));
const pickTasksEvery = (v: string) => save({ task_ask_minutes: Number(v) }, () => t('mn.msg.taskEvery', { n: v }));
const pickBlock = (v: string) => save({ checkpoint_block_enabled: v === 'on' },
  cfg => v === 'on' ? t('mn.msg.checkpointOn', { n: cfg.checkpoint_days }) : t('mn.msg.checkpointOff'));
const pickBlockDays = (v: string) => save({ checkpoint_days: Number(v) }, () => t('mn.msg.checkpointDays', { n: v }));
const pickConfidence = (v: string) => save({ confidence_ask_enabled: v === 'on' },
  cfg => v === 'on' ? t('mn.msg.confidenceOn', { n: cfg.unverified_days }) : t('mn.msg.confidenceOff'));
const pickConfidenceDays = (v: string) => save({ unverified_days: Number(v) },
  () => t('mn.msg.confidenceDays', { n: v }));
const pickConfidenceEvery = (v: string) => save({ confidence_ask_minutes: Number(v) },
  () => t('mn.msg.confidenceEvery', { n: v }));
</script>

<template>
  <section class="panel mnt-short">
    <h3 class="panel-title">{{ t('mn.wd.title') }}
      <span class="panel-aside">{{ t('mn.wd.aside') }}</span></h3>
    <div class="list-toolbar toolbar-sm">
      <label class="inline-label">{{ t('mn.wd.state') }}
        <Picker id="wdOn" v-model="wdOn" :items="onOffItems" :aria-label="t('mn.wd.state')" @pick="pickWarden" /></label>
      <label class="inline-label">{{ t('mn.wd.every') }}
        <Picker id="wdEvery" v-model="wdEvery" :items="everyItems" :aria-label="t('mn.wd.every')"
                @pick="pickWardenEvery" /></label>
    </div>
    <p class="hint">{{ t('mn.wd.body') }}</p>
  </section>
  <section class="panel mnt-short">
    <h3 class="panel-title">{{ t('mn.ta.title') }}
      <span class="panel-aside">{{ t('mn.ta.aside') }}</span></h3>
    <div class="list-toolbar toolbar-sm">
      <label class="inline-label">{{ t('mn.ta.state') }}
        <Picker id="taOn" v-model="taOn" :items="onOffItems" :aria-label="t('mn.ta.state')" @pick="pickTasks" /></label>
      <label class="inline-label">{{ t('mn.ta.every') }}
        <Picker id="taEvery" v-model="taEvery" :items="everyItems" :aria-label="t('mn.ta.every')"
                @pick="pickTasksEvery" /></label>
    </div>
    <p class="hint">{{ t('mn.ta.body') }}</p>
  </section>
  <section class="panel mnt-short">
    <h3 class="panel-title">{{ t('mn.cb.title') }}
      <span class="panel-aside">{{ t('mn.cb.aside') }}</span></h3>
    <div class="list-toolbar toolbar-sm">
      <label class="inline-label">{{ t('mn.cb.state') }}
        <Picker id="cbOn" v-model="cbOn" :items="onOffItems" :aria-label="t('mn.cb.state')" @pick="pickBlock" /></label>
      <label class="inline-label">{{ t('mn.cb.days') }}
        <Picker id="cbDays" v-model="cbDays" :items="checkpointDayItems" :aria-label="t('mn.cb.days')"
                @pick="pickBlockDays" /></label>
    </div>
    <p class="hint">{{ t('mn.cb.body') }}</p>
  </section>
  <section class="panel mnt-short">
    <h3 class="panel-title">{{ t('mn.ca.title') }}
      <span class="panel-aside">{{ t('mn.ca.aside') }}</span></h3>
    <div class="list-toolbar toolbar-sm">
      <label class="inline-label">{{ t('mn.ca.state') }}
        <Picker id="caOn" v-model="caOn" :items="onOffItems" :aria-label="t('mn.ca.state')" @pick="pickConfidence" /></label>
      <label class="inline-label">{{ t('mn.ca.days') }}
        <Picker id="caDays" v-model="caDays" :items="unverifiedDayItems" :aria-label="t('mn.ca.days')"
                @pick="pickConfidenceDays" /></label>
      <label class="inline-label">{{ t('mn.ca.every') }}
        <Picker id="caEvery" v-model="caEvery" :items="confidenceEveryItems" :aria-label="t('mn.ca.every')"
                @pick="pickConfidenceEvery" /></label>
    </div>
    <p class="hint">{{ t('mn.ca.body') }}</p>
  </section>
</template>
