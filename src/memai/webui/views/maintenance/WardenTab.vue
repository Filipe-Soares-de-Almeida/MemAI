<script setup lang="ts">
/* The two reminders the Stop hook sends a session, the warden and the open tasks, each switched
   on or off with its own interval; the interval stays on file while its reminder is off. */
import { onBeforeUnmount, ref } from 'vue';
import { failed, toast } from '../../core/ui.js';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import Picker from '../../components/Picker.vue';
import { WARDEN_MINUTES } from './maintenance.ts';

const onOffItems = [{ value: 'on', label: t('mn.wd.on') }, { value: 'off', label: t('mn.wd.off') }];
const everyItems = WARDEN_MINUTES.map(n => ({ value: String(n), label: t('mn.wd.mins', { n }) }));

/* the controls show the stored values; only a pick writes one */
const wdOn = ref(onOffItems[0].value);
const wdEvery = ref(everyItems[0].value);
const taOn = ref(onOffItems[0].value);
const taEvery = ref(everyItems[0].value);
let alive = true;
onBeforeUnmount(() => { alive = false; });

const known = (items: { value: string }[], value: string, into: { value: string }) => {
  if (items.some(it => it.value === value)) into.value = value;
};
client.config.get().then(cfg => {
  if (!alive) return;
  known(onOffItems, cfg.warden_enabled ? 'on' : 'off', wdOn);
  known(everyItems, String(cfg.warden_minutes), wdEvery);
  known(onOffItems, cfg.task_ask_enabled ? 'on' : 'off', taOn);
  known(everyItems, String(cfg.task_ask_minutes), taEvery);
}).catch(() => {});

async function save(body: Record<string, unknown>, msg: (cfg: { warden_minutes: number; task_ask_minutes: number }) => string) {
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
</template>
