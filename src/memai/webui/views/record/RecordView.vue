<script setup lang="ts">
/* A memory's record, at #/memory?uid=…: its fields edited one at a time (or all at once), a diagram's
   canvas or a task's checklist in their place, and the side that curates it. */
import { computed, nextTick, onBeforeUnmount, onMounted, provide, reactive, ref, shallowRef } from 'vue';
import { copyUid, failed, modalOpen, openDialog, openDropMenu, toast } from '../../core/ui.js';
import { go, refreshBehind } from '../../core/router.ts';
import { openRecord } from '../../core/nav.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { MemoryRecord, TaskAnswer } from '../../api/types.ts';
import type { ViewProps } from '../../core/vue.ts';
import BlockIndex from './BlockIndex.vue';
import DiagramPreview from './DiagramPreview.vue';
import FieldEditor from './FieldEditor.vue';
import MetaDialog from './MetaDialog.vue';
import MetaPanel from './MetaPanel.vue';
import RecordBar from './RecordBar.vue';
import RecordTitle from './RecordTitle.vue';
import SaveBar from './SaveBar.vue';
import TaskChecklist from './TaskChecklist.vue';
import { REFRESH, fieldsOf, saveBody } from './record.ts';
import { nameStop, step, walk } from './walk.ts';

const props = defineProps<ViewProps>();

const uid = props.params.get('uid') || '';
if (uid) walk(uid);
else go('memories');
const m = shallowRef<MemoryRecord | null>(uid ? await client.memories.get(uid) : null);
const label = (r: MemoryRecord) => r.title || r.content.split('\n', 1)[0];
if (m.value && !props.ctx.stale()) nameStop(uid, label(m.value));

let alive = true;
onBeforeUnmount(() => { alive = false; });

/* every write reads the record again in place; once the view is gone, the route reruns instead */
async function refresh() {
  if (!alive) { refreshBehind(); return; }
  try {
    const now = await client.memories.get(uid);
    if (!alive) return;
    m.value = now;
    nameStop(uid, label(now));
  } catch (err) { failed('err.load', err); }
}
provide(REFRESH, refresh);

const fields = computed(() => (m.value ? fieldsOf(m.value) : []));
const isDiagram = computed(() => m.value?.type === 'diagram');
const isTask = computed(() => m.value?.type === 'task' && Boolean(m.value.task));

/* which field is open, by key, or `all` for every one at once */
const editing = reactive({ key: null as string | null, all: false });
const drafts = ref<Record<string, string>>({});
const note = ref('');
const picked = ref('');
const sel = computed(() => Math.max(0, fields.value.findIndex(f => f.key === picked.value)));

const main = ref<HTMLElement | null>(null);
function openEditor(key: string | null, all: boolean) {
  editing.key = key;
  editing.all = all;
  note.value = '';
  drafts.value = Object.fromEntries(fields.value.filter(f => all || f.key === key).map(f => [f.key, f.text]));
  if (key === null && !all) return;
  nextTick(() => {
    const box = main.value?.querySelector<HTMLTextAreaElement>('[data-src]');
    box?.focus();
    box?.setSelectionRange(box.value.length, box.value.length);
  });
}
const closeEditor = () => openEditor(null, false);
const pick = (key: string) => { picked.value = key; closeEditor(); };

async function save() {
  if (!m.value) return;
  const body = saveBody(fields.value, drafts.value, note.value);
  try {
    await ('sections' in body ? client.memories.sections(uid, body) : client.memories.content(uid, body));
    toast(t('dr.contentUpdated'), 'ok');
    closeEditor();
    await refresh();
  } catch (err) { failed('err.save', err); }
}

/* a heading in the index scrolls the body to the nth `.rt-h`, the order headings() listed them in */
function toMark(j: number) {
  const body = main.value?.querySelector<HTMLElement>('.rf-body');
  const mark = body?.querySelectorAll('.rt-h')[j];
  if (!body || !mark) return;
  body.scrollTop += mark.getBoundingClientRect().top - body.getBoundingClientRect().top - 8;
}

