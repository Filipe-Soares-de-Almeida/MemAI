<script setup lang="ts">
/* The picked level: what is filed there, what is only cross-listed into it, and the operations
   that act on the whole bucket. */
import { ref } from 'vue';
import { fmtAgo, fmtInt } from '../../core/dom.ts';
import { domainLeaf } from '../../core/domains.ts';
import { openDialog, openDropMenu } from '../../core/ui.js';
import { go } from '../../core/router.ts';
import { openRecord } from '../../core/nav.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { DomainDetail, DomainEntry } from '../../api/types.ts';
import LoadFailed from '../../components/LoadFailed.vue';
import TypeTag from '../../components/TypeTag.vue';
import { archiveDomain, restoreDomain, sendToProject } from './actions.ts';
import DeleteDialog from './DeleteDialog.vue';
import RenameDialog from './RenameDialog.vue';

const props = defineProps<{ node: DomainEntry; domains: DomainEntry[] }>();
const d = props.node;

const data = ref<DomainDetail | null>(null);
const error = ref<Error | null>(null);

async function load() {
  data.value = null;
  error.value = null;
  try { data.value = await client.domains.detail({ domain: d.domain }); }
  catch (err) { error.value = err as Error; }
}
load();

/* status '' so the list shows the subtree whole: a domain filter there covers descendants */
const openMemories = () => go('memories', { domain: d.domain, status: '' });

function more(e: MouseEvent) {
  openDropMenu(e.currentTarget, [
    { label: t('do.act.toProject'), run: () => sendToProject(d) },
    { sep: true },
    { label: t('do.act.delete'), danger: true, run: () => openDialog(DeleteDialog, { node: d, domains: props.domains }) },
  ], { align: 'right' });
}
</script>

<template>
  <LoadFailed v-if="error" :message="error.message" @retry="load" />
  <div v-else-if="!data" class="loading"><span class="spin"></span></div>
  <template v-else>
    <div class="dom-detail-head">
      <div class="dom-detail-name">
        <span class="dom-detail-leaf">{{ domainLeaf(d.domain) }}</span>
        <span class="dom-detail-path">{{ d.domain }}</span>
      </div>
      <div class="dom-detail-facts">
        <span><b>{{ fmtInt(d.active) }}</b> {{ t('do.det.here') }}</span>
        <span><b>{{ fmtInt(d.subtree_active - d.active) }}</b> {{ t('do.det.below') }}</span>
        <span><b>{{ fmtInt(d.archived) }}</b> {{ t('do.det.archived') }}</span>
        <span class="crossing"><b>{{ fmtInt(d.also) }}</b> {{ t('do.det.alsoHere') }}</span>
        <span>{{ t('do.det.last', { when: fmtAgo(d.latest_at || d.subtree_latest_at) }) }}</span>
      </div>
      <div class="act-row">
        <button class="btn btn-solid btn-sm" data-open @click="openMemories">{{ t('do.det.openMemories') }}</button>
        <button class="btn btn-sm" data-move
                @click="openDialog(RenameDialog, { from: d.domain, domains })">{{ t('do.rn.move') }}</button>
        <button v-if="d.subtree_active" class="btn btn-sm" data-arch
                @click="archiveDomain(d)">{{ t('do.act.archive') }}</button>
        <button v-else-if="d.subtree_archived" class="btn btn-sm" data-rest
                @click="restoreDomain(d)">{{ t('do.act.restore') }}</button>
        <button class="btn btn-sm" data-more @click="more">{{ t('do.det.more') }}</button>
      </div>
    </div>
    <div class="dom-detail-body">
      <div class="mg-label">{{ t('do.det.storedHere') }}</div>
      <div class="dom-mems">
        <button v-for="m in data.filed" :key="m.uid" type="button" class="dom-mem" :data-uid="m.uid"
                @click="openRecord(m.uid)">
          <TypeTag :type="m.type" />
          <span class="dom-mem-title">{{ m.title || m.content }}</span>
          <span class="dom-mem-age">{{ fmtAgo(m.created_at) }}</span>
        </button>
        <div v-if="!data.filed.length" class="empty">{{ t('do.det.nothingFiled') }}</div>
        <button v-if="data.filed_total > data.filed.length" type="button" class="dom-mem-more" data-open
                @click="openMemories">{{ t('do.det.andMore', { n: fmtInt(data.filed_total - data.filed.length) }) }}</button>
      </div>
      <div v-if="data.crossing.length" class="dom-crossing">
        <div class="mg-label">{{ t('do.det.crossingTitle') }}</div>
        <button v-for="m in data.crossing" :key="m.uid" type="button" class="dom-cross" :data-uid="m.uid"
                @click="openRecord(m.uid)">
          <span class="dom-cross-home">{{ m.domain }}</span>
          <span class="dom-mem-title">{{ m.title || m.content }}</span>
        </button>
      </div>
    </div>
  </template>
</template>
