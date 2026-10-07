<script setup lang="ts">
/* One run: what the batch was checked against and what it would do to the store, beside one row
   per kind with the decisions that act on a whole kind or the whole run. */
import { computed, ref, shallowRef } from 'vue';
import { fmtDate } from '../../core/dom.ts';
import { confirmModal, failed, toast } from '../../core/ui.js';
import { kindLabel } from '../../core/shared.js';
import { go } from '../../core/router.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { OptimizationSummary } from '../../api/types.ts';
import BackButton from './BackButton.vue';
import KindRow from './KindRow.vue';
import LedgerPanel from './LedgerPanel.vue';
import VerifiedPanel from './VerifiedPanel.vue';
import { reportApplied, upTo } from './suggestion.ts';

const props = defineProps<{ summary: OptimizationSummary }>();

const sum = shallowRef(props.summary);
const runId = sum.value.run.id;
const busy = ref('');
const decided = computed(() => ({
  a: sum.value.groups.reduce((n, g) => n + g.applied, 0),
  r: sum.value.groups.reduce((n, g) => n + g.rejected, 0),
}));

const reload = async () => { sum.value = await client.optimization.summary({ run: runId }); };

/* one write per kind at a time; a failure leaves the row as it was */
async function decide(kind: string, what: 'apply' | 'reject' | 'undo', n: number) {
  if (!(await confirmModal({ title: t(`op.group.${what}Confirm.title`),
    body: t(`op.group.${what}Confirm.body`, { n, kind: kindLabel(kind), id: runId }),
    okLabel: t(`op.group.${what}Confirm.ok`) }))) return;
  busy.value = kind;
  try {
    if (what === 'apply') reportApplied(await client.optimization.applyAll({ run: runId, kind }));
    else if (what === 'reject') {
      toast(t('op.toast.rejectedN', { n: (await client.optimization.rejectAll({ run: runId, kind })).rejected }), 'ok');
    } else {
      /* undoing a group puts it back to pending, one revert per applied suggestion */
      const r = await client.optimization.suggestions({ run: runId, kind, status: 'applied' });
      for (const s of r.suggestions) await client.optimization.revert({ id: s.id });
      toast(t('op.toast.revertedN', { n: r.suggestions.length }), 'ok');
    }
    await reload();
  } catch (err) { failed('err.optimize', err); }
  finally { busy.value = ''; }
}

async function applyAll() {
  if (!sum.value.pending) { toast(t('op.toast.nothingPending'), ''); return; }
  if (!(await confirmModal({ title: t('op.applyAllConfirm.title'),
    body: t('op.applyAllConfirm.body', { n: sum.value.pending, id: runId }),
    okLabel: t('op.applyAllConfirm.ok') }))) return;
  try {
    reportApplied(await client.optimization.applyAll({ run: runId }));
    await reload();
  } catch (err) { failed('err.optimize', err); }
}

async function discard() {
  if (!(await confirmModal({ title: t('op.discardConfirm.title'), body: t('op.discardConfirm.body', { id: runId }),
    okLabel: t('op.discardConfirm.ok'), danger: true }))) return;
  try {
    await client.optimization.deleteRun(runId);
    toast(t('op.toast.discarded'), 'ok');
    go('optimization');
  } catch (err) { failed('err.optimize', err); }
}

const openKind = (kind: string) => go('optimization', { run: String(runId), kind });
</script>

<template>
  <div class="opt-shell">
    <div class="opt-bar">
      <BackButton :label="t('op.backToRuns')" @click="upTo()" />
      <h2 class="opt-bar-title">{{ t('op.runTitle', { id: runId }) }} <em class="opt-run-when">· {{
        fmtDate(sum.run.created_at) }}</em></h2>
      <span class="opt-bar-end">
        <span class="panel-aside">{{ t('op.summary', { p: sum.pending, a: decided.a, r: decided.r }) }}</span>
        <button id="optDiscard" type="button" class="btn btn-danger btn-sm" @click="discard">{{ t('op.discard') }}</button>
      </span>
    </div>
    <div v-if="sum.run.note" class="opt-run-note-line">{{ sum.run.note }}</div>
    <div class="opt-work">
      <div class="opt-rail">
        <VerifiedPanel :verified="sum.verified" :pending="sum.pending" />
        <LedgerPanel :summary="sum" />
      </div>
      <div class="opt-groups">
        <div class="opt-groups-head">
          <h3 class="panel-title">{{ t('op.grp.title') }}</h3>
          <span class="panel-aside">{{ t('op.grp.aside', { n: sum.pending, g: sum.groups.filter(g => g.pending).length }) }}</span>
          <span class="opt-foot-gap"></span>
          <button id="optApplyAll" type="button" class="btn btn-sm" :disabled="!sum.pending" @click="applyAll">{{
            t('op.applyAll') }}</button>
        </div>
        <div class="opt-grp-scroll">
          <template v-if="sum.groups.length">
            <div class="opt-grp opt-grp-th">
              <span></span><span>{{ t('op.grp.col.kind') }}</span><span>{{ t('op.grp.col.what') }}</span>
              <span class="opt-grp-n">{{ t('op.grp.col.pending') }}</span>
              <span>{{ t('op.grp.col.checked') }}</span><span></span>
            </div>
            <KindRow v-for="g in sum.groups" :key="g.kind" :group="g" :busy="busy === g.kind" @open="openKind(g.kind)"
                     @apply="decide(g.kind, 'apply', g.pending)" @reject="decide(g.kind, 'reject', g.pending)"
                     @undo="decide(g.kind, 'undo', g.applied)" />
          </template>
          <div v-else class="empty">{{ t('op.emptyRun') }}</div>
        </div>
        <div class="opt-groups-foot">
          <span class="dot" style="--c:var(--warn)"></span>
          <span>{{ t('op.grp.legend') }}</span>
          <span class="opt-foot-gap"></span>
          <span class="opt-grp-note">{{ t('op.grp.footNote') }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
