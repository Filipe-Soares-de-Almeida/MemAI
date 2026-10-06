<script setup lang="ts">
/* The countable defects that pull the index down, each with its share and the button that opens
   exactly that set. */
import { computed } from 'vue';
import { fmtInt } from '../../core/dom.ts';
import { go } from '../../core/router.ts';
import { t } from '../../i18n.ts';
import type { I18nKey } from '../../i18n.ts';
import type { Symptom } from '../../api/types.ts';
import { rankSymptoms, symptomRoute } from './health.ts';
import DupeScan from './DupeScan.vue';
import FileCheck from './FileCheck.vue';

const props = defineProps<{ symptoms: Symptom[]; active: number }>();

const rows = computed(() => rankSymptoms(props.symptoms));

/* the server's symptom keys name their catalog entries */
const label = (s: Symptom, suffix = '') => t(`ov.sym.${s.key}${suffix}` as I18nKey);

/* A symptom over flows or relation rows carries its own denominator and no share of the store. */
const OF_LABEL: Partial<Record<string, I18nKey>> = { diagrams: 'ov.sym.ofFlows', orphans: 'ov.sym.ofRelations' };

function share(s: Symptom): string {
  const of = OF_LABEL[s.key];
  if (of) return t(of, { n: fmtInt(s.count), all: fmtInt(s.of) });
  return props.active ? `${(s.share * 100).toFixed(1)}%` : '';
}
</script>

<template>
  <div class="panel hx-symptoms">
    <h3 class="panel-title">{{ t('ov.sym.title') }}
      <span class="panel-aside">{{ t('ov.sym.aside') }}</span></h3>
    <div class="hx-syms">
      <div v-for="s in rows" :key="s.key" class="hx-sym" :data-sym="s.key">
        <span class="hx-sev" :class="`sev-${s.severity}`"></span>
        <span class="hx-sym-name">{{ label(s) }}</span>
        <span class="hx-sym-n">{{ fmtInt(s.count) }}</span>
        <span class="hx-sym-share">{{ share(s) }}</span>
        <button type="button" class="btn btn-sm hx-sym-go" :disabled="!s.count"
                @click="go(...symptomRoute(s))">{{ label(s, '.act') }}</button>
      </div>
      <DupeScan />
      <FileCheck />
    </div>
  </div>
</template>
