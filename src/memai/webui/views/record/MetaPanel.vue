<script setup lang="ts">
/* The record's side: what the memory is and how far it is trusted, where it is filed and what it is
   tagged, its relations and history, and the acts that archive or delete it. */
import { computed } from 'vue';
import { esc, fmtDate, fmtInt } from '../../core/dom.ts';
import { failed, openDialog, promptModal, toast, typedConfirmModal } from '../../core/ui.js';
import { CONF, PIN, invalidateDomains } from '../../core/shared.js';
import { backTo, go } from '../../core/router.ts';
import { openRecord } from '../../core/nav.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { MemoryRecord } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import StatusTag from '../../components/StatusTag.vue';
import TypeTag from '../../components/TypeTag.vue';
import UidChip from '../../components/UidChip.vue';
import HistoryPanel from './HistoryPanel.vue';
import MetaDialog from './MetaDialog.vue';
import RelationsPanel from './RelationsPanel.vue';
import { tagList, useRefresh } from './record.ts';

const props = defineProps<{ m: MemoryRecord }>();
const refresh = useRefresh();

const tags = computed(() => tagList(props.m.tags || ''));
const also = computed(() => props.m.also || []);

/* the three pin choices; a domain pin needs a domain, so a memory without one cannot take it */
const pins = computed(() => [
  { value: '', label: t('dr.pin.none'), title: t('dr.pin.noneWhy'), mark: 'none', icon: 'pin', off: false },
  { value: 'global', label: t('dr.pin.global'), title: t('dr.pin.globalWhy'), mark: 'global',
    icon: PIN.global.icon, off: false },
  { value: 'domain', label: t('dr.pin.domain'),
    title: props.m.domain ? t('dr.pin.domainWhy', { domain: props.m.domain }) : t('dr.pin.noDomain'),
    mark: 'domain', icon: PIN.domain.icon, off: !props.m.domain },
]);

async function write(call: () => Promise<unknown>, done: string) {
  try {
    await call();
    toast(done, 'ok');
    refresh();
  } catch (err) { failed('err.save', err); }
}

function setConfidence(c: string) {
  if (c === props.m.confidence) return;
  void write(() => client.memories.confidence(props.m.uid, { confidence: c }), t('dr.confSet', { label: CONF[c].label }));
}

function setPin(pin: { value: string; label: string; off: boolean }) {
  if (pin.off || pin.value === (props.m.pin || '')) return;
  void write(() => client.memories.pin(props.m.uid, { pin: pin.value }), t('dr.pinSet', { label: pin.label }));
}

async function writeMeta(body: Record<string, string>) {
  try {
    await client.memories.meta(props.m.uid, body);
    invalidateDomains();
    refresh();
  } catch (err) { failed('err.save', err); }
}

async function addMeta(kind: 'also' | 'tag') {
  const value = await promptModal({
    title: kind === 'also' ? t('dr.meta.addDomain') : t('dr.meta.addTag'),
    label: kind === 'also' ? t('dr.meta.domain') : t('dr.meta.tags'),
    okLabel: t('common.add'),
  }) as string | null;
  if (value === null || !value.trim()) return;
  await writeMeta(kind === 'also' ? { also: [...also.value, value.trim()].join(', ') }
                                  : { tags: [...tags.value, value.trim()].join(', ') });
}

const dropMeta = (kind: 'also' | 'tag', value: string) => writeMeta(kind === 'also'
  ? { also: also.value.filter(p => p !== value).join(', ') }
  : { tags: tags.value.filter(x => x !== value).join(', ') });

async function editMeta() {
  if (await openDialog(MetaDialog, { m: props.m })) refresh();
}

const setStatus = (status: string, reason?: string) =>
  client.memories.status(props.m.uid, reason === undefined ? { status } : { status, reason });

async function archive() {
  const reason = await promptModal({
    title: t('dr.archiveModal.title'), body: t('dr.archiveModal.body'),
    label: t('dr.archiveModal.label'), okLabel: t('common.archive'), danger: true }) as string | null;
  if (reason === null) return;
  try {
    await setStatus('archived', reason);
    toast(t('dr.archived'), 'ok', {
      action: {
        label: t('common.undo'),
        run: () => setStatus('active')
          .then(() => { toast(t('dr.restored'), 'ok'); refresh(); })
          .catch(err => failed('err.status', err)),
      },
    });
    refresh();
  } catch (err) { failed('err.status', err); }
}

/* no Undo: archiving again needs a reason, which this screen does not keep */
async function restore() {
  try {
    await setStatus('active');
    toast(t('dr.restored'), 'ok');
    refresh();
  } catch (err) { failed('err.status', err); }
}

/* the one irreversible act: the phrase typed out in full, the reversible option named beside it */
async function purge() {
  const phrase = `DELETE ${props.m.uid}`;
  const ok = await typedConfirmModal({
    title: t('dz.summary'), bodyHTML: `<div class="dz-hint">${esc(t('dz.hint'))}</div>`, phrase, okLabel: t('dz.button'),
  });
  if (!ok) return;
  try {
    await client.memories.purge(props.m.uid, { confirm: phrase });
    toast(t('dz.purged'), 'ok');
    backTo('memories');
  } catch (err) { failed('err.purge', err); }
}

</script>

