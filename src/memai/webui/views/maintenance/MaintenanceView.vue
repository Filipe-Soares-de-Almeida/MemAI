<script setup lang="ts">
/* The store's own upkeep as workspaces behind one tab strip (?tab). A tab is built when first
   opened and then kept, hidden: a dedup scan is slow to redo and a scrolled log is a place you were. */
import { onBeforeUnmount, provide, reactive, ref, shallowRef } from 'vue';
import type { Component } from 'vue';
import { fmtBytes, fmtInt } from '../../core/dom.ts';
import { replaceParams } from '../../core/router.ts';
import type { ViewProps } from '../../core/vue.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { Health } from '../../api/types.ts';
import LoadFailed from '../../components/LoadFailed.vue';
import BackupsTab from './BackupsTab.vue';
import DupesTab from './DupesTab.vue';
import LogTab from './LogTab.vue';
import SectionsTab from './SectionsTab.vue';
import StorageTab from './StorageTab.vue';
import WardenTab from './WardenTab.vue';
import { MAINTENANCE, TABS, isTab } from './maintenance.ts';
import type { Tab } from './maintenance.ts';

const props = defineProps<ViewProps>();

const PANES: Record<Tab, Component> = {
  backups: BackupsTab, storage: StorageTab, sections: SectionsTab, dupes: DupesTab, log: LogTab,
  warden: WardenTab,
};

const asked = props.params.get('tab');
const current = ref<Tab>(isTab(asked) ? asked : TABS[0]);
const built = reactive(new Set<Tab>([current.value]));
const badges = reactive<Partial<Record<Tab, number>>>({});
let alive = true;
onBeforeUnmount(() => { alive = false; });

/* a tab is an address worth deep-linking, not a step worth pressing Back through */
function show(id: Tab) {
  current.value = id;
  built.add(id);
  replaceParams('maintenance', { tab: id });
}

function onKey(e: KeyboardEvent) {
  const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
  if (!step) return;
  e.preventDefault();
  const next = TABS[(TABS.indexOf(current.value) + step + TABS.length) % TABS.length];
  show(next);
  (e.currentTarget as HTMLElement).querySelector<HTMLElement>(`#mntTab-${next}`)?.focus();
}

/* the store line, and the numbers the backups and storage tabs are drawn from, fetched once for both */
const health = shallowRef<Health | null>(null);
const healthError = ref<Error | null>(null);
async function loadHealth() {
  try {
    const h = await client.maintenance.health();
    if (!alive) return;
    health.value = h;
    healthError.value = null;
  } catch (err) {
    if (alive) healthError.value = err instanceof Error ? err : new Error(String(err));
  }
}

const changed = ref(0);
provide(MAINTENANCE, {
  health, loadHealth, changed, show,
  touch: () => { void loadHealth(); changed.value++; },
  badge: (id, n) => { badges[id] = n; },
});

void loadHealth();
/* the sections count is a badge on a closed tab too, so its queue is asked for either way */
if (current.value !== 'sections') {
  client.maintenance.sectionsQueue().then(s => { if (alive) badges.sections = s.queue.length; }).catch(() => {});
}
</script>

<template>
  <div class="anim">
    <h2 class="sr-only">{{ t('mn.title') }}</h2>
    <div class="mnt-head">
      <div class="mnt-tabs" role="tablist" :aria-label="t('mn.title')" @keydown="onKey">
        <button v-for="id in TABS" :key="id" type="button" class="mnt-tab" role="tab" :data-tab="id"
                :id="`mntTab-${id}`" :aria-controls="`mntPanel-${id}`" :aria-selected="id === current ? 'true' : 'false'"
                :tabindex="id === current ? 0 : -1" @click="show(id)">
          {{ t(`mn.tab.${id}`) }}<span class="mnt-tab-badge" :data-badge="id" :hidden="!badges[id]">{{
            badges[id] ? fmtInt(badges[id]) : '' }}</span></button>
      </div>
      <!-- Which store these tabs act on; its condition is read on Health, which owns the diagnosis. -->
      <div class="mnt-state">
        <span class="mnt-store" id="mntStore"><LoadFailed v-if="healthError" :message="healthError.message"
          @retry="loadHealth" /><template v-else>{{
          health ? `${health.project} · ${fmtBytes(health.file.size)}` : t('mn.checks.running') }}</template></span>
        <button class="btn btn-sm" id="hRefresh" @click="loadHealth">{{ t('common.refresh') }}</button>
      </div>
    </div>
    <div class="mnt-panels">
      <section v-for="id in TABS" :key="id" class="mnt-panel" role="tabpanel" :data-panel="id" :id="`mntPanel-${id}`"
               :aria-labelledby="`mntTab-${id}`" tabindex="0" :hidden="id !== current">
        <component :is="PANES[id]" v-if="built.has(id)" />
      </section>
    </div>
  </div>
</template>
