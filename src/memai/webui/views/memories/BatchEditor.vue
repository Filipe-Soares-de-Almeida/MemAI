<script setup lang="ts">
/* The inspector's editor: confidence, tags and filed domain are staged and written together by
   Apply; archive, restore, send to a project and delete run when pressed. */
import { computed, ref } from 'vue';
import { esc } from '../../core/dom.ts';
import { CONF } from '../../core/shared.js';
import { t } from '../../i18n.ts';
import type { DomainEntry } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import ConfPill from '../../components/ConfPill.vue';
import DomainPicker from '../../components/DomainPicker.vue';
import { applyStaged, runAct } from './actions.ts';
import { confChanges, domainChanges, stagedCount, stagedTag } from './staged.ts';
import type { MemoriesState } from './state.ts';

const props = defineProps<{ state: MemoriesState; domains: DomainEntry[] }>();
const s = props.state;
const picked = s.picked;
const staged = s.staged;
const n = computed(() => picked.value.length);
const CONFS = Object.keys(CONF);

/* pressing the staged one again unstages it: the scale has no fourth "leave it alone" state */
const stageConf = (c: string) => { staged.confidence = staged.confidence === c ? '' : c; };

const tagDraft = ref('');
function onTagKey(e: KeyboardEvent) {
  if (e.key !== 'Enter' && e.key !== ',') return;
  e.preventDefault();
  const tag = stagedTag(tagDraft.value, staged);
  if (tag) staged.tags.push(tag);
  tagDraft.value = '';
}
const untag = (tag: string) => { staged.tags = staged.tags.filter(x => x !== tag); };

const writes = computed(() => stagedCount(picked.value, staged));
/* Said before Apply, in rows: how much of the selection each edit actually touches. */
const plan = computed(() => {
  const lines: string[] = [];
  if (staged.confidence) {
    lines.push(t('mem.mi.planConf', { n: confChanges(picked.value, staged.confidence),
                                      label: esc(CONF[staged.confidence].label) }));
  }
  if (staged.tags.length) {
    lines.push(t('mem.mi.planTags', { n: picked.value.length, list: esc(staged.tags.join(', ')) }));
  }
  if (staged.domain) {
    lines.push(t('mem.mi.planDomain', { n: domainChanges(picked.value, staged.domain), domain: esc(staged.domain) }));
  }
  if (lines.length) lines.push(t('mem.mi.planEdits', { n: writes.value }));
  return lines;
});

function clearAll() {
  s.clearStaged();
  s.selection.clear();
}
</script>

<template>
  <div class="mi-body">
    <div class="mi-field">
      <div class="mg-label">{{ t('mem.mi.confidence') }}</div>
      <div class="mi-confs"><button v-for="c in CONFS" :key="c" type="button" class="mi-conf"
              :class="[`c-${c}`, { on: staged.confidence === c }]" :data-conf="c" :aria-pressed="staged.confidence === c"
              @click="stageConf(c)">
        <span class="mi-radio"></span>
        <ConfPill :confidence="c" />
        <span class="mi-conf-n">{{ confChanges(picked, c)
          ? t('mem.mi.nChange', { n: confChanges(picked, c) }) : t('mem.mi.allAlready') }}</span>
      </button></div>
    </div>

    <div class="mi-field">
      <label class="mg-label" for="miTag">{{ t('mem.mi.addTags') }}</label>
      <div class="mi-tagbox">
        <button v-for="tag in staged.tags" :key="tag" type="button" class="chip clickable" :data-untag="tag"
                :title="t('mem.mi.tagOff')" @click="untag(tag)">{{ tag }}<AppIcon name="close" /></button>
        <input id="miTag" v-model="tagDraft" type="text" autocomplete="off" spellcheck="false"
               :placeholder="t('mem.mi.tagPlaceholder')" @keydown="onTagKey">
      </div>
    </div>

    <div class="mi-field">
      <div class="mg-label">{{ t('mem.mi.rehome') }}</div>
      <DomainPicker id="miDomain" v-model="staged.domain" :domains="domains" :aria-label="t('mem.mi.rehome')"
                    :any-label="t('mem.mi.rehomeNone')" />
    </div>

    <div class="mi-field">
      <div class="mg-label">{{ t('mem.mi.otherActions') }}</div>
      <div class="mi-actions">
        <button type="button" class="btn btn-sm" data-act="archive" @click="runAct('archive', picked)">{{
          t('mem.mi.archiveN', { n }) }}<span class="hint-sm">{{ t('mem.mi.reversible') }}</span></button>
        <button type="button" class="btn btn-sm" data-act="restore"
                @click="runAct('restore', picked)">{{ t('common.restore') }}</button>
        <button type="button" class="btn btn-sm" data-act="project"
                @click="runAct('project', picked)">{{ t('bulk.move') }}</button>
        <button type="button" class="btn btn-sm btn-danger" data-act="purge" @click="runAct('purge', picked)">{{
          t('bulk.purge.button') }}<span class="hint-sm">{{ t('bulk.purge.irreversible') }}</span></button>
      </div>
    </div>

    <!-- the catalog marks the counts up; the values in it are escaped -->
    <div v-if="plan.length" class="mi-plan">
      <div class="mg-label">{{ t('mem.mi.planTitle') }}</div>
      <span v-for="(line, i) in plan" :key="i" v-html="line"></span>
    </div>
  </div>
  <div class="mi-foot">
    <button type="button" class="btn btn-solid" data-apply :disabled="!writes"
            @click="applyStaged(picked, staged)">{{ t('mem.mi.apply', { n }) }}</button>
    <button type="button" class="btn" data-clear @click="clearAll">{{ t('mem.mi.clear') }}</button>
  </div>
</template>
