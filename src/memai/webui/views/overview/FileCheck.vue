<script setup lang="ts">
/* The store file's two checks, run when asked: whether SQLite reads it clean, and whether the
   keyword index holds the rows it indexes. */
import { computed, ref } from 'vue';
import { fmtInt } from '../../core/dom.ts';
import { go } from '../../core/router.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';

const checking = ref(false);
const verdict = ref<{ passed: number; dbOk: boolean; detail: string } | null>(null);
const failing = computed(() => !!verdict.value && verdict.value.passed < 2);

async function check() {
  checking.value = true;
  try {
    const h = await client.maintenance.health();
    /* an index that passes its own check but misses rows cannot be trusted either */
    const indexOk = h.fts.ok && h.fts.rows === h.fts.expected;
    verdict.value = {
      passed: Number(h.integrity.ok) + Number(indexOk),
      dbOk: h.integrity.ok,
      detail: [
        h.integrity.ok ? '' : (h.integrity.detail || t('ov.sym.file.dbBad')),
        indexOk ? '' : (h.fts.detail || t('ov.sym.file.rows',
          { a: fmtInt(h.fts.rows), b: fmtInt(h.fts.expected) })),
      ].filter(Boolean).join(' · '),
    };
  } catch {
    /* the last verdict stands, ready to check again */
  } finally {
    checking.value = false;
  }
}

/* Grey only before the first check: a clean verdict is coloured like every zero count above it. */
const severity = computed(() => {
  const v = verdict.value;
  if (!v) return '';
  return v.passed === 2 ? 'sev-info' : v.dbOk ? 'sev-warn' : 'sev-bad';
});

/* the repairs live with the other operations on the file */
const act = () => (failing.value ? go('maintenance', { tab: 'storage' }) : check());
</script>

<!-- The row's title carries the whole verdict, SQLite's own message included. -->
<template>
  <div class="hx-sym" data-sym="file" :title="verdict ? verdict.detail || t('ov.sym.file.okLong') : undefined">
    <span class="hx-sev" :class="severity"></span>
    <span class="hx-sym-name">{{ t('ov.sym.file') }}</span>
    <span class="hx-sym-n">{{ verdict ? `${verdict.passed}/2` : '—' }}</span>
    <span class="hx-sym-share">{{ !verdict ? t('ov.sym.file.notChecked')
      : failing ? t('ov.sym.file.bad', { n: 2 - verdict.passed }) : t('ov.sym.file.ok') }}</span>
    <button type="button" class="btn btn-sm" :disabled="checking" @click="act">{{
      checking ? t('ov.sym.file.checking') : failing ? t('ov.sym.file.repair') : t('ov.sym.file.act') }}</button>
  </div>
</template>
