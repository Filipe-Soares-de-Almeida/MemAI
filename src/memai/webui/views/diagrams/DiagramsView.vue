<script setup lang="ts">
/* The diagram list: one row per flow, filtered in the browser, beside an inspector that follows the
   caret. The list is one tab stop whose arrows move the caret, and the caret is the selection. */
import { computed, nextTick, ref, watch } from 'vue';
import { fmtInt } from '../../core/dom.ts';
import { newDiagramSkeleton } from '../../core/diagram-skeleton.ts';
import { go } from '../../core/router.ts';
import { getDomains, invalidateDomains } from '../../core/shared.js';
import { failed, promptModal } from '../../core/ui.js';
import type { ViewProps } from '../../core/vue.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { DomainEntry } from '../../api/types.ts';
import DomainPicker from '../../components/DomainPicker.vue';
import DiagramInspector from './DiagramInspector.vue';
import DiagramRow from './DiagramRow.vue';
import { filterDiagrams } from './diagrams.ts';

const props = defineProps<ViewProps>();

const status = props.params.has('status') ? props.params.get('status') ?? '' : 'active';
const domain = props.params.get('domain') || '';
/* status goes out even when empty: "" asks for every status, which omitting it does not */
const qs = new URLSearchParams({ status });
if (domain) qs.set('domain', domain);
const [domains, data] = await Promise.all([
  getDomains().catch(() => []) as Promise<DomainEntry[]>,
  client.diagrams.list(qs),
]);

const query = ref('');
const shown = computed(() => filterDiagrams(data.items, query.value));
const broken = computed(() => shown.value.filter(d => d.issues.length).length);
/* the first row is picked on arrival, since an empty pane beside a full list says nothing */
const cursor = ref(0);
watch(query, () => { cursor.value = 0; });
const current = computed(() => shown.value[cursor.value] ?? null);
const rowsEl = ref<HTMLElement | null>(null);

function nav(patch: { domain?: string; status?: string }) {
  const p = { status, domain, ...patch };
  const out: Record<string, string> = {};
  if (p.domain) out.domain = p.domain;
  if (p.status === '') out.status = '';
  go('diagrams', out);
}

/* a flow starts as a start-to-end skeleton and is grown on the canvas */
async function newDiagram() {
  const title = await promptModal({ title: t('dgl.newTitle'), body: t('dgl.newBody'), label: t('dg.meta.name'),
                                    placeholder: t('nm.titlePh'), okLabel: t('dgl.new') });
  if (title === null || !title.trim()) return;
  try {
    const r = await newDiagramSkeleton({ title, domain });
    invalidateDomains();
    go('diagram', { uid: r.uid });
  } catch (err) { failed('err.create', err); }
}

async function moveTo(i: number) {
  if (i < 0 || i >= shown.value.length) return;
  cursor.value = i;
  await nextTick();
  rowsEl.value?.querySelectorAll<HTMLElement>('.dgl-row')[i]?.focus();
}

/* Enter opens the editor, not the record: a flow's shape is what this view is about */
function onRowsKey(e: KeyboardEvent) {
  const row = (e.target as HTMLElement).closest<HTMLElement>('.dgl-row');
  if (!row) return;
  const i = shown.value.findIndex(d => d.uid === row.dataset.uid);
  const step = ({ ArrowDown: 1, ArrowUp: -1 } as Record<string, number>)[e.key];
  if (step !== undefined) {
    e.preventDefault();
    void moveTo(Math.min(shown.value.length - 1, Math.max(0, i + step)));
  } else if (e.key === 'Home' || e.key === 'End') {
    e.preventDefault();
    void moveTo(e.key === 'Home' ? 0 : shown.value.length - 1);
  } else if (e.key === 'Enter') {
    e.preventDefault();
    go('diagram', { uid: shown.value[i].uid });
  }
}

/* Down out of the filter lands on the caret's row, so finding a flow and opening it is one path */
function onFilterKey(e: KeyboardEvent) {
  if (e.key !== 'ArrowDown' || !shown.value.length) return;
  e.preventDefault();
  rowsEl.value?.querySelectorAll<HTMLElement>('.dgl-row')[cursor.value]?.focus();
}
</script>

<template>
  <div class="dgl-shell">
    <h2 class="sr-only">{{ t('dgl.title') }}</h2>

    <div class="dgl-work">
      <div class="dgl-pane">
        <div class="list-toolbar">
          <DomainPicker id="dglDomain" :model-value="domain" :domains="domains" :aria-label="t('common.allDomains')"
                        @update:model-value="d => nav({ domain: d })" />
          <div class="seg" id="dglStatus" role="group" :aria-label="t('mem.status.aria')">
            <button type="button" data-v="active" :aria-pressed="status === 'active'"
                    @click="nav({ status: 'active' })">{{ t('common.active') }}</button>
            <button type="button" data-v="" :aria-pressed="status === ''" @click="nav({ status: '' })">{{
              t('common.all') }}</button>
          </div>
          <input v-model="query" type="search" id="dglFilter" :placeholder="t('dgl.filter')" :aria-label="t('dgl.filter')"
                 autocomplete="off" spellcheck="false" @keydown="onFilterKey">
          <button class="btn btn-solid" id="dglNew" @click="newDiagram">{{ t('dgl.new') }}</button>
        </div>

        <div class="dgl-list" id="dglList">
          <div v-if="!shown.length" class="empty"><template v-if="data.total">{{ t('dgl.noMatch') }}</template><template
            v-else>{{ t('dgl.empty') }}<div class="dg-empty" style="margin-top:8px">{{ t('dgl.emptyHint') }}</div></template></div>
          <template v-else>
            <!-- a sibling of the rows, since a column head is not a listbox option; it counts what is shown -->
            <div class="dgl-head">
              <span></span>
              <span>{{ t('dgl.count', { n: fmtInt(shown.length) }) + (broken ? ' · ' : '') }}<span v-if="broken"
                class="dgl-broken">{{ t('dgl.subIssues', { n: fmtInt(broken) }) }}</span></span>
              <span>{{ t('dgl.colUpdated') }}</span>
            </div>
            <div ref="rowsEl" id="dglRows" role="listbox" :aria-label="t('dgl.title')" @keydown="onRowsKey">
              <DiagramRow v-for="(d, i) in shown" :key="d.uid" :d="d" :picked="i === cursor" @click="cursor = i"
                          @dblclick="go('diagram', { uid: d.uid })" />
            </div>
          </template>
        </div>
      </div>

      <DiagramInspector :d="current" />
    </div>
  </div>
</template>
