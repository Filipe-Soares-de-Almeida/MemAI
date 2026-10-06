<script setup lang="ts">
/* Backup files under group headings, for the shelf and for a zip's members alike: only the shelf's
   rows can be ticked, pinned and named, so `pick` is what the two differ by. */
import { fmtAgo, fmtBytes, fmtDate, fmtInt } from '../../core/dom.ts';
import { t } from '../../i18n.ts';
import type { ArchiveMember } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import RenameField from './RenameField.vue';
import { reasonLabel } from './maintenance.ts';
import type { Shelf, ShelfRow } from './shelf.ts';

type Row = ShelfRow | (ArchiveMember & { kind?: undefined; pinned?: undefined });

const props = defineProps<{ groups: [string, Row[]][]; pick: boolean; shelf: Shelf }>();

const total = (rows: Row[]) => rows.reduce((n, r) => n + r.size, 0);
/* a typed name wins, then the reason it was taken for, which a by-reason heading already says */
const named = (f: Row) => f.label || (props.pick && props.shelf.group.value !== 'reason' ? reasonLabel(f.kind ?? '') : '');
</script>

<template>
  <template v-for="[name, rows] in groups" :key="name">
    <div class="mnt-group">
      <span class="mnt-group-name">{{ name }}</span>
      <span class="mnt-group-rule"></span>
      <span class="mnt-group-meta">{{ t('mn.bk.shelfMeta', { n: fmtInt(rows.length), size: fmtBytes(total(rows)) }) }}</span>
    </div>
    <div v-for="f in rows" :key="f.name" class="mnt-file" :class="{ 'is-picked': pick && shelf.sel.has(f.name) }">
      <span v-if="pick"><input type="checkbox" :data-pick="f.name" :checked="shelf.sel.has(f.name)"
                               :disabled="!!f.pinned" :title="f.pinned ? t('mn.bk.pinnedWhy') : ''" :aria-label="f.name"
                               @change="shelf.toggle(f.name, ($event.target as HTMLInputElement).checked)"></span>
      <AppIcon name="db-file" />
      <div class="mnt-file-main">
        <RenameField v-if="pick && shelf.renaming.value === f.name" :value="f.label || ''"
                     @save="shelf.saveName" @cancel="shelf.renaming.value = null" />
        <div v-else-if="!named(f)" class="mnt-file-label mnt-file-mono" :title="f.name">{{ f.name }}</div>
        <template v-else>
          <div class="mnt-file-label">{{ named(f) }}<span v-if="f.pinned" class="mnt-pin-mark"
                                                            :title="t('mn.bk.pinnedWhy')"><AppIcon name="pin" /></span></div>
          <div class="mnt-file-sub"><span class="mnt-file-name" :title="f.name">{{ f.name }}</span></div>
        </template>
      </div>
      <span class="mnt-file-when" :title="fmtDate(f.mtime)">{{ fmtAgo(f.mtime) }}</span>
      <span class="mnt-file-size">{{ fmtBytes(f.size) }}</span>
      <span v-if="pick" class="mnt-file-acts">
        <button type="button" class="icon-btn mnt-pin" :class="{ 'is-on': f.pinned }" :data-pin="f.name"
                :aria-pressed="f.pinned ? 'true' : 'false'" :title="t('mn.bk.pinnedWhy')"
                :aria-label="t('mn.bk.pinnedWhy')"
                :disabled="shelf.pinning.has(f.name)" @click="shelf.pin(f.name)"><AppIcon name="pin" /></button>
        <button type="button" class="icon-btn" :data-rename="f.name" :title="t('mn.bk.rename')"
                :aria-label="t('mn.bk.rename')" @click="shelf.renaming.value = f.name"><AppIcon name="pencil" /></button>
      </span>
    </div>
  </template>
</template>
