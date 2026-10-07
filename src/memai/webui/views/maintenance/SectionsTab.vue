<script setup lang="ts">
/* The bodies the store could not read into their fields, each opening the record that settles it. */
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue';
import { fmtInt } from '../../core/dom.ts';
import { openRecord } from '../../core/nav.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { SectionQueue } from '../../api/types.ts';
import LoadFailed from '../../components/LoadFailed.vue';
import StatusTag from '../../components/StatusTag.vue';
import TypeTag from '../../components/TypeTag.vue';
import OpButton from './OpButton.vue';
import { useMaintenance } from './maintenance.ts';

const ctx = useMaintenance();
const queue = shallowRef<SectionQueue | null>(null);
const error = ref<Error | null>(null);
let alive = true;
onBeforeUnmount(() => { alive = false; });

async function load() {
  try {
    const s = await client.maintenance.sectionsQueue();
    if (!alive) return;
    queue.value = s;
    error.value = null;
    ctx.badge('sections', s.queue.length);
  } catch (err) {
    if (alive) error.value = err instanceof Error ? err : new Error(String(err));
  }
}
void load();
watch(ctx.changed, () => void load());

/* a store nobody has read, one that came out clean, and one holding bodies a human must settle */
const head = computed(() => {
  const s = queue.value;
  if (!s) return '';
  return !s.migrated ? t('mn.sc.notRead')
    : s.queue.length ? t('mn.sc.pending', { n: fmtInt(s.queue.length) }) : t('mn.sc.clean');
});
</script>

<template>
  <section class="panel">
    <h3 class="panel-title">{{ t('mn.sc.head') }}
      <OpButton op="sectionize" :label="t('mn.sc.run')" /></h3>
    <p class="intro">{{ t('mn.sc.intro') }}</p>
    <div class="panel-body" id="scBody">
      <LoadFailed v-if="error" :message="error.message" @retry="load" />
      <div v-else-if="!queue" class="loading"><span class="spin"></span></div>
      <div v-else-if="!queue.queue.length" class="empty">{{ head }}</div>
      <template v-else>
        <p class="intro">{{ head }}</p>
        <button v-for="e in queue.queue" :key="e.uid" type="button" class="sc-row" :data-uid="e.uid"
                @click="openRecord(e.uid)">
          <span class="sc-row-head"><TypeTag :type="e.type" /> <StatusTag :status="e.status" />
            <span class="sc-row-uid">{{ e.uid }}</span>
            <span class="sc-domain">{{ e.domain || '' }}</span></span>
          <span class="sc-detail">{{ e.detail }}</span>
          <span class="sc-snippet">{{ e.snippet }}</span>
        </button>
      </template>
    </div>
  </section>
</template>
