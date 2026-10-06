<script setup lang="ts">
/* Deletes a level and every memory filed under it, once "DELETE <domain>" is typed out; the field
   says why the button is still off. */
import { computed, ref } from 'vue';
import { esc, fmtInt } from '../../core/dom.ts';
import { inDomainPath } from '../../core/domains.ts';
import { failed, toast } from '../../core/toasts.ts';
import { invalidateDomains } from '../../core/shared.js';
import { go, refreshBehind } from '../../core/router.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { DomainEntry } from '../../api/types.ts';
import AppModal from '../../components/AppModal.vue';

const props = defineProps<{ node: DomainEntry; domains: DomainEntry[] }>();
const emit = defineEmits<{ done: [deleted: boolean] }>();

const d = props.node;
const filed = d.subtree_active + d.subtree_archived;
const levels = props.domains.filter(x => inDomainPath(x.domain, d.domain)).length;
const want = `DELETE ${d.domain}`;
const typed = ref('');
const armed = computed(() => typed.value === want);

async function remove() {
  try {
    const r = await client.domains.delete({ domain: d.domain, confirm: typed.value });
    emit('done', true);
    toast(t('do.del.done', { n: fmtInt(r.purged) })
          + (r.unlinked ? t('do.del.unlinked', { n: fmtInt(r.unlinked) }) : ''), 'ok');
    invalidateDomains();
    /* the level that was showing is gone; the pane goes back to its parent */
    go('domains', { path: d.parent || '' });
    refreshBehind();
  } catch (err) { failed('err.domainPurge', err); }
}
</script>

<!-- The catalog marks these sentences up; the domain in them is escaped. -->
<template>
  <AppModal :title="t('do.del.title')" @close="emit('done', false)">
    <!-- a purely cross-cutting level deletes no memory, and says what it does delete instead -->
    <div class="dz-hint" v-html="filed ? t('do.del.hint', { n: fmtInt(filed), domain: esc(d.domain) })
                                      : t('do.del.hintCrossing', { domain: esc(d.domain) })"></div>
    <div class="hint">{{ t('do.del.counts', { active: fmtInt(d.subtree_active),
                                               archived: fmtInt(d.subtree_archived), levels: fmtInt(levels) }) }}</div>
    <div v-if="d.subtree_also" class="hint warn">{{ t('do.del.crossing', { n: fmtInt(d.subtree_also) }) }}</div>
    <div class="dz-type" v-html="t('dz.typeThis', { phrase: `<code>DELETE ${esc(d.domain)}</code>` })"></div>
    <div class="dz-row">
      <input id="ddPhrase" v-model="typed" type="text" :aria-label="t('dz.phrase.aria')" autocomplete="off">
    </div>
    <div id="ddState" class="dz-state" :class="{ armed }" role="status">{{
      armed ? t('dz.armed') : typed ? t('dz.mismatch') : '' }}</div>
    <template #foot>
      <button class="btn" data-x @click="emit('done', false)">{{ t('common.cancel') }}</button>
      <button class="btn btn-danger" data-ok :disabled="!armed" @click="remove">{{ t('dz.button') }}</button>
    </template>
  </AppModal>
</template>
