<script setup lang="ts">
/* One column of the tree: the levels inside its parent, each a link to walk in and a handle to drag.
   A drop on a level nests under it; a drop on the column's empty space makes a sibling of its rows. */
import { fmtInt } from '../../core/dom.ts';
import { domainLeaf } from '../../core/domains.ts';
import { go } from '../../core/router.ts';
import { t } from '../../i18n.ts';
import type { DomainEntry } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import { enqueue, queue } from './store.ts';
import { canMove, isArchived, isCrossing } from './tree.ts';
import type { Column } from './tree.ts';

export interface Drag { from: string; on: Set<string> }

const props = defineProps<{ column: Column; byPath: Map<string, DomainEntry>; drag: Drag }>();

const queuedTo = (path: string) => queue.value.find(m => m.from === path)?.to;
const open = (path: string) => go('domains', { path });

function onDragStart(e: DragEvent, path: string) {
  e.dataTransfer?.setData('text/plain', path);
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
  props.drag.from = path;
}

function onDragEnd() {
  props.drag.from = '';
  props.drag.on.clear();
}

/* `key` names the target for its highlight, `to` the parent a drop there files the level under */
function onDragOver(e: DragEvent, key: string, to: string) {
  if (!props.drag.from || !canMove(props.drag.from, to)) return;
  e.preventDefault();
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
  props.drag.on.add(key);
}

function onDrop(e: DragEvent, key: string, to: string) {
  e.preventDefault();
  e.stopPropagation();
  props.drag.on.delete(key);
  const from = e.dataTransfer?.getData('text/plain') || '';
  if (from && canMove(from, to)) enqueue(from, to, props.byPath.get(from));
}
</script>

<template>
  <div class="dom-col" :data-parent="column.parent" :style="{ '--depth': column.depth }">
    <div class="dom-col-head">{{ column.parent ? `${domainLeaf(column.parent)} · ${column.kids.length}`
                                               : t('do.col.roots', { n: column.kids.length }) }}</div>
    <div class="dom-col-body" :class="{ 'drop-on': drag.on.has(`B:${column.parent}`) }"
         :data-drop-parent="column.parent" @dragover="onDragOver($event, `B:${column.parent}`, column.parent)"
         @dragleave="drag.on.delete(`B:${column.parent}`)" @drop="onDrop($event, `B:${column.parent}`, column.parent)">
      <div v-for="d in column.kids" :key="d.domain" class="dom-level"
           :class="{ on: d.domain === column.picked, queued: queuedTo(d.domain) !== undefined,
                     'is-dragged': drag.from === d.domain, 'drop-on': drag.on.has(`L:${d.domain}`) }"
           draggable="true" :data-path="d.domain" tabindex="0" role="button"
           :aria-current="d.domain === column.picked ? 'true' : 'false'" :title="d.domain"
           @click="open(d.domain)" @keydown.enter.space.prevent="open(d.domain)"
           @dragstart="onDragStart($event, d.domain)" @dragend="onDragEnd"
           @dragover="onDragOver($event, `L:${d.domain}`, d.domain)" @dragleave="drag.on.delete(`L:${d.domain}`)"
           @drop="onDrop($event, `L:${d.domain}`, d.domain)">
        <span class="dom-grip" aria-hidden="true"><AppIcon name="grip" /></span>
        <span class="dom-name" :class="{ implicit: d.implicit }">{{ domainLeaf(d.domain) }}</span>
        <span v-if="queuedTo(d.domain) !== undefined" class="dom-arrow">→ {{ queuedTo(d.domain) }}</span>
        <span v-if="isArchived(d)" class="status-tag archived"
              :title="t('do.tree.archivedWhy')">{{ t('do.tree.archivedTag') }}</span>
        <span v-if="isCrossing(d)" class="dom-count crossing"
              :title="t('do.tree.crossingWhy')">{{ t('do.col.alsoN', { n: fmtInt(d.subtree_also) }) }}</span>
        <span v-else class="dom-count">{{ fmtInt(d.subtree_active || d.active) }}</span>
        <!-- the slot stays on a leaf too, or every count in the column shifts by its width -->
        <AppIcon v-if="d.children" name="chevron-right" cls="dom-into" />
        <span v-else class="dom-into" aria-hidden="true"></span>
      </div>
      <div class="dom-col-rest" :class="{ 'drop-on': drag.on.has(`R:${column.parent}`) }"
           :data-drop-parent="column.parent" @dragover="onDragOver($event, `R:${column.parent}`, column.parent)"
           @dragleave="drag.on.delete(`R:${column.parent}`)"
           @drop="onDrop($event, `R:${column.parent}`, column.parent)"></div>
      <div v-if="!column.parent" class="dom-col-hint">{{ t('do.drop.hint') }}</div>
    </div>
  </div>
</template>
