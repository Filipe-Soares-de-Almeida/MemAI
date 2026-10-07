<script setup lang="ts">
/* The backups the store has taken: the fresh shelf grouped by date or reason with ticks to archive,
   restore or delete, and each zip read-only beside it. */
import { computed } from 'vue';
import { fmtBytes, fmtInt } from '../../core/dom.ts';
import { t } from '../../i18n.ts';
import AppIcon from '../../components/AppIcon.vue';
import LoadFailed from '../../components/LoadFailed.vue';
import BackupRail from './BackupRail.vue';
import FileRows from './FileRows.vue';
import OpButton from './OpButton.vue';
import { dateBucket, groupBy, reasonLabel, useMaintenance, zipLabel } from './maintenance.ts';
import { useShelf } from './shelf.ts';
import type { ShelfGroup } from './shelf.ts';

const shelf = useShelf(useMaintenance());
const { data, error, archive, files, sel, picked, project } = shelf;
void shelf.load();

/* by date the buckets come out newest first, as the files already are */
const groups = computed(() => groupBy(files.value, f =>
  shelf.group.value === 'date' ? dateBucket(f.mtime) : reasonLabel(f.kind)));
const allPicked = computed(() => files.value.length > 0 && files.value.every(f => sel.has(f.name)));
const GROUPINGS: ShelfGroup[] = ['date', 'reason'];
</script>

<template>
  <div class="mnt-two">
    <BackupRail :shelf="shelf" />
    <section class="panel mnt-flush">
      <div class="mnt-shelf-head">
        <span class="mnt-shelf-title" id="bkTitle">{{
          !data ? '—' : archive ? zipLabel(archive.name, project) : project }}</span>
        <span class="mnt-shelf-meta" id="bkMeta">{{ !data ? '' : archive
          ? t('mn.bk.zipMeta', { n: fmtInt(archive.count), size: fmtBytes(archive.size), raw: fmtBytes(archive.raw) })
          : t('mn.bk.shelfMeta', { n: fmtInt(files.length), size: fmtBytes(shelf.loose.value) }) }}</span>
        <div class="mnt-shelf-acts" id="bkActs">
          <template v-if="data && archive">
            <button class="btn btn-sm" id="bkRenameZip" @click="shelf.renameZip">{{ t('mn.bk.renameZip') }}</button>
            <button class="btn btn-sm" id="bkUnzip" @click="shelf.unzip">{{ t('mn.bk.unzip') }}</button>
            <button class="btn btn-sm btn-danger" id="bkDropZip" @click="shelf.dropZip">{{ t('mn.bk.deleteZip') }}</button>
          </template>
          <template v-else-if="data">
            <span class="inline-label">{{ t('mn.bk.groupBy') }}
              <span class="seg" id="bkGroupSeg" role="group" :aria-label="t('mn.bk.groupBy')">
                <button v-for="g in GROUPINGS" :key="g" type="button" :data-g="g"
                        :aria-pressed="shelf.group.value === g ? 'true' : 'false'" @click="shelf.group.value = g">{{
                  t(g === 'date' ? 'mn.bk.byDate' : 'mn.bk.byReason') }}</button>
              </span></span>
            <OpButton op="backup" cls="btn btn-solid btn-sm" :label="t('mn.op.backup')" />
          </template>
        </div>
      </div>
      <div class="mnt-shelf-body" :class="{ pickable: data && !archive }" id="bkBody">
        <LoadFailed v-if="error" :message="error.message" @retry="shelf.load" />
        <div v-else-if="!data" class="loading"><span class="spin"></span></div>
        <template v-else-if="archive">
          <FileRows v-if="archive.count" :groups="[[t('mn.bk.inside'), archive.members]]" :pick="false" :shelf="shelf" />
          <div v-else class="empty">{{ t('mn.bk.emptyZip') }}</div>
        </template>
        <div v-else-if="!files.length" class="empty">{{ t('mn.backups.empty') }}</div>
        <template v-else>
          <div class="mnt-file mnt-file-head">
            <span><input type="checkbox" id="bkAll" :checked="allPicked" :aria-label="t('mn.bk.selectAll')"
                         :title="t('mn.bk.selectAll')"
                         @change="shelf.toggleAll(($event.target as HTMLInputElement).checked)"></span>
            <span></span><span>{{ t('mn.bk.th.backup') }}</span>
            <span>{{ t('mn.bk.th.taken') }}</span><span class="num">{{ t('mn.bk.th.size') }}</span>
          </div>
          <FileRows :groups="groups" pick :shelf="shelf" />
        </template>
      </div>
      <div class="mnt-sel" id="bkSelBar">
        <span v-if="data && archive" class="mnt-sel-text">{{ t('mn.bk.zipReadOnly') }}</span>
        <template v-else-if="data">
          <span class="mnt-sel-text" :class="{ 'is-on': picked.length }">{{ picked.length
            ? t('mn.bk.selN', { n: fmtInt(picked.length), size: fmtBytes(shelf.pickedSize.value) })
            : t('mn.bk.selNone') }}</span>
          <button class="btn btn-sm mnt-sel-zip" id="bkArchive" :disabled="!picked.length"
                  @click="shelf.archiveSelected"><AppIcon name="archive" />{{ t('mn.bk.archiveSel') }}</button>
          <button class="btn btn-sm" id="bkRestore" :disabled="picked.length !== 1" :title="t('mn.bk.restoreOne')"
                  @click="shelf.restore">{{ t('common.restore') }}</button>
          <button class="btn btn-sm btn-danger" id="bkDelete" :disabled="!picked.length" @click="shelf.remove">{{
            t('mn.bk.delete') }}</button>
        </template>
      </div>
    </section>
  </div>
</template>
