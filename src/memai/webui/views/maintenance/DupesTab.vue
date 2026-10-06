<script setup lang="ts">
/* A dedup scan on demand, since the pairwise sweep is slow: each pair can be linked as duplicates,
   and either side opened or archived. */
import { onBeforeUnmount, reactive, ref, shallowRef } from 'vue';
import { fmtDate } from '../../core/dom.ts';
import { byDomainPath } from '../../core/domains.ts';
import { openRecord } from '../../core/nav.ts';
import { getDomains, typeItems } from '../../core/shared.js';
import { failed, promptModal, toast } from '../../core/ui.js';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { DedupPair } from '../../api/types.ts';
import LoadFailed from '../../components/LoadFailed.vue';
import Picker from '../../components/Picker.vue';
import StatusTag from '../../components/StatusTag.vue';
import TypeTag from '../../components/TypeTag.vue';
import UidChip from '../../components/UidChip.vue';
import { useMaintenance } from './maintenance.ts';

const ctx = useMaintenance();
const types = typeItems({ any: t('common.allTypes') });

const threshold = ref('0.60');
const type = ref('');
const domain = ref('');
const domains = ref<string[]>([]);
const state = ref<'idle' | 'loading' | 'ready' | 'failed'>('idle');
const error = ref('');
const pairs = shallowRef<DedupPair[]>([]);
const linked = reactive(new Set<number>());
/* `<pair>:<side>` of each card archived from here */
const decided = reactive(new Set<string>());
let alive = true;
onBeforeUnmount(() => { alive = false; });

getDomains().then((ds: { domain: string }[]) => {
  if (alive) domains.value = ds.slice().sort(byDomainPath).map(d => d.domain);
}).catch(() => {});

async function run() {
  state.value = 'loading';
  try {
    const qs = new URLSearchParams({ threshold: threshold.value });
    if (type.value) qs.set('type', type.value);
    if (domain.value.trim()) qs.set('domain', domain.value.trim());
    const r = await client.maintenance.dedup(qs);
    if (!alive) return;
    ctx.badge('dupes', r.pairs.length);
    linked.clear();
    decided.clear();
    pairs.value = r.pairs;
    state.value = 'ready';
  } catch (err) {
    if (!alive) return;
    error.value = err instanceof Error ? err.message : '';
    state.value = 'failed';
  }
}

const pct = (p: DedupPair) => (p.ratio * 100).toFixed(0);

async function link(i: number) {
  const p = pairs.value[i];
  try {
    await client.relations.create({ from_uid: p.a.uid, to_uid: p.b.uid, relation_type: 'duplicates',
                                    note: t('mn.dd.linkNote', { p: pct(p) }) });
    toast(t('mn.dd.linked'), 'ok');
    linked.add(i);
  } catch (err) { failed('err.relation', err); }
}

/* the card sinks a step rather than dimming: its snippet is how you check you archived the right one */
async function archive(i: number, side: 'a' | 'b') {
  const reason = await promptModal({ title: t('mn.dd.archTitle'), label: t('bulk.reason.label'),
                                     placeholder: t('mn.dd.archPh'), okLabel: t('common.archive'), danger: true });
  if (reason === null) return;
  try {
    await client.memories.status(pairs.value[i][side].uid,
                                 { status: 'archived', reason: reason || t('mn.dd.dupReason') });
    toast(t('dr.archived'), 'ok');
    decided.add(`${i}:${side}`);
  } catch (err) { failed('err.status', err); }
}
</script>

<template>
  <section class="panel">
    <h3 class="panel-title">{{ t('mn.dd.head') }}</h3>
    <p class="intro">{{ t('mn.dd.aside') }}</p>
    <div class="list-toolbar toolbar-sm">
      <label class="inline-label">
        {{ t('mn.dd.threshold') }} <input type="range" id="ddThr" min="0.45" max="0.95" step="0.05"
                                          :value="threshold" @input="threshold = ($event.target as HTMLInputElement).value">
        <b id="ddThrVal">{{ Number(threshold).toFixed(2) }}</b></label>
      <Picker id="ddType" v-model="type" :items="types" :aria-label="t('common.allTypes')" />
      <input type="text" id="ddDomain" v-model="domain" :placeholder="t('mn.dd.domainPh')"
             :aria-label="t('mn.dd.domainPh')" list="ddDomainsDL" style="max-width:200px">
      <datalist id="ddDomainsDL"><option v-for="d in domains" :key="d" :value="d"></option></datalist>
      <button class="btn btn-solid btn-sm" id="ddRun" @click="run">{{ t('mn.dd.run') }}</button>
    </div>
    <div class="panel-body" id="ddBody">
      <div v-if="state === 'idle'" class="empty">{{ t('mn.dd.hint') }}</div>
      <div v-else-if="state === 'loading'" class="loading"><span class="spin"></span></div>
      <!-- the scan's inputs are still on screen, so retrying is pressing the button again -->
      <LoadFailed v-else-if="state === 'failed'" :message="error" @retry="run" />
      <div v-else-if="!pairs.length" class="empty">{{ t('mn.dd.none') }}</div>
      <template v-else>
        <div v-for="(p, i) in pairs" :key="`${p.a.uid}:${p.b.uid}`" class="dedup-pair">
          <div style="display:flex;justify-content:space-between;align-items:baseline">
            <span class="hint-sm">{{ t('mn.dd.overlap') }} <b style="color:var(--ink)">{{ pct(p) }}%</b></span>
            <button class="btn btn-sm" :data-linkdup="i" :disabled="linked.has(i)" @click="link(i)">{{
              t('mn.dd.linkDup') }}</button>
          </div>
          <div class="ratio-bar"><div class="ratio-fill" :style="{ '--v': p.ratio.toFixed(3) }"></div></div>
          <div class="pair-cards">
            <div v-for="side in (['a', 'b'] as const)" :key="side" class="pair-card"
                 :class="{ decided: decided.has(`${i}:${side}`) }">
              <div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap">
                <TypeTag :type="p[side].type" /> <UidChip :uid="p[side].uid" /> <StatusTag :status="p[side].status" />
                <span class="hint-sm">{{ fmtDate(p[side].created_at) }}</span>
              </div>
              <span v-if="p[side].domain" class="chip">{{ p[side].domain }}</span>
              <div class="snippet">{{ p[side].content }}</div>
              <div class="act-row">
                <button class="btn btn-sm" :data-openm="p[side].uid" @click="openRecord(p[side].uid)">{{
                  t('common.openRecord') }}</button>
                <button class="btn btn-sm" :data-archm="p[side].uid" @click="archive(i, side)">{{
                  t('mn.dd.archiveThis') }}</button>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>
  </section>
</template>