const editMeta = async () => {
  if (m.value && await openDialog(MetaDialog, { m: m.value })) await refresh();
};

function more(e: MouseEvent) {
  openDropMenu(e.currentTarget, [
    { label: t('mm.title'), run: editMeta },
    { label: t('a11y.copyUid', { uid }), run: () => copyUid(uid) },
  ], { align: 'right' });
}

/* A task's write changed the record's Updated time, its size and its history. */
async function retrail(answer?: TaskAnswer) {
  if (m.value && answer?.task) m.value = { ...m.value, task: answer.task };
  try {
    const now = await client.memories.get(uid);
    if (!alive || !m.value) return;
    m.value = { ...m.value, updated_at: now.updated_at, content: now.content, edit_history: now.edit_history };
  } catch { /* the side keeps what it showed */ }
}
const setStatus = (status: string) => { if (m.value) m.value = { ...m.value, status }; };

/* Left and Right step through the list, except where an arrow means something else: in a form
   control, with a modifier, under a modal, or while a field holds unsaved text. */
function stepKeys(e: KeyboardEvent) {
  const delta = ({ ArrowLeft: -1, ArrowRight: 1 } as Record<string, number>)[e.key];
  if (delta === undefined) return;
  if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
  if (modalOpen() || editing.all || editing.key !== null) return;
  const el = document.activeElement as HTMLElement | null;
  if (el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable)) return;
  e.preventDefault();
  step(uid, delta);
}
onMounted(() => document.addEventListener('keydown', stepKeys));
onBeforeUnmount(() => document.removeEventListener('keydown', stepKeys));
</script>

<template>
  <div v-if="m" class="rec-shell">
    <RecordBar :m="m" :editing-all="editing.all" @toggle-all="openEditor(null, !editing.all)" @more="more" />
    <div class="rec-work">
      <div ref="main" class="rec-main">
        <RecordTitle :uid="uid" :title="m.title || ''" />
        <div v-if="m.section_problem" class="sec-problem">{{ t('dr.sections.problem', { detail: m.section_problem }) }}</div>
        <DiagramPreview v-if="isDiagram" :uid="uid" :title="m.title" :content="m.content" />
        <div v-else-if="isTask && m.task" id="taskHost"><TaskChecklist :uid="uid" :task="m.task" :status="m.status"
             @status="setStatus" @write="retrail" /></div>
        <template v-else-if="editing.all">
          <div class="rec-stack"><FieldEditor v-for="f in fields" :key="f.key" v-model:draft="drafts[f.key]"
               :field="f" :type="m.type" :links="m.body_links" open all @save="save" @cancel="closeEditor" /></div>
          <SaveBar id="dSaveAll" v-model:note="note" :label="t('dr.saveAll')" @save="save" @cancel="closeEditor" />
        </template>
        <div v-else class="rec-stage">
          <BlockIndex :fields="fields" :type="m.type" :sel="sel" @pick="pick" @mark="toMark" />
          <FieldEditor :key="fields[sel].key" v-model:draft="drafts[fields[sel].key]" v-model:note="note"
                       :field="fields[sel]" :type="m.type" :links="m.body_links" :open="editing.key === fields[sel].key"
                       :all="false" @edit="openEditor(fields[sel].key, false)" @save="save" @cancel="closeEditor" />
        </div>
        <div v-if="m.referenced_by_diagrams?.length" class="rf">
          <header class="rf-head"><span class="rf-label">{{ t('dr.inDiagrams') }}</span><span class="rf-count">{{
            m.referenced_by_diagrams.length }}</span></header>
          <div class="dg-links">
            <div v-for="r in m.referenced_by_diagrams" :key="`${r.memory_uid}:${r.node_key}`" class="dg-link">
              <span class="dg-key">{{ r.node_key }}</span> <button type="button" class="snippet clickable"
                    :data-open="r.memory_uid" @click="openRecord(r.memory_uid)">{{ r.title }}{{
                    r.label ? ` · ${r.label}` : '' }}</button>
            </div>
          </div>
        </div>
      </div>
      <MetaPanel :m="m" />
    </div>
  </div>
</template>
