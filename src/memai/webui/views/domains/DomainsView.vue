<script setup lang="ts">
/* Domains: the tree walked left to right in columns, each listing the levels inside the one picked
   before it, with the picked level read in the pane beside them. */
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue';
import { fmtInt } from '../../core/dom.ts';
import { getDomains } from '../../core/shared.js';
import { failed, openDialog, openDropMenu, toast } from '../../core/ui.js';
import { go } from '../../core/router.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { DomainEntry, NormalizePlan } from '../../api/types.ts';
import type { ViewProps } from '../../core/vue.ts';
import AppIcon from '../../components/AppIcon.vue';
import DomainPicker from '../../components/DomainPicker.vue';
import CaseDialog from './CaseDialog.vue';
import DomainColumn from './DomainColumn.vue';
import type { Drag } from './DomainColumn.vue';
import DomainPane from './DomainPane.vue';
import MoveQueue from './MoveQueue.vue';
import NormalizeDialog from './NormalizeDialog.vue';
import { pruneQueue, showArchived } from './store.ts';
import { columnsFor, crumbs, isArchived } from './tree.ts';

const props = defineProps<ViewProps>();

const [domains, cfg] = await Promise.all([
  getDomains(true) as Promise<DomainEntry[]>,
  client.config.get().catch(() => ({ domain_case: 'preserve' })),
]);
const byPath = new Map(domains.map(d => [d.domain, d]));
/* a path renamed or deleted since the link was made lands at the roots, not on an empty column */
const asked = props.params.get('path') || '';
const path = byPath.has(asked) ? asked : '';
const here = byPath.get(path) || null;
if (!props.ctx.stale()) pruneQueue(p => byPath.has(p));

const named = domains.filter(d => !d.implicit).length;
const roots = domains.filter(d => !d.parent).length;
const archived = domains.filter(isArchived).length;
const columns = computed(() => columnsFor(domains, path, showArchived.value));
const drag = reactive<Drag>({ from: '', on: new Set<string>() });

/* the deepest column is the one being worked in, so the strip opens scrolled to it */
const strip = ref<HTMLElement | null>(null);
const toDeepest = () => { if (strip.value) strip.value.scrollLeft = strip.value.scrollWidth; };
onMounted(toDeepest);
watch(showArchived, () => nextTick(toDeepest));

const findDomain = (to: string) => go('domains', to ? { path: to } : {});
const toggleArchived = () => { showArchived.value = !showArchived.value; };

async function normalize() {
  let plan: NormalizePlan;
  try {
    plan = await client.domains.normalize({ dry_run: true }) as NormalizePlan;
  } catch (err) { failed('err.load', err); return; }
  /* under 'preserve' nothing is ever planned, and the toast says why */
  if (!plan.plan.length) {
    toast(plan.mode === 'preserve' ? t('do.case.preserveHint') : t('do.case.none'),
          plan.mode === 'preserve' ? '' : 'ok');
    return;
  }
  openDialog(NormalizeDialog, { plan });
}

/* store-wide, since the casing policy belongs to the file and not to one level */
function storeMenu(e: MouseEvent) {
  openDropMenu(e.currentTarget, [
    { label: t('do.case.title'), run: () => openDialog(CaseDialog, { mode: cfg.domain_case }) },
    { label: t('do.case.normalize'), run: normalize },
  ], { align: 'right' });
}
</script>

<template>
  <div class="dom-shell">
    <div class="dom-bar">
      <h2 class="sr-only">{{ t('do.title') }}</h2>
      <span class="dom-crumb"><span v-if="!path" class="dom-crumb-none">{{ t('do.crumb.roots') }}</span><template
            v-for="(c, i) in crumbs(path)" v-else :key="c.upto"><span v-if="i" class="dom-crumb-sep">/</span><button
            type="button" class="dom-crumb-seg" :class="{ on: c.upto === path }" :data-goto="c.upto"
            @click="go('domains', { path: c.upto })">{{ c.seg }}</button></template></span>
      <span class="dom-bar-sub">{{ t('do.sub.count', { n: fmtInt(named) }) }} · {{
        t('do.sub.roots', { n: fmtInt(roots) }) }}</span>
      <span class="dom-bar-end">
        <!-- a tree of hundreds is found by typing, not walked; picking a row goes to that level -->
        <DomainPicker id="domFind" model-value="" cls="dom-find" :domains="domains" :any-label="t('do.find')"
                      :aria-label="t('do.find')" @update:model-value="findDomain" />
        <button v-if="archived" id="domArchived" class="btn btn-sm" :class="{ 'btn-solid': showArchived }"
                :aria-pressed="showArchived ? 'true' : 'false'" @click="toggleArchived">{{
          t(showArchived ? 'do.tree.archivedHide' : 'do.tree.archivedShow', { n: fmtInt(archived) }) }}</button>
        <button id="domMore" type="button" class="icon-btn" :title="t('do.storeMenu')" :aria-label="t('do.storeMenu')"
                @click="storeMenu"><AppIcon name="maintenance" /></button>
      </span>
    </div>

    <div id="domCols" class="dom-cols" :class="{ dragging: drag.from }">
      <!-- the columns scroll sideways rather than squeeze the pane, where the level is read -->
      <div id="domStrip" ref="strip" class="dom-strip"><DomainColumn v-for="col in columns" :key="col.parent"
           :column="col" :by-path="byPath" :drag="drag" /></div>
      <div id="domDetail" class="dom-detail">
        <DomainPane v-if="here" :node="here" :domains="domains" />
        <div v-else class="dom-detail-empty">
          <div class="mi-empty-title">{{ t('do.det.pickTitle') }}</div>
          <p class="hint">{{ t('do.det.pickBody') }}</p>
        </div>
      </div>
    </div>

    <MoveQueue />
  </div>
</template>
