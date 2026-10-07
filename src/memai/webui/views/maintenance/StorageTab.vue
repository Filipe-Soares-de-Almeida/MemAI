<script setup lang="ts">
/* The store file: what it is made of, the backup-then-compact sequence, the two repairs, and how
   long diagram renders are kept; all drawn from the view's health answer. */
import { computed, ref, watch } from 'vue';
import { fmtAgo, fmtBytes, fmtInt } from '../../core/dom.ts';
import { failed, toast } from '../../core/ui.js';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import Picker from '../../components/Picker.vue';
import OpButton from './OpButton.vue';
import StackedBar from './StackedBar.vue';
import { RECLAIM_WARN, RETENTION, useMaintenance } from './maintenance.ts';
import type { Segment } from './maintenance.ts';

const { health } = useMaintenance();

const keepItems = RETENTION.map(m => ({ value: m, label: t(`mn.rn.mode.${m}`) }));
/* the control shows the stored value; only a pick writes one */
const keep = ref<string>(RETENTION[0]);
watch(health, h => {
  if (h && keepItems.some(it => it.value === h.renders.retention)) keep.value = h.renders.retention;
}, { immediate: true });

async function setKeep(mode: string) {
  try {
    await client.config.set({ svg_retention: mode });
    toast(t('mn.msg.retention', { mode: t(`mn.rn.mode.${mode as typeof RETENTION[number]}`) }), 'ok');
  } catch (err) { failed('err.maintenance', err); }
}

const used = computed(() => health.value ? Math.max(0, health.value.file.size - health.value.file.reclaimable) : 0);
const parts = computed<Segment[]>(() => {
  const f = health.value?.file;
  if (!f) return [];
  const out = [{ name: t('mn.st.inUse'), value: used.value, fill: 'var(--accent)' },
               { name: t('mn.st.free'), value: f.reclaimable, fill: 'var(--warn)' }];
  if (f.wal_size) out.push({ name: t('mn.st.wal'), value: f.wal_size, fill: 'rgba(187, 134, 252, .42)' });
  return out;
});
/* the folder and the filename name the store; the rest of an absolute path is the machine */
const shortPath = computed(() => health.value ? health.value.file.path.split(/[\\/]/).slice(-2).join('/') : '');
/* compacting discards what an undo would need, so the button stays shut until a backup exists */
const newest = computed(() => health.value?.backups[0] ?? null);
</script>

<template>
  <div class="mnt-stack">
    <section class="panel">
      <h3 class="panel-title">{{ t('mn.st.file') }}
        <span class="panel-aside" id="stPath" :title="health ? health.file.path : ''">{{ shortPath }}</span></h3>
      <div id="stFile">
        <template v-if="health">
          <StackedBar :parts="parts" legend />
          <div v-if="health.file.reclaimable > RECLAIM_WARN && health.file.compact_reason === 'vector_store'"
               class="mnt-note">{{ t('mn.h.diskFromVectors') }}</div>
        </template>
        <div v-else class="loading"><span class="spin"></span></div>
      </div>
    </section>

    <section class="panel">
      <h3 class="panel-title">{{ t('mn.st.compact') }}
        <span class="panel-aside">{{ t('mn.st.compactAside') }}</span></h3>
      <div id="stSteps">
        <template v-if="health">
          <div class="mnt-step" :class="newest ? 'done' : 'now'">
            <span class="mnt-step-n">{{ newest ? '✓' : '1' }}</span>
            <div class="mnt-step-body">
              <div class="mnt-step-name">{{ t('mn.st.step.backup') }}</div>
              <div class="mnt-step-note">{{ newest
                ? t('mn.st.step.backupDone', { when: fmtAgo(newest.mtime), size: fmtBytes(newest.size) })
                : t('mn.st.step.backupNone') }}</div>
            </div>
            <OpButton op="backup" :label="t('mn.op.backup')" />
          </div>
          <div class="mnt-step" :class="newest ? 'now' : 'locked'">
            <span class="mnt-step-n">2</span>
            <div class="mnt-step-body">
              <div class="mnt-step-name">{{ t('mn.st.step.compact') }}</div>
              <div class="mnt-step-note">{{ newest
                ? t('mn.st.step.compactNote', { a: fmtBytes(health.file.size), b: fmtBytes(used) })
                : t('mn.st.step.locked') }}</div>
            </div>
            <OpButton op="vacuum" cls="btn btn-sm btn-danger" :label="t('mn.op.vacuum')" :disabled="!newest" />
          </div>
        </template>
      </div>
    </section>

    <!-- The rebuild destroys nothing, so it is always offered; cleaning references deletes rows, so
         only once there is something to delete. -->
    <section class="panel">
      <h3 class="panel-title">{{ t('mn.fix.title') }}</h3>
      <div id="stFix">
        <template v-if="health">
          <div class="mnt-fix">
            <span class="mnt-fix-name">{{ t('mn.h.fts') }}</span>
            <span class="mnt-fix-state">{{ health.fts.detail || t('mn.h.ftsConsistent') }} · {{
              t('mn.h.rows', { a: fmtInt(health.fts.rows), b: fmtInt(health.fts.expected) }) }}</span>
            <OpButton op="fts" :label="t('mn.op.fts')" />
          </div>
          <div class="mnt-fix">
            <span class="mnt-fix-name">{{ t('mn.fix.refs') }}</span>
            <span class="mnt-fix-state">{{ health.relations.orphans === 0
              ? t('mn.h.noOrphans') : t('mn.h.orphanEdges', { n: health.relations.orphans }) }}</span>
            <OpButton v-if="health.relations.orphans" op="orphans" cls="btn btn-sm btn-danger"
                      :label="t('mn.op.orphans')" />
          </div>
        </template>
      </div>
    </section>

    <section class="panel">
      <h3 class="panel-title">{{ t('mn.rn.title') }}</h3>
      <p class="intro">{{ t('mn.rn.aside') }}</p>
      <div class="list-toolbar toolbar-sm">
        <label class="inline-label">{{ t('mn.rn.keep') }}
          <Picker id="rnKeep" v-model="keep" :items="keepItems" :aria-label="t('mn.rn.keep')" @pick="setKeep" /></label>
        <OpButton op="prune-renders" :label="t('mn.rn.now')" />
        <OpButton op="prune-renders-all" :label="t('mn.rn.all')" />
      </div>
      <div id="rnBody" class="hint">{{ !health ? '—' : health.renders.files
        ? t('mn.rn.usage', { n: fmtInt(health.renders.files), size: fmtBytes(health.renders.bytes) })
        : t('mn.rn.empty') }}</div>
    </section>
  </div>
</template>
