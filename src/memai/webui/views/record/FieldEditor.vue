<script setup lang="ts">
/* One field of a memory, read as rich text or open with its source beside what it becomes; in `all`
   mode every field is open under one shared save bar, with no preview. */
import { computed, ref, watch } from 'vue';
import { debounce, fmtInt } from '../../core/dom.ts';
import { sectionHue } from '../../core/shared.js';
import { t } from '../../i18n.ts';
import type { BodyLink } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import KeyHint from '../../components/KeyHint.vue';
import RichText from '../../components/RichText.vue';
import SaveBar from './SaveBar.vue';
import type { Field } from './record.ts';

const props = defineProps<{ field: Field; type: string; links: Record<string, BodyLink>; open: boolean;
                            all: boolean }>();
const draft = defineModel<string>('draft', { default: '' });
const note = defineModel<string>('note', { default: '' });
const emit = defineEmits<{ edit: []; save: []; cancel: [] }>();

const size = computed(() => (props.open ? draft.value : props.field.text).length);
/* the preview follows the source a beat behind the typing */
const preview = ref(draft.value);
watch(draft, debounce((text: string) => { preview.value = text; }, 180));
watch(() => props.open, open => { if (open) preview.value = draft.value; });

function key(e: KeyboardEvent) {
  if (e.key === 'Escape') { emit('cancel'); return; }
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); emit('save'); }
}
</script>

<template>
  <section class="rf" :class="{ 'is-open': open }" :data-field="field.key" :style="sectionHue(type, field.key)">
    <header class="rf-head">
      <span class="rf-dot" aria-hidden="true"></span>
      <span class="rf-label"><span v-if="field.raw !== undefined" class="sec-label-text" :title="field.raw">{{
        field.label }}</span><template v-else>{{ field.label }}</template></span>
      <span v-if="field.max" class="rf-count" :class="{ over: open && size > field.max }" data-count>{{
        t('dr.sections.count', { n: size, max: field.max }) }}</span>
      <span v-else class="rf-count">{{ t('dr.chars', { n: fmtInt(size) }) }}</span>
      <button v-if="!open" type="button" class="rf-edit" :data-edit="field.key" @click="emit('edit')"><AppIcon
              name="pencil" />{{ t('common.edit') }}</button>
      <span v-else-if="!all" class="rf-keys"><KeyHint save :action="t('dr.key.save')" /><KeyHint :keys="['Esc']"
            :action="t('dr.key.close')" /></span>
    </header>
    <template v-if="!open">
      <!-- the box that scrolls and the box that holds the reading measure are two, so the scrollbar
           sits at the card's edge -->
      <div v-if="field.present" class="rf-body"><RichText :text="field.text" :links="links" /></div>
      <div v-else class="sec-absent">{{ t('dr.sections.missing') }}</div>
    </template>
    <template v-else>
      <div class="rf-split" :class="{ 'rf-solo': all }">
        <div class="rf-pane">
          <span class="rf-sub">{{ t('dr.source') }}</span>
          <textarea v-model="draft" :data-src="field.key" rows="10" spellcheck="false" :aria-label="field.label"
                    @keydown="key"></textarea>
        </div>
        <div v-if="!all" class="rf-pane">
          <span class="rf-sub">{{ t('dr.asRead') }}</span>
          <RichText class="rf-preview" data-preview :text="preview" :links="links" />
        </div>
      </div>
      <SaveBar v-if="!all" id="dSave" v-model:note="note" :label="t('dr.saveVersion')" @save="emit('save')"
               @cancel="emit('cancel')" />
    </template>
  </section>
</template>
