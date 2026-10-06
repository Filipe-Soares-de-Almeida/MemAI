<script setup lang="ts">
/* Every metadata field of a memory in one form; `done` reports true once a save is accepted. */
import { reactive } from 'vue';
import { failed, toast } from '../../core/ui.js';
import { cachedDomains, invalidateDomains, typeItems } from '../../core/shared.js';
import { byDomainPath } from '../../core/domains.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { MemoryRecord } from '../../api/types.ts';
import AppModal from '../../components/AppModal.vue';
import Picker from '../../components/Picker.vue';
import type { PickItem } from '../../components/Picker.vue';

const props = defineProps<{ m: MemoryRecord }>();
const emit = defineEmits<{ done: [saved: boolean] }>();

/* a type the vocabulary does not know is still what this memory is, so it joins the list */
const types: PickItem[] = typeItems();
if (!types.some(it => it.value === props.m.type)) types.push({ value: props.m.type, label: props.m.type });
const domains = (cachedDomains() as Array<{ domain: string }>).slice().sort(byDomainPath);

const f = reactive({
  type: props.m.type, title: props.m.title || '', domain: props.m.domain,
  also: (props.m.also || []).join(', '), tags: props.m.tags, session: props.m.session,
});

async function save() {
  try {
    const r = await client.memories.meta(props.m.uid, { ...f });
    toast(r.changed.length ? t('mm.updated', { list: r.changed.join(', ') }) : t('mm.nothing'), 'ok');
    invalidateDomains();
    emit('done', true);
  } catch (err) { failed('err.save', err); }
}
</script>

<template>
  <AppModal :title="t('mm.title')" @close="emit('done', false)">
    <div class="field"><label for="mmType">{{ t('mm.type') }}</label>
      <Picker id="mmType" v-model="f.type" :items="types" :aria-label="t('mm.type')" /></div>
    <div class="field"><label for="mmName">{{ t('mm.name.label') }}</label>
      <input id="mmName" v-model="f.title" type="text"></div>
    <div class="field"><label for="mmDomain">{{ t('dr.meta.domain') }}</label>
      <input id="mmDomain" v-model="f.domain" type="text" list="mmDomainsDL"><datalist id="mmDomainsDL"><option
             v-for="d in domains" :key="d.domain" :value="d.domain"></option></datalist></div>
    <div class="field"><label for="mmAlso">{{ t('dr.meta.also') }}</label>
      <input id="mmAlso" v-model="f.also" type="text" :placeholder="t('mm.also.placeholder')" list="mmDomainsDL">
      <div class="hint-sm">{{ t('mm.also.hint') }}</div></div>
    <div class="field"><label for="mmTags">{{ t('mm.tags.label') }}</label>
      <input id="mmTags" v-model="f.tags" type="text"></div>
    <div class="field"><label for="mmSession">{{ t('dr.meta.session') }}</label>
      <input id="mmSession" v-model="f.session" type="text"></div>
    <div class="hint-sm">{{ t('mm.hint') }}</div>
    <template #foot>
      <button class="btn" data-x @click="emit('done', false)">{{ t('common.cancel') }}</button>
      <button class="btn btn-solid" data-ok @click="save">{{ t('common.save') }}</button>
    </template>
  </AppModal>
</template>
