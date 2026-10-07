<script setup lang="ts">
/* The record's relations to its peers, each with its type and why, removable with an Undo; several
   peers are linked in one pass under one type and note. */
import { confirmModal, failed, toast } from '../../core/ui.js';
import { REL_SUGGEST, peerName, relLabel, relTypeTitle } from '../../core/shared.js';
import { pickMemories } from '../../core/link-picker.js';
import { openRecord } from '../../core/nav.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { RecordRelation } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import { useRefresh } from './record.ts';

const props = defineProps<{ uid: string; relations: RecordRelation[] }>();
const refresh = useRefresh();

/* a relation is made again from its own row, so deleting one needs no second trip through the picker */
const relink = (rel: RecordRelation) => client.relations.create({
  from_uid: rel.direction === 'out' ? props.uid : rel.peer.uid,
  to_uid: rel.direction === 'out' ? rel.peer.uid : props.uid,
  relation_type: rel.relation_type,
  note: rel.note || '',
});

async function remove(rel: RecordRelation) {
  const ok = await confirmModal({
    title: t('dr.rel.removeModal.title'), body: t('dr.rel.removeModal.body'),
    okLabel: t('dr.rel.removeModal.ok'), danger: true });
  if (!ok) return;
  try {
    await client.relations.delete(rel.id);
    toast(t('dr.rel.removed'), 'ok', {
      action: {
        label: t('common.undo'),
        run: () => relink(rel)
          .then(() => { toast(t('dr.rel.created'), 'ok'); refresh(); })
          .catch(err => failed('err.relation', err)),
      },
    });
    refresh();
  } catch (err) { failed('err.relation', err); }
}

/* Peers already related stay pickable: two memories can be tied twice under different types. */
async function add() {
  const chosen = await pickMemories({
    title: t('dr.rel.pickTitle'), exclude: props.uid, relOptions: REL_SUGGEST, relValue: 'relates_to',
    withNote: true, okLabel: t('dr.rel.link'),
  });
  if (!chosen?.uids.length) return;
  const relType = chosen.relation || 'relates_to';
  let made = 0;
  try {
    for (const target of chosen.uids) {
      await client.relations.create({ from_uid: props.uid, to_uid: target, relation_type: relType, note: chosen.note });
      made++;
    }
    toast(t('dr.rel.createdN', { n: made }), 'ok');
  } catch (err) {
    /* the ones made before the failure stay, and the count says how far it got */
    failed('err.relation', err, made ? { detail: t('dr.rel.createdN', { n: made }) } : {});
  }
  refresh();
}
</script>

<template>
  <div class="rs-field">
    <div class="rs-field-head"><span class="mg-label">{{ t('dr.relations') }}</span><span class="rs-n">{{
      relations.length }}</span><button id="relAdd" type="button" class="rs-more" @click="add">{{
      t('dr.rel.link') }}</button></div>
    <div v-for="r in relations" :key="r.id" class="rs-rel">
      <span class="rel-dir" :title="r.direction === 'out' ? t('dr.rel.out.title') : t('dr.rel.in.title')"><AppIcon
            :name="r.direction === 'out' ? 'arrow-right' : 'arrow-left'" /></span>
      <span class="rel-type-chip" :title="relTypeTitle(r.relation_type)">{{ relLabel(r.relation_type) }}</span>
      <span v-if="'missing' in r.peer" class="snippet rel-gone">{{ t('dr.rel.missing', { uid: r.peer.uid }) }}</span>
      <button v-else type="button" class="snippet" :class="{ 'mem-named': peerName(r.peer).named }"
              :data-open="r.peer.uid" :title="peerName(r.peer).hover || peerName(r.peer).text"
              @click="openRecord(r.peer.uid)">{{ peerName(r.peer).text }}</button>
      <button type="button" class="icon-btn danger" :data-delrel="r.id" :title="t('dr.rel.remove.title')"
              :aria-label="t('dr.rel.remove.title')" @click="remove(r)"><AppIcon name="close" /></button>
      <span v-if="r.note" class="rs-rel-note">{{ r.note }}</span>
    </div>
    <div v-if="!relations.length" class="hint-sm">{{ t('dr.rel.empty') }}</div>
  </div>
</template>
