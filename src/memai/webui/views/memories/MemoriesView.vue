<script setup lang="ts">
/* Memories: the filtered, paged list, and the inspector beside it. */
import { onBeforeUnmount, ref } from 'vue';
import { query } from '../../core/api.ts';
import { esc, fmtInt } from '../../core/dom.ts';
import { getDomains } from '../../core/shared.js';
import { go, parseHash } from '../../core/router.ts';
import { setRecordSequence } from '../../core/nav.ts';
import { t } from '../../i18n.ts';
import { ADMIN } from '../../contract.ts';
import * as client from '../../api/client.ts';
import type { DomainEntry } from '../../api/types.ts';
import type { ViewProps } from '../../core/vue.ts';
import AppIcon from '../../components/AppIcon.vue';
import FilterBar from './FilterBar.vue';
import Inspector from './Inspector.vue';
import MemoryTable from './MemoryTable.vue';
import { PAGE, listFilter, readFilters, routeParams } from './filters.ts';
import type { Filters } from './filters.ts';
import { useMemoriesState } from './state.ts';

const props = defineProps<ViewProps>();

const f = readFilters(props.params);
const filter = listFilter(f);
const [domains, data] = await Promise.all([
  getDomains().catch((): DomainEntry[] => []),
  client.memories.list({ ...filter, limit: PAGE, offset: f.page * PAGE }),
]);
const state = useMemoriesState(data.items, data.total, query({ ...filter, limit: ADMIN.BULK_MAX, offset: 0 }));
const first = f.page * PAGE;

const navigate = (patch: Partial<Filters>) => go('memories', routeParams(f, patch));

/* What the record steps through when opened from here; it outlives the list only into the record. */
if (!props.ctx.stale()) setRecordSequence(state.pageUids);
onBeforeUnmount(() => { if (parseHash().name !== 'memory') setRecordSequence([]); });

const table = ref<InstanceType<typeof MemoryTable> | null>(null);
function intoList(e: KeyboardEvent) {
  if (!data.items.length) return;
  e.preventDefault();
  table.value?.focusCaret();
}
</script>

<template>
  <div class="mem-shell">
    <h2 class="sr-only">{{ t('mem.title') }}</h2>
    <div class="mem-work">
      <div class="mem-pane">
        <FilterBar :filters="f" :domains="domains" :scope="data.domain_scope" :searched="data.searched"
                   @navigate="navigate" @down="intoList" />
        <MemoryTable ref="table" :state="state" :items="data.items" :scope="f.domain" />
        <div class="list-foot">
          <!-- the catalog marks the count up; the query in it is escaped -->
          <span v-if="data.searched" v-html="t('mem.results', { n: fmtInt(data.total), q: esc(f.q) })"></span>
          <span v-else>{{ t('mem.range', { a: fmtInt(first + Math.min(1, data.items.length)),
                                            b: fmtInt(first + data.items.length), c: fmtInt(data.total) }) }}</span>
          <span class="pager">
            <button id="pgPrev" class="btn btn-sm" :disabled="f.page === 0"
                    @click="navigate({ page: f.page - 1 })"><AppIcon name="chevron-left" />{{ t('mem.prev') }}</button>
            <button id="pgNext" class="btn btn-sm" :disabled="(f.page + 1) * PAGE >= data.total"
                    @click="navigate({ page: f.page + 1 })">{{ t('mem.next') }}<AppIcon name="chevron-right" /></button>
          </span>
        </div>
      </div>
      <Inspector :state="state" :domains="domains" />
    </div>
  </div>
</template>