<template>
  <aside class="rec-side" :data-uid="m.uid">
    <div class="rs-head">
      <div class="rs-head-row">
        <TypeTag :type="m.type" /><UidChip :uid="m.uid" />
        <span class="rs-status"><StatusTag v-if="m.status === 'archived'" status="archived" /><template v-else>{{
          t('common.active') }}</template></span>
      </div>
      <div id="dConf" class="seg rs-conf" role="group" :aria-label="t('dr.curation')">
        <button v-for="(meta, c) in CONF" :key="c" type="button" :data-c="c" :aria-pressed="m.confidence === c"
                @click="setConfidence(String(c))"><span class="conf-pill" :class="`c-${c}`"><AppIcon
                :name="meta.icon" /></span>{{ meta.label }}</button>
      </div>
    </div>

    <div class="rs-body">
      <div class="rs-field">
        <div class="rs-field-head"><span id="dPinLabel" class="mg-label">{{ t('dr.pin.label') }}</span></div>
        <div id="dPin" class="seg rs-pin" role="group" aria-labelledby="dPinLabel">
          <button v-for="p in pins" :key="p.value" type="button" :data-pin="p.value"
                  :aria-pressed="(m.pin || '') === p.value" :title="p.title" :disabled="p.off"
                  @click="setPin(p)"><span class="pin-mark" :class="`pin-${p.mark}`"><AppIcon
                  :name="p.icon" /></span>{{ p.label }}</button>
        </div>
      </div>

      <div class="rs-field">
        <div class="rs-field-head"><span class="mg-label">{{ t('dr.meta.domain') }}</span><button id="dMeta"
             type="button" class="rs-more" @click="editMeta">{{ t('dr.meta.change') }}</button></div>
        <button type="button" class="rs-value" :data-fdomain="m.domain" :disabled="!m.domain"
                @click="go('memories', { domain: m.domain })">{{ m.domain || t('mem.mi.noDomain') }}</button>
      </div>

      <div class="rs-field">
        <div class="rs-field-head"><span class="mg-label">{{ t('dr.meta.also') }}</span><span class="rs-n">{{
          also.length }}</span><button type="button" class="rs-more" data-add="also" @click="addMeta('also')">{{
          t('dr.meta.addDomain') }}</button></div>
        <div class="rs-chips">
          <span v-for="p in also" :key="p" class="rs-chip">{{ p }}<button type="button" class="rs-chip-x"
                data-drop="also" :data-value="p" :title="t('dr.meta.dropAlso')" :aria-label="t('dr.meta.dropAlso')"
                @click="dropMeta('also', p)"><AppIcon name="close" /></button></span>
          <span v-if="!also.length" class="hint-sm">{{ t('dr.meta.noAlso') }}</span>
        </div>
      </div>

      <div class="rs-field">
        <div class="rs-field-head"><span class="mg-label">{{ t('dr.meta.tags') }}</span><span class="rs-n">{{
          tags.length }}</span><button type="button" class="rs-more" data-add="tag" @click="addMeta('tag')">{{
          t('dr.meta.addTag') }}</button></div>
        <div class="rs-chips">
          <span v-for="x in tags" :key="x" class="rs-chip">{{ x }}<button type="button" class="rs-chip-x"
                data-drop="tag" :data-value="x" :title="t('dr.meta.dropTag')" :aria-label="t('dr.meta.dropTag')"
                @click="dropMeta('tag', x)"><AppIcon name="close" /></button></span>
          <span v-if="!tags.length" class="hint-sm">{{ t('mem.noTags') }}</span>
        </div>
      </div>

      <div class="rs-rows">
        <div><span>{{ t('dr.meta.session') }}</span><button v-if="m.session" type="button" class="rs-mono"
             :data-fsession="m.session" :title="m.session" :aria-label="t('a11y.filterSession', { session: m.session })"
             @click="go('memories', { session: m.session, status: '' })">{{ m.session }}</button><b v-else
             class="rs-mono">—</b></div>
        <div><span>{{ t('dr.meta.created') }}</span><b class="rs-mono" :title="m.created_at">{{
          fmtDate(m.created_at) }}</b></div>
        <div><span>{{ t('dr.meta.updated') }}</span><b class="rs-mono" data-rs="updated" :title="m.updated_at">{{
          fmtDate(m.updated_at) }}</b></div>
        <div><span>{{ t('dr.meta.size') }}</span><b class="rs-mono" data-rs="size">{{
          t('dr.chars', { n: fmtInt(m.content.length) }) }}</b></div>
        <div v-if="m.superseded_by"><span>{{ t('dr.meta.supersededBy') }}</span><button type="button" class="rs-mono"
             :data-open="m.superseded_by" @click="openRecord(m.superseded_by)">{{ m.superseded_by }}</button></div>
      </div>

      <RelationsPanel :uid="m.uid" :relations="m.relations" />
      <HistoryPanel :history="m.edit_history" />
    </div>

    <div class="rs-foot">
      <button v-if="m.status === 'active'" id="dArchive" class="btn btn-sm" @click="archive">{{
        t('dr.archiveSoft') }}</button>
      <button v-else id="dRestore" class="btn btn-sm" @click="restore">{{ t('common.restore') }}</button>
      <button id="dDelete" class="btn btn-sm btn-danger" @click="purge"><AppIcon name="trash" />{{
        t('dz.button') }}</button>
    </div>
  </aside>
</template>
