<script setup lang="ts">
/* The shelf and its zips as places to read, and what the store and its copies take on disk. */
import { computed } from 'vue';
import { fmtBytes, fmtInt } from '../../core/dom.ts';
import { t } from '../../i18n.ts';
import AppIcon from '../../components/AppIcon.vue';
import StackedBar from './StackedBar.vue';
import { useMaintenance, zipLabel } from './maintenance.ts';
import type { Shelf } from './shelf.ts';

const props = defineProps<{ shelf: Shelf }>();
const { health, show } = useMaintenance();

const store = computed(() => health.value?.file.size ?? 0);
const parts = computed(() => [
  { value: store.value, fill: 'var(--accent)' },
  { value: props.shelf.loose.value, fill: 'rgba(187, 134, 252, .42)' },
  { value: props.shelf.zipped.value, fill: 'var(--zip)' },
]);
</script>

<template>
  <aside class="mnt-rail2">
    <div>
      <div class="mnt-rail-head">{{ t('mn.bk.fresh') }} <b id="bkTotal">{{
        shelf.data.value ? fmtBytes(shelf.loose.value) : '' }}</b></div>
      <div id="bkShelves">
        <button v-if="shelf.data.value" type="button" class="mnt-shelf" data-shelf=""
                :aria-current="shelf.archive.value ? 'false' : 'true'" @click="shelf.open(null)"><AppIcon
                name="folder" /><span class="mnt-shelf-name">{{ shelf.project.value }}</span>
          <span class="mnt-shelf-count">{{ fmtInt(shelf.files.value.length) }}</span></button>
      </div>
    </div>
    <div>
      <div class="mnt-rail-head is-zip">{{ t('mn.bk.archived') }} <b id="bkZipTotal">{{
        shelf.data.value ? fmtBytes(shelf.zipped.value) : '' }}</b></div>
      <div id="bkArchives">
        <template v-if="shelf.data.value">
          <button v-for="a in shelf.data.value.archives" :key="a.name" type="button" class="mnt-shelf is-zip"
                  :data-shelf="a.name" :aria-current="shelf.archive.value?.name === a.name ? 'true' : 'false'"
                  :title="a.name"
                  @click="shelf.open(a.name)"><AppIcon name="archive" />
            <span class="mnt-shelf-name">{{ zipLabel(a.name, shelf.project.value) }}</span>
            <span class="mnt-shelf-count">{{ fmtInt(a.count) }}</span></button>
          <p v-if="!shelf.data.value.archives.length" class="mnt-rail-empty">{{ t('mn.bk.noZips') }}</p>
        </template>
      </div>
    </div>
    <div class="mnt-disk">
      <div class="mnt-disk-label">{{ t('mn.bk.onDisk') }}</div>
      <div class="mnt-disk-value" id="bkDiskAll">{{
        shelf.data.value ? fmtBytes(store + shelf.loose.value + shelf.zipped.value) : '—' }}</div>
      <div class="mnt-disk-split" id="bkDiskSplit">{{ shelf.data.value ? t('mn.bk.diskSplit', {
        store: fmtBytes(store), loose: fmtBytes(shelf.loose.value), zip: fmtBytes(shelf.zipped.value) }) : '' }}</div>
      <div id="bkBar"><StackedBar v-if="shelf.data.value" :parts="parts" /></div>
      <button class="btn btn-sm" id="bkStorage" @click="show('storage')">{{ t('mn.bk.storageDetail') }}</button>
    </div>
  </aside>
</template>
