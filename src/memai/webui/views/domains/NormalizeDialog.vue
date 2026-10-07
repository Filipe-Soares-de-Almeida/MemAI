<script setup lang="ts">
/* What bringing the stored domains in line with the casing policy would rename and merge, applied
   only on confirmation. */
import { fmtInt } from '../../core/dom.ts';
import { failed, toast } from '../../core/toasts.ts';
import { invalidateDomains } from '../../core/shared.js';
import { refreshBehind } from '../../core/router.ts';
import { t } from '../../i18n.ts';
import type { I18nKey } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { NormalizeDone, NormalizePlan } from '../../api/types.ts';
import AppModal from '../../components/AppModal.vue';

const props = defineProps<{ plan: NormalizePlan }>();
const emit = defineEmits<{ done: [applied: boolean] }>();

const modeLabel = t(`do.case.mode.${props.plan.mode}` as I18nKey);

async function apply() {
  try {
    const r = await client.domains.normalize({ dry_run: false }) as NormalizeDone;
    emit('done', true);
    toast(t('do.norm.done', { n: r.moved, affected: r.affected }), 'ok');
    invalidateDomains();
    refreshBehind();
  } catch (err) { failed('err.domain', err); }
}
</script>

<template>
  <AppModal :title="t('do.norm.title')" @close="emit('done', false)">
    <div class="intro">{{ t('do.norm.intro', { renames: plan.renames, merges: plan.merges, mode: modeLabel }) }}</div>
    <div v-if="plan.merges" class="intro warn">{{ t('do.norm.mergeWarn') }}</div>
    <div class="table-scroll"><table class="table"><thead><tr>
      <th>{{ t('do.norm.th.from') }}</th><th>{{ t('do.norm.th.to') }}</th>
      <th class="num">{{ t('do.norm.th.count') }}</th><th>{{ t('do.norm.th.action') }}</th>
    </tr></thead><tbody>
      <tr v-for="e in plan.plan" :key="e.from">
        <td>{{ e.from }}</td>
        <td style="color:var(--ink)">{{ e.to }}</td>
        <td class="num">{{ fmtInt(e.count) }}</td>
        <td><span :style="{ color: e.action === 'merge' ? 'var(--warn)' : 'var(--ink-3)' }">{{
          t(`do.norm.act.${e.action}`) }}</span></td>
      </tr>
    </tbody></table></div>
    <template #foot>
      <button class="btn" data-x @click="emit('done', false)">{{ t('common.cancel') }}</button>
      <button class="btn btn-solid" data-ok @click="apply">{{ t('do.norm.apply') }}</button>
    </template>
  </AppModal>
</template>
