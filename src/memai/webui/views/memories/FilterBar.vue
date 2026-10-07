<script setup lang="ts">
/* The list's filters, each under its name, with the rarer ones folded behind More. */
import { ref } from 'vue';
import { debounce } from '../../core/dom.ts';
import { confItems, pinItems, typeItems } from '../../core/shared.js';
import { t } from '../../i18n.ts';
import type { I18nKey } from '../../i18n.ts';
import { TASK } from '../../contract.ts';
import type { DomainEntry } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import DomainPicker from '../../components/DomainPicker.vue';
import Picker from '../../components/Picker.vue';
import TbField from '../../components/TbField.vue';
import { DEFECTS, SORTS, activeSort, moreCount } from './filters.ts';
import type { Filters } from './filters.ts';

const props = defineProps<{ filters: Filters; domains: DomainEntry[]; scope?: string[]; searched: boolean }>();
const emit = defineEmits<{ navigate: [patch: Partial<Filters>]; down: [e: KeyboardEvent] }>();

const f = props.filters;
const types = typeItems({ any: t('mem.f.allM') });
const confs = confItems({ any: t('mem.f.allF') });
const pins = pinItems({ any: t('mem.f.allF') });
const SORT_LABEL: Record<string, I18nKey> = {
  'created_at:desc': 'mem.sort.newest', 'created_at:asc': 'mem.sort.oldest',
  'updated_at:desc': 'mem.sort.updated', 'recalls:desc': 'mem.sort.used', 'recalls:asc': 'mem.sort.unused',
};
const sorts = SORTS.map(value => ({ value, label: t(SORT_LABEL[value]) }));
const taskStates: Array<[string, I18nKey]> = [
  ...TASK.STATES.map((s): [string, I18nKey] => [s, `task.state.${s}` as I18nKey]), ['', 'common.all']];
const statuses: Array<[string, I18nKey]> = [['active', 'common.active'], ['archived', 'common.archived'],
                                            ['', 'common.all']];
const defects = DEFECTS.filter(key => f[key]);

/* Whether the folded row is open: the reader's setting, per browser, unless a filter in it is set. */
const MORE_PREF = 'memai.memories.more';
const moreActive = moreCount(f);
const moreOpen = ref(moreActive > 0 || (() => {
  try { return localStorage.getItem(MORE_PREF) === '1'; } catch { return false; }
})());
function toggleMore() {
  moreOpen.value = !moreOpen.value;
  try { localStorage.setItem(MORE_PREF, moreOpen.value ? '1' : '0'); } catch { /* not kept */ }
}

const query = ref(f.q);
const clearedSearch = debounce(() => {
  if (query.value.trim() === '' && f.q) emit('navigate', { q: '', page: 0 });
}, 500);

function onSearchKey(e: KeyboardEvent) {
  if (e.key === 'Enter') emit('navigate', { q: query.value.trim(), page: 0 });
  /* down out of the search lands in the list, so finding rows and acting on them is one path */
  if (e.key === 'ArrowDown') emit('down', e);
}

/* A completed or cancelled task is archived, so those states lift the status filter; Open puts it
   back. */
const pickTaskState = (v: string) => emit('navigate', { task_state: v, status: v === 'open' ? 'active' : '', page: 0 });
function pickSort(v: string) {
  const [sort, dir] = v.split(':');
  emit('navigate', { sort, dir, page: 0 });
}
</script>

<template>
  <div class="list-toolbar tb-labeled">
    <TbField :label="t('mem.f.search')" cls="tb-grow"><input id="fQ" v-model="query" type="search"
             :placeholder="t('mem.search.placeholder')" :aria-label="t('mem.search.placeholder')"
             :title="t('mem.sub')" spellcheck="false" @keydown="onSearchKey" @input="clearedSearch"></TbField>
    <TbField :label="t('mem.f.type')"><Picker id="fType" :model-value="f.type" :items="types"
             :aria-label="t('common.allTypes')" @pick="type => emit('navigate', { type, task_state: '', page: 0 })" /></TbField>
    <TbField v-if="f.type === 'task'"><div id="fTask" class="seg" role="group" :aria-label="t('mem.task.aria')"><button
             v-for="[v, key] in taskStates" :key="v" type="button" :data-v="v" :aria-pressed="f.task_state === v"
             @click="pickTaskState(v)">{{ t(key) }}</button></div></TbField>
    <TbField :label="t('mem.f.domain')"><DomainPicker id="fDomain" :model-value="f.domain" :domains="domains"
             :aria-label="t('common.allDomains')" :any-label="t('mem.f.allM')"
             @update:model-value="domain => emit('navigate', { domain, page: 0 })" /></TbField>
    <!-- the filter resolved a name that was only the deep end of a path, and says which paths it ran -->
    <TbField v-if="scope"><span class="chip" :title="t('mem.scope.title')">{{
      t('mem.scope.resolved', { list: scope.join(', ') }) }}</span></TbField>
    <TbField :label="t('mem.f.status')"><div id="fStatus" class="seg" role="group" :aria-label="t('mem.status.aria')"><button
             v-for="[v, key] in statuses" :key="v" type="button" :data-v="v" :aria-pressed="f.status === v"
             @click="emit('navigate', { status: v, page: 0 })">{{ t(key) }}</button></div></TbField>
    <TbField :label="t('mem.f.conf')"><Picker id="fConf" :model-value="f.confidence" :items="confs"
             :aria-label="t('mem.conf.all')" @pick="confidence => emit('navigate', { confidence, page: 0 })" /></TbField>
    <TbField><button id="fMore" type="button" class="btn mem-more" :aria-expanded="moreOpen" aria-controls="memMore"
             @click="toggleMore"><AppIcon name="filter" />{{ t('mem.more') }}<span v-if="moreActive"
             class="mem-more-n">{{ moreActive }}</span></button></TbField>
  </div>
  <div id="memMore" class="list-toolbar tb-labeled mem-more-row" :hidden="!moreOpen">
    <TbField :label="t('mem.f.pin')"><Picker id="fPin" :model-value="f.pin" :items="pins" :aria-label="t('mem.pin.aria')"
             @pick="pin => emit('navigate', { pin, page: 0 })" /></TbField>
    <TbField v-if="!searched" :label="t('mem.f.sort')"><Picker id="fSort" :model-value="activeSort(f)" :items="sorts"
             :aria-label="t('mem.sort.aria')" @pick="pickSort" /></TbField>
    <TbField v-if="defects.length"><button v-for="key in defects" :key="key" type="button" class="chip clickable"
             :data-undefect="key" :title="t('mem.defect.off')" @click="emit('navigate', { [key]: '', page: 0 })">{{
             t(`mem.defect.${key}`) }}<AppIcon name="close" /></button></TbField>
    <TbField v-if="f.session"><button id="fSession" type="button" class="chip clickable" :title="t('mem.session.title')"
             @click="emit('navigate', { session: '', page: 0 })">{{
             t('mem.session.chip', { s: f.session.slice(0, 18) }) }}<AppIcon name="close" /></button></TbField>
  </div>
</template>
