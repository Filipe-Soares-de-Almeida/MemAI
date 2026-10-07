<script setup lang="ts">
/* Likely duplicates, counted only when asked: the pairwise text comparison is too slow to run on
   every landing. */
import { ref } from 'vue';
import { fmtInt } from '../../core/dom.ts';
import { go } from '../../core/router.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';

const scanning = ref(false);
const found = ref<{ pairs: number; overlap: number } | null>(null);

async function scan() {
  scanning.value = true;
  try {
    const r = await client.maintenance.dedup({ limit: 60 });
    found.value = { pairs: r.pairs.length, overlap: Math.round(r.threshold * 100) };
  } catch {
    /* the row stays unscanned, ready to try again */
  } finally {
    scanning.value = false;
  }
}

const act = () => (found.value ? go('maintenance') : scan());
</script>

<template>
  <div class="hx-sym" data-sym="dupes">
    <span class="hx-sev sev-info"></span>
    <span class="hx-sym-name">{{ t('ov.sym.dupes') }}</span>
    <span class="hx-sym-n">{{ found ? fmtInt(found.pairs) : '—' }}</span>
    <span class="hx-sym-share">{{
      found ? t('ov.sym.atOverlap', { p: found.overlap }) : t('ov.sym.notScanned') }}</span>
    <button type="button" class="btn btn-sm" :disabled="scanning || found?.pairs === 0" @click="act">{{
      scanning ? t('ov.sym.scanning') : found ? t('ov.sym.dupes.act') : t('ov.sym.scan') }}</button>
  </div>
</template>
