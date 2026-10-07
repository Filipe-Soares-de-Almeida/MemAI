<script setup lang="ts">
/* One kind of a run: how much of it is pending and checked, and the group-wide decisions on it.
   Each button carries its verb alone; what a press touches is named in the dialog it opens. */
import { computed } from 'vue';
import { fmtInt } from '../../core/dom.ts';
import { kindLabel, kindTitle } from '../../core/shared.js';
import { t } from '../../i18n.ts';
import type { KindGroup } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import { VERIFIED_FILL, groupWhat, verifiedState } from './suggestion.ts';

const props = defineProps<{ group: KindGroup; busy: boolean }>();
const emit = defineEmits<{ open: []; apply: []; reject: []; undo: [] }>();

const state = computed(() => verifiedState(props.group.verified, props.group.pending));
</script>

<template>
  <div class="opt-grp" :class="{ decided: !group.pending }">
    <span class="opt-grp-mark" :style="{ background: VERIFIED_FILL[state] }"></span>
    <button type="button" class="opt-grp-kind" :data-open="group.kind" :title="kindTitle(group.kind)"
            @click="emit('open')">{{ kindLabel(group.kind) }}</button>
    <span class="opt-grp-what">{{ groupWhat(group) }}</span>
    <span class="opt-grp-n" :class="{ nil: !group.pending }">{{ group.pending || '—' }}</span>
    <span class="opt-grp-val"><template v-if="group.pending"><b>{{ fmtInt(group.verified) }}</b> {{
      t('op.grp.ofN', { n: fmtInt(group.pending) }) }}</template><span v-else class="opt-grp-quiet">{{
      group.applied ? t('op.applied') : t('op.rejected') }}</span></span>
    <span class="opt-grp-acts">
      <template v-if="group.pending">
        <button type="button" class="btn btn-sm btn-ghost" :data-rejectkind="group.kind" :data-n="group.pending"
                :disabled="busy" @click="emit('reject')">{{ t('common.reject') }}</button>
        <button type="button" class="btn btn-sm" :data-applykind="group.kind" :data-n="group.pending"
                :disabled="busy" @click="emit('apply')">{{ t('common.apply') }}</button>
      </template>
      <button v-else-if="group.applied" type="button" class="btn btn-sm btn-ghost" :data-undokind="group.kind"
              :data-n="group.applied" :disabled="busy" @click="emit('undo')">{{ t('common.undo') }}</button>
      <button type="button" class="btn btn-sm opt-grp-go" :data-open="group.kind"
              :title="t('op.grp.open', { kind: kindLabel(group.kind) })" @click="emit('open')">{{
        t('op.grp.detail') }}<AppIcon name="chevron-right" /></button>
    </span>
  </div>
</template>
