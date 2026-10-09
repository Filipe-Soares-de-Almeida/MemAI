<script setup lang="ts">
/* One checklist item: its state mark, its text and counts, its menu, and the panel it opens. */
import { computed, ref, watch } from 'vue';
import type { Directive } from 'vue';
import { TASK } from '../../contract.ts';
import { openDropMenu } from '../../core/ui.js';
import { openRecord } from '../../core/nav.ts';
import { t } from '../../i18n.ts';
import type { I18nKey } from '../../i18n.ts';
import type { TaskItem } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import TypeTag from '../../components/TypeTag.vue';
import TaskComment from './TaskComment.vue';
import TaskComposer from './TaskComposer.vue';
import TaskNotes from './TaskNotes.vue';
import { MARK, NEXT, STATES, spinAt } from './checklist.ts';
import type { Checklist } from './checklist.ts';

const props = defineProps<{ c: Checklist; item: TaskItem; n: number }>();
const { current, ui, enter } = props.c;

const key = computed(() => String(props.item.id));
const open = computed(() => ui.open === key.value);
const renaming = computed(() => ui.renaming === key.value);
const draft = ref('');
watch(renaming, on => { if (on) draft.value = props.item.text; }, { immediate: true });
const over = computed(() => draft.value.trim().length > TASK.ITEM_MAX);
const ready = computed(() => Boolean(draft.value.trim()) && !over.value);
function save(e?: KeyboardEvent) {
  if (e?.isComposing || !ready.value) return;
  void props.c.saveRename(key.value, draft.value);
}
const notes = computed(() => current.value.notes.filter(n => n.items.includes(props.item.id)));
const thread = computed(() => current.value.comments.filter(cm => cm.item === props.item.id));
const action = computed(() => t(`task.mark.${NEXT[props.item.state]}` as I18nKey));
const counts = computed(() => [
  { n: notes.value.length, icon: 'note', key: 'task.notes.n' as const },
  { n: props.item.links.length, icon: 'relation', key: 'task.links.n' as const },
  { n: thread.value.length, icon: 'comment', key: 'task.comments.n' as const },
].filter(x => x.n));

/* set once per arc, so a later redraw of the row does not shift a turn already under way */
const vSpin: Directive<HTMLElement, boolean> = {
  mounted: (el, { value }) => { if (value) el.style.setProperty('--spin-at', `${spinAt()}s`); },
};

function menu(e: MouseEvent) {
  const item = props.item;
  openDropMenu(e.currentTarget, [
    ...STATES.filter(s => s !== item.state).map(s => ({
      label: t(`task.mark.${s}` as I18nKey), danger: s === 'dropped',
      run: () => { props.c.setItem(key.value, s); },
    })),
    { sep: true },
    { label: t('task.item.rename'), run: () => props.c.renameItem(key.value) },
    { label: t('task.item.delete'), danger: true, run: () => props.c.deleteItem(key.value),
      note: current.value.items.length < 2 ? t('task.item.deleteLast') : '' },
  ], { align: 'right' });
}
</script>

<template>
  <li class="tk-item" :class="{ 'is-open': open, 'is-new': enter.items.has(key), 'is-leaving': ui.leaving === key,
             'is-flash': enter.flash === key }"
      :data-s="item.state" :data-key="key">
    <div class="tk-row">
      <span class="tk-num" aria-hidden="true">{{ n }}</span>
      <button type="button" class="tk-state" :class="{ 'is-pulse': enter.pulse === key }" :data-s="item.state"
              :data-step="key" :title="action"
              :aria-label="t('task.state.aria', { n, text: item.text, state: t(`task.state.${item.state}` as I18nKey), action })"
              @click="c.setItem(key, NEXT[item.state])">
        <span :key="item.state" v-spin="item.state === 'doing'" class="tk-ring"><AppIcon v-if="MARK[item.state]"
              :name="MARK[item.state]" /></span>
      </button>
      <div v-if="renaming" class="tk-rename">
        <input v-model="draft" type="text" class="tk-rename-box" :data-rename="key"
               :aria-label="t('task.item.renameAria', { n })"
               @keydown.enter.prevent="save" @keydown.esc.prevent="c.leaveRename()">
        <div class="tk-rename-foot">
          <button type="button" class="btn btn-sm btn-solid" data-rename-save :disabled="!ready" @click="save()">{{
            t('common.save') }}</button>
          <button type="button" class="btn btn-sm btn-ghost" @click="c.leaveRename()">{{ t('common.cancel') }}</button>
          <span class="rf-count" :class="{ over }" data-rename-count>{{
            t('dr.sections.count', { n: draft.trim().length, max: TASK.ITEM_MAX }) }}</span>
        </div>
      </div>
      <button v-else type="button" class="tk-main" :data-toggle="key" :aria-expanded="open"
              :aria-controls="open ? `tkp-${key}` : undefined" @click="c.toggle(key)">
        <span class="tk-text">{{ item.text }}</span>
        <span class="tk-counts"><span v-for="x in counts" :key="x.icon" class="tk-count" :title="t(x.key, { n: x.n })"><span
              aria-hidden="true"><AppIcon :name="x.icon" />{{ x.n }}</span><span class="sr-only">{{
              t(x.key, { n: x.n }) }}</span></span></span>
        <span class="tk-chev"><AppIcon name="chevron-right" /></span>
      </button>
      <button type="button" class="icon-btn tk-menu" :data-menu="key" :title="t('task.item.menu')"
              :aria-label="t('task.item.menuNamed', { text: item.text })" @click="menu"><AppIcon name="more" /></button>
    </div>
    <div v-if="open" :id="`tkp-${key}`" class="tk-panel" :class="{ 'is-enter': enter.opened === key }"
         role="group" :aria-label="t('task.panel.aria', { text: item.text })">
      <TaskNotes :c="c" :scope="key" :notes="notes" />
      <div class="tk-sub">
        <h3 class="tk-sub-h">{{ t('task.links') }}<span class="rs-n">{{ item.links.length }}</span></h3>
        <button type="button" class="rs-more" :data-link="key" @click="c.link(item)">{{ t('task.link.add') }}</button>
      </div>
      <div v-if="item.links.length" class="tk-links"><div v-for="l in item.links" :key="l.uid" class="rs-rel tk-link">
        <TypeTag :type="l.type" />
        <button type="button" class="snippet tk-link-open" :data-open="l.uid" :title="l.uid"
                @click="openRecord(l.uid)">{{ l.title || l.uid }}</button>
        <button type="button" class="icon-btn danger" :data-unlink="l.uid" :data-item="key"
                :title="t('task.link.remove')" :aria-label="t('task.link.removeNamed', { title: l.title || l.uid })"
                @click="c.unlink(item, l.uid)"><AppIcon name="close" /></button>
      </div></div>
      <div v-else class="hint-sm">{{ t('task.links.empty') }}</div>
      <div class="tk-sub"><h3 class="tk-sub-h">{{ t('task.comments') }}<span class="rs-n">{{ thread.length }}</span></h3></div>
      <div class="tk-cs">
        <TaskComment v-for="cm in thread" :key="cm.id" :comment="cm" :fresh="enter.comments.has(cm.id)"
                     :refs="current.refs" :on-item="c.flashItem" />
        <TaskComposer :c="c" :scope="key" :reply="thread.length > 0" />
      </div>
    </div>
  </li>
</template>
