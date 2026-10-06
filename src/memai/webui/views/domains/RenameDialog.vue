<script setup lang="ts">
/* Moves or renames a level as a parent and a name, never one typed path: the parent comes from the
   tree minus this level's own subtree, and the name arrives holding the leaf. */
import { computed, onMounted, ref } from 'vue';
import { fmtInt } from '../../core/dom.ts';
import { DOMAIN_SEP, domainLeaf, domainSegments, inDomainPath } from '../../core/domains.ts';
import { failed, toast } from '../../core/toasts.ts';
import { invalidateDomains } from '../../core/shared.js';
import { go, refreshBehind } from '../../core/router.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { DomainEntry } from '../../api/types.ts';
import AppModal from '../../components/AppModal.vue';
import DomainPicker from '../../components/DomainPicker.vue';
import { pathOf } from './tree.ts';

const props = defineProps<{ from: string; domains: DomainEntry[] }>();
const emit = defineEmits<{ done: [moved: boolean] }>();

const node = props.domains.find(d => d.domain === props.from);
const descendants = node ? node.subtree_active + node.subtree_archived - node.active - node.archived : 0;
const parents = props.domains.filter(d => !inDomainPath(d.domain, props.from));
const parent = ref(domainSegments(props.from).slice(0, -1).join(DOMAIN_SEP));
const name = ref(domainLeaf(props.from));
const nameField = ref<HTMLInputElement | null>(null);

/* a name written as a path still nests, so only the separators at its ends are dropped */
const target = computed(() => pathOf(parent.value, name.value.trim().replace(/^\/+|\/+$/g, '')));
const same = computed(() => target.value === props.from);
/* only a composed target is a cycle to warn about: the dialog opens on the path it is already at */
const cycle = computed(() => Boolean(target.value) && !same.value && inDomainPath(target.value, props.from));
const merges = computed(() => !same.value && !cycle.value
  && props.domains.some(d => d.domain === target.value && !d.implicit));

onMounted(() => { nameField.value?.focus(); nameField.value?.select(); });

async function apply() {
  const to = target.value;
  try {
    const r = await client.domains.rename({ from: props.from, to });
    emit('done', true);
    toast(t('do.rn.moved', { n: r.affected }) + (r.merged ? t('do.rn.merged') : ''), 'ok');
    invalidateDomains();
    go('domains', { path: to });
    refreshBehind();
  } catch (err) { failed('err.domain', err); }
}
</script>

<template>
  <AppModal :title="t('do.rn.rename')" @close="emit('done', false)">
    <div class="field"><label for="rnFrom">{{ t('do.rn.from') }}</label>
      <input id="rnFrom" type="text" :value="from" disabled></div>
    <div class="rn-target">
      <div class="field"><label for="rnParent">{{ t('do.rn.parent') }}</label>
        <DomainPicker id="rnParent" v-model="parent" :domains="parents" :aria-label="t('do.rn.parent')"
                      :any-label="t('do.rn.root')" /></div>
      <div class="field"><label for="rnName">{{ t('do.rn.name') }}</label>
        <input id="rnName" ref="nameField" v-model="name" type="text" autocomplete="off" spellcheck="false"></div>
    </div>
    <div class="rn-result">{{ t('do.rn.result') }} <code id="rnPath">{{ target || '—' }}</code></div>
    <!-- the catalog marks the warning up; it carries no values -->
    <div id="rnWarn" class="hint warn" :hidden="!merges" v-html="t('do.rn.warn')"></div>
    <div id="rnCycle" class="hint warn" :hidden="!cycle">{{ t('do.rn.cycle') }}</div>
    <div v-if="descendants" class="hint">{{ t('do.rn.subtree', { n: fmtInt(descendants) }) }}</div>
    <div class="hint-sm">{{ t('do.rn.hint') }}</div>
    <template #foot>
      <button class="btn" data-x @click="emit('done', false)">{{ t('common.cancel') }}</button>
      <button class="btn btn-solid" data-ok :disabled="!target || same || cycle" @click="apply">{{
        t('common.apply') }}</button>
    </template>
  </AppModal>
</template>
