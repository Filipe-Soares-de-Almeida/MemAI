<script setup lang="ts">
/* The audit trail over the edits table, one heading per local day, newest first. */
import { computed, onBeforeUnmount, ref, shallowRef } from 'vue';
import { dayKey, fmtInt } from '../../core/dom.ts';
import { openRecord } from '../../core/nav.ts';
import { typeClass } from '../../core/shared.js';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { AuditEntry } from '../../api/types.ts';
import LoadFailed from '../../components/LoadFailed.vue';
import { fmtDayKey, groupBy } from './maintenance.ts';

const entries = shallowRef<AuditEntry[] | null>(null);
const error = ref<Error | null>(null);
let alive = true;
onBeforeUnmount(() => { alive = false; });

async function load() {
  try {
    const r = await client.audit({ limit: 200 });
    if (!alive) return;
    entries.value = r.entries;
    error.value = null;
  } catch (err) {
    if (alive) error.value = err instanceof Error ? err : new Error(String(err));
  }
}
void load();

const pad2 = (n: number) => String(n).padStart(2, '0');
/* an entry's day comes from the reader's clock, not from the first ten characters of a UTC stamp */
const days = computed(() => groupBy(entries.value ?? [], e => {
  const d = new Date(e.edited_at);
  return isNaN(d.getTime()) ? String(e.edited_at).slice(0, 10) : dayKey(d);
}));
const todayKey = dayKey(new Date());

function timeOf(iso: string): string {
  const at = new Date(iso);
  return isNaN(at.getTime()) ? '' : `${pad2(at.getHours())}:${pad2(at.getMinutes())}`;
}
</script>

<template>
  <section class="panel">
    <h3 class="panel-title">{{ t('mn.log.head') }}
      <button class="btn btn-sm" id="logRefresh" @click="load">{{ t('common.refresh') }}</button></h3>
    <p class="intro">{{ t('mn.au.aside') }}</p>
    <div class="mnt-log" id="logBody">
      <LoadFailed v-if="error" :message="error.message" @retry="load" />
      <div v-else-if="!entries" class="loading"><span class="spin"></span></div>
      <div v-else-if="!entries.length" class="empty">{{ t('mn.au.empty') }}</div>
      <template v-else>
        <template v-for="[key, rows] in days" :key="key">
          <div class="mnt-day">
            <span class="mnt-day-name">{{ key === todayKey ? t('mn.log.today') : fmtDayKey(key) }}</span>
            <span class="mnt-group-rule"></span>
            <span class="mnt-group-meta">{{ t('mn.log.count', { n: fmtInt(rows.length) }) }}</span>
          </div>
          <button v-for="e in rows" :key="e.id" type="button" class="mnt-ev" :data-uid="e.memory_uid"
                  :title="e.note || ''" :aria-label="t('a11y.openRecord', { uid: e.memory_uid })"
                  @click="openRecord(e.memory_uid)">
            <span class="mnt-ev-time">{{ timeOf(e.edited_at) }}</span>
            <span class="dot" :class="typeClass(e.type) || undefined"></span>
            <span class="mnt-ev-main">
              <span class="mnt-ev-title">{{ e.note || t('mn.au.contentEdit') }}</span>
              <span class="mnt-ev-detail">
                <span class="mnt-ev-uid">{{ e.memory_uid }}</span>
                <span class="mnt-ev-domain">{{ e.domain || '—' }}</span>
              </span>
            </span>
            <span class="mnt-ev-delta">{{ e.content_changed ? `${fmtInt(e.prev_len)} → ${fmtInt(e.new_len)}` : '' }}</span>
          </button>
        </template>
      </template>
    </div>
  </section>
</template>
