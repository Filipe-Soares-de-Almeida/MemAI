<script setup lang="ts">
/* The New memory dialog: a plain type is one body, a sectioned type its named fields, a task a goal
   and items, and a diagram a title over a seeded start→end skeleton. */
import { computed, reactive, ref } from 'vue';
import AppModal from '../../components/AppModal.vue';
import Picker from '../../components/Picker.vue';
import { byDomainPath, confItems, invalidateDomains, sectionLabel, typeItems } from '../../core/shared.js';
import { failed, toast } from '../../core/toasts.ts';
import { go, refreshBehind } from '../../core/router.ts';
import { openRecord } from '../../core/nav.ts';
import { newDiagramSkeleton } from '../../core/diagram-skeleton.ts';
import { t } from '../../i18n.ts';
import { TASK } from '../../contract.ts';
import * as client from '../../api/client.ts';
import type { DomainEntry, SectionSpec } from '../../api/types.ts';
import { itemLines, itemsOver } from './task-items.ts';

const props = defineProps<{ domains: DomainEntry[]; spec: Record<string, SectionSpec[]> }>();
const emit = defineEmits<{ close: [] }>();

/* the server refuses a handoff written here */
const NOT_WRITTEN_HERE = ['handoff'];
const types = typeItems().filter(it => !NOT_WRITTEN_HERE.includes(it.value));
const confs = confItems();
const domainPaths = props.domains.slice().sort(byDomainPath).map(d => d.domain);

const type = ref('note');
const confidence = ref('unverified');
const form = reactive({ title: '', domain: '', also: '', tags: '', content: '', goal: '', items: '' });
/* keyed by type and field, so a field keeps its text when the type changes and changes back */
const sections = reactive<Record<string, string>>({});

const isDiagram = computed(() => type.value === 'diagram');
const isTask = computed(() => type.value === 'task');
const fields = computed(() => props.spec[type.value] || []);
const sectionKey = (key: string) => `${type.value}.${key}`;
const sectionText = (key: string) => sections[sectionKey(key)] ?? '';
const lines = computed(() => itemLines(form.items));

/* Counts are shown, never enforced by `maxlength`, which truncates a paste without a word. */
const count = (n: number, max: number) => t('dr.sections.count', { n, max });

async function submit(): Promise<{ uid: string; toast: 'nm.created' | 'nm.task.created' }> {
  const where = { domain: form.domain, also: form.also, tags: form.tags };
  if (isDiagram.value) {
    const r = await newDiagramSkeleton({ title: form.title, ...where });
    return { uid: r.uid, toast: 'nm.created' };
  }
  if (isTask.value) {
    const r = await client.tasks.create({ title: form.title, goal: form.goal, items: form.items, ...where });
    return { uid: r.uid, toast: 'nm.task.created' };
  }
  const r = await client.memories.create({
    type: type.value, confidence: confidence.value, title: form.title, ...where,
    ...(fields.value.length
      ? { sections: Object.fromEntries(fields.value.map(f => [f.key, sectionText(f.key)])) }
      : { content: form.content }),
  });
  return { uid: r.uid, toast: 'nm.created' };
}

async function create() {
  const diagram = isDiagram.value;
  try {
    const made = await submit();
    emit('close');
    toast(t(made.toast, { uid: made.uid }), 'ok');
    invalidateDomains();
    /* a diagram opens on its canvas, anything else on its record */
    if (diagram) {
      go('diagram', { uid: made.uid });
    } else {
      refreshBehind();
      openRecord(made.uid);
    }
  } catch (err) {
    failed('err.create', err);
  }
}
</script>

<template>
  <AppModal :title="t('nm.title')" @close="emit('close')">
    <div class="field-pair">
      <div class="field"><label for="nmType">{{ t('nm.type') }}</label>
        <Picker id="nmType" v-model="type" :items="types" :aria-label="t('nm.type')" /></div>
      <div class="field"><label for="nmConf">{{ t('nm.conf') }}</label>
        <!-- a diagram's content is its graph and a task's its checklist: both start unverified -->
        <Picker id="nmConf" v-model="confidence" :items="confs" :aria-label="t('nm.conf')"
                :disabled="isDiagram || isTask" /></div>
    </div>
    <div class="field"><label for="nmTitle">{{ t('nm.name') }}</label>
      <input id="nmTitle" v-model="form.title" type="text" :placeholder="t('nm.titlePh')">
      <div id="nmDiagramHint" class="dg-empty" style="margin-top:7px" :hidden="!isDiagram">{{
        t('nm.diagramHint') }}</div></div>
    <div class="field"><label for="nmDomain">{{ t('nm.domain') }}</label>
      <input id="nmDomain" v-model="form.domain" type="text" list="nmDomainsDL" :placeholder="t('nm.domainPh')">
      <datalist id="nmDomainsDL"><option v-for="path in domainPaths" :key="path" :value="path"></option></datalist></div>
    <div class="field"><label for="nmAlso">{{ t('nm.also') }}</label>
      <input id="nmAlso" v-model="form.also" type="text" list="nmDomainsDL" :placeholder="t('mm.also.placeholder')">
      <div class="hint-sm">{{ t('mm.also.hint') }}</div></div>
    <div class="field"><label for="nmTags">{{ t('nm.tags') }}</label>
      <input id="nmTags" v-model="form.tags" type="text" :placeholder="t('nm.tagsPh')"></div>
    <div id="nmContentField" class="field" :hidden="isDiagram || isTask || fields.length > 0">
      <label for="nmContent">{{ t('nm.content') }}</label>
      <textarea id="nmContent" v-model="form.content" rows="7" :placeholder="t('nm.contentPh')"></textarea></div>
    <div id="nmSectionFields" :hidden="!fields.length">
      <div v-for="f in fields" :key="sectionKey(f.key)" class="field">
        <label :for="`nmSec-${f.key}`"><span class="sec-label-text" :title="f.label">{{
          sectionLabel(type, f) }}</span> <span v-if="f.max_len" class="sec-count"
            :class="{ over: sectionText(f.key).length > f.max_len }">{{
            count(sectionText(f.key).length, f.max_len) }}</span></label>
        <textarea :id="`nmSec-${f.key}`" v-model="sections[sectionKey(f.key)]" rows="4"></textarea></div>
    </div>
    <div id="nmTaskFields" :hidden="!isTask">
      <div class="field"><label for="nmGoal">{{ t('nm.task.goal') }}
          <span id="nmGoalCount" class="sec-count" :class="{ over: form.goal.length > TASK.GOAL_MAX }">{{
            count(form.goal.length, TASK.GOAL_MAX) }}</span></label>
        <textarea id="nmGoal" v-model="form.goal" class="tk-box" rows="3"
                  :placeholder="t('nm.task.goalPh')"></textarea></div>
      <div class="field"><label for="nmItems">{{ t('nm.task.items') }}
          <span id="nmItemsCount" class="sec-count" :class="{ over: itemsOver(lines) }">{{
            t('nm.task.itemsCount', { n: lines.length }) }}</span></label>
        <textarea id="nmItems" v-model="form.items" class="tk-box" rows="6"
                  :placeholder="t('nm.task.itemsPh')"></textarea>
        <div class="hint-sm">{{ t('nm.task.itemsHint', { n: TASK.ITEMS_MAX, max: TASK.ITEM_MAX }) }}</div></div>
    </div>
    <template #foot>
      <button class="btn" data-x @click="emit('close')">{{ t('common.cancel') }}</button>
      <button class="btn btn-solid" data-ok @click="create">{{ t('nm.create') }}</button>
    </template>
  </AppModal>
</template>
