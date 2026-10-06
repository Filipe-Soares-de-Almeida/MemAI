<script setup lang="ts">
/* The relation link mode draws between two memories: its type and note. `done` reports whether one
   was created. */
import { ref } from 'vue';
import { REL_SUGGEST, typeColor } from '../../core/shared.js';
import { failed, toast } from '../../core/ui.js';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { GraphNode } from '../../engines/graph-arrange.ts';
import AppIcon from '../../components/AppIcon.vue';
import AppModal from '../../components/AppModal.vue';
import RelTypeField from '../../components/RelTypeField.vue';

const props = defineProps<{ from: GraphNode; to: GraphNode }>();
const emit = defineEmits<{ done: [created: boolean] }>();

const relType = ref('relates_to');
const note = ref('');

async function create() {
  try {
    await client.relations.create({ from_uid: props.from.uid, to_uid: props.to.uid,
                                    relation_type: relType.value || 'relates_to', note: note.value });
    emit('done', true);
    toast(t('dr.rel.created'), 'ok');
  } catch (err) { failed('err.relation', err); }
}
</script>

<template>
  <AppModal :title="t('g.modal.title')" :head-html="t('g.modal.title')" @close="emit('done', false)">
    <div class="gl-peers">
      <div><span class="dot" :style="{ '--c': typeColor(from.type), display: 'inline-block', marginRight: '6px' }"></span>{{
        from.uid }} · {{ from.name }}</div>
      <div style="color:var(--accent);padding-left:2px;--ico:15px"><AppIcon name="arrow-down" /></div>
      <div><span class="dot" :style="{ '--c': typeColor(to.type), display: 'inline-block', marginRight: '6px' }"></span>{{
        to.uid }} · {{ to.name }}</div>
    </div>
    <div class="field"><label for="glType">{{ t('g.modal.relType') }}</label>
      <div class="act-row"><RelTypeField id="glType" v-model="relType" custom-id="glTypeCustom" :options="REL_SUGGEST"
                                         :aria-label="t('g.modal.relType')" /></div></div>
    <div class="field"><label for="glNote">{{ t('g.modal.note') }}</label><input v-model="note" type="text" id="glNote"></div>
    <template #foot>
      <button class="btn" data-x @click="emit('done', false)">{{ t('common.cancel') }}</button>
      <button class="btn btn-solid" data-ok @click="create">{{ t('g.modal.create') }}</button>
    </template>
  </AppModal>
</template>
