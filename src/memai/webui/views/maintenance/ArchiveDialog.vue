<script setup lang="ts">
/* Where the ticked backups go: one zip per month or week, one new named zip, or an existing zip. The
   plan line names the zips to be written, from the server for month and week since it owns the grouping. */
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue';
import { fmtBytes, fmtInt } from '../../core/dom.ts';
import { ADMIN } from '../../contract.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { ArchivePlan, ArchivePlanEntry } from '../../api/types.ts';
import AppModal from '../../components/AppModal.vue';
import { zipLabel } from './maintenance.ts';
import type { ArchiveWay, ArchiveWhere } from './shelf.ts';

const props = defineProps<{ names: string[]; size: number; project: string; zips: string[]; way: ArchiveWay;
                            onWay: (way: ArchiveWay) => void }>();
const emit = defineEmits<{ done: [where: ArchiveWhere | null] }>();

const ZIP_NAME = /^[\p{L}\p{N}_]([\p{L}\p{N}_ .-]*[\p{L}\p{N}_])?$/u;
const ZIP_NAME_MAX = ADMIN.ARCHIVE_LABEL_MAX;
const WAYS = ['month', 'week', 'name', 'existing'] as const;

const taken = new Set(props.zips);
const way = ref<ArchiveWay>(props.way === 'existing' && !props.zips.length ? 'month' : props.way);
const label = ref('');
const into = ref(props.zips[0] ?? '');
const nameBox = ref<HTMLInputElement | null>(null);

type Plan = { rows: ArchivePlanEntry[] } | { problem: string } | { text: string };
const plan = shallowRef<Plan>({ text: '' });
const ready = ref(false);
let seq = 0;
let alive = true;
onBeforeUnmount(() => { alive = false; });

async function refresh() {
  const w = way.value;
  const mine = ++seq;
  ready.value = false;
  if (w === 'name') {
    const name = label.value.trim();
    if (!name) { plan.value = { text: '' }; return; }
    if (name.length > ZIP_NAME_MAX || !ZIP_NAME.test(name)) {
      plan.value = { problem: t('mn.arch.nameBad', { max: ZIP_NAME_MAX }) };
      return;
    }
    const file = `${props.project}-${name}.zip`;
    if (taken.has(file)) { plan.value = { problem: t('mn.arch.nameTaken') }; return; }
    plan.value = { rows: [{ name: file, added: props.names.length, exists: false }] };
    ready.value = true;
  } else if (w === 'existing') {
    plan.value = { rows: [{ name: into.value, added: props.names.length, exists: true }] };
    ready.value = !!into.value;
  } else {
    plan.value = { text: '…' };
    try {
      const r = await client.maintenance.archive({ names: props.names, group: w, dry_run: true }) as ArchivePlan;
      if (mine !== seq || !alive) return;
      plan.value = { rows: r.plan };
      ready.value = true;
    } catch {
      if (mine === seq && alive) plan.value = { problem: t('err.maintenance') };
    }
  }
}
void refresh();
watch([label, into], () => void refresh());

function pick(w: ArchiveWay) {
  way.value = w;
  props.onWay(w);
  void refresh();
  if (w === 'name') void nextTick(() => nameBox.value?.focus());
}

const where = computed<ArchiveWhere>(() => way.value === 'name' ? { group: 'name', label: label.value.trim() }
  : way.value === 'existing' ? { group: 'existing', into: into.value } : { group: way.value });

function ok() {
  if (ready.value) emit('done', where.value);
}
</script>

<template>
  <AppModal :title="t('mn.bk.archiveSel')" @close="emit('done', null)">
    <p class="intro">{{ t('mn.arch.intro', { n: names.length, size: fmtBytes(size) }) }}</p>
    <div class="arch-opts" role="radiogroup" :aria-label="t('mn.arch.where')">
      <label v-for="w in WAYS" :key="w" class="arch-opt">
        <input type="radio" name="archWay" :value="w" :checked="way === w" :disabled="w === 'existing' && !zips.length"
               @change="pick(w)">
        <span class="arch-opt-main"><b>{{ t(`mn.arch.${w}`) }}</b>
          <span class="hint-sm">{{ w === 'existing' && !zips.length ? t('mn.bk.noZips') : t(`mn.arch.${w}Hint`) }}</span></span>
      </label>
      <input ref="nameBox" v-model="label" type="text" id="archName" class="arch-field" autocomplete="off"
             spellcheck="false" :maxlength="ZIP_NAME_MAX" :placeholder="t('mn.arch.namePh')"
             :aria-label="t('mn.arch.name')" :hidden="way !== 'name'" @keydown.enter="ok">
      <select v-model="into" id="archInto" class="arch-field" :aria-label="t('mn.arch.existing')"
              :hidden="way !== 'existing'">
        <option v-for="z in zips" :key="z" :value="z">{{ zipLabel(z, project) }}</option>
      </select>
    </div>
    <div class="arch-plan" :class="{ 'is-bad': 'problem' in plan }" id="archPlan" role="status" aria-live="polite">
      <template v-if="'rows' in plan">
        <div v-for="r in plan.rows" :key="r.name" class="arch-plan-row">
          <span class="arch-plan-name">{{ zipLabel(r.name, project) }}</span>
          <span>{{ t('mn.arch.planN', { n: fmtInt(r.added) }) }} · {{
            t(r.exists ? 'mn.arch.planAdds' : 'mn.arch.planNew') }}</span>
        </div>
      </template>
      <template v-else-if="'problem' in plan">{{ plan.problem }}</template>
      <template v-else>{{ plan.text }}</template>
    </div>
    <template #foot>
      <button class="btn" data-x @click="emit('done', null)">{{ t('common.cancel') }}</button>
      <button class="btn btn-solid" data-ok :disabled="!ready" @click="ok">{{ t('mn.arch.ok') }}</button>
    </template>
  </AppModal>
</template>
