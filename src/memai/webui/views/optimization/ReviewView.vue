<script setup lang="ts">
/* A list of suggestions to pick from beside the evidence for the picked one: ticks build a
   selection the footer applies or rejects, the cursor walks with the arrows and Space ticks. */
import { computed, nextTick, onBeforeUnmount, reactive, ref, shallowRef } from 'vue';
import { confirmModal, failed, toast } from '../../core/ui.js';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { Suggestion } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import LoadFailed from '../../components/LoadFailed.vue';
import BackButton from './BackButton.vue';
import SuggestionEvidence from './SuggestionEvidence.vue';
import { reportApplied, rewriteShare, rowName, upTo } from './suggestion.ts';
import type { Scope } from './scope.ts';

const props = defineProps<{ scope: Scope }>();

const items = shallowRef<Suggestion[]>([]);
const state = ref<'loading' | 'ready' | 'failed'>('loading');
const error = ref('');
const sub = ref(props.scope.sub);
const picked = ref(0);
/* the ids the footer's two buttons act on */
const marked = reactive(new Set<number>());
/* where a Shift-extended range starts */
let anchor = 0;
const busy = ref(false);
let alive = true;
onBeforeUnmount(() => { alive = false; });

const pendingIds = computed(() => items.value.filter(s => s.status === 'pending').map(s => s.id));
const chosen = computed(() => pendingIds.value.filter(id => marked.has(id)));
const current = computed(() => items.value[picked.value]);

/* only the header's counts are read again; the list stays as the reader is walking it */
async function reloadHead() {
  const fresh = await props.scope.refresh();
  if (alive) sub.value = fresh.sub;
}

async function load() {
  try {
    const r = await client.optimization.suggestions(props.scope.query);
    if (!alive) return;
    items.value = r.suggestions;
    /* the footer counts only what is still pending */
    const open = new Set(pendingIds.value);
    for (const id of [...marked]) if (!open.has(id)) marked.delete(id);
    picked.value = Math.max(0, Math.min(picked.value, items.value.length - 1));
    state.value = 'ready';
    await reloadHead();
  } catch (err) {
    if (!alive) return;
    error.value = err instanceof Error ? err.message : '';
    state.value = 'failed';
  }
}
void load();

/* A scope that asked only for what was open cannot hold a decided row, so there the row is marked
   where it stands; a scope holding the whole history fetches again and shows it as the list's state. */
async function settle(id: number, status: string) {
  if (!props.scope.keepDecided) { await load(); return; }
  items.value = items.value.map(s => (s.id === id ? { ...s, status } : s));
  marked.delete(id);
  await reloadHead();
}

async function act(call: (body: { id: number }) => Promise<{ backup?: unknown } | unknown>, msg: string,
                   status: string, undoable: boolean) {
  const s = current.value;
  if (!s) return;
  busy.value = true;
  try {
    const res = await call({ id: s.id }) as { backup?: unknown } | null;
    toast(res && res.backup ? t('op.toast.appliedBackup') : msg, 'ok', undoable ? {
      action: {
        label: t('common.undo'),
        run: () => client.optimization.revert({ id: s.id })
          .then(async () => { toast(t('op.toast.reverted'), 'ok'); await settle(s.id, 'pending'); })
          .catch(err => failed('err.optimize', err)),
      },
    } : {});
    await settle(s.id, status);
  } catch (err) { failed('err.optimize', err); }
  finally { busy.value = false; }
}

const rows = ref<HTMLElement | null>(null);
function pick(i: number, focus = false) {
  picked.value = Math.max(0, Math.min(items.value.length - 1, i));
  nextTick(() => {
    const row = rows.value?.querySelectorAll<HTMLElement>('.opt-row')[picked.value];
    row?.scrollIntoView({ block: 'nearest' });
    if (focus) row?.focus();
  });
}

function toggle(i: number, on?: boolean) {
  const s = items.value[i];
  if (!s || s.status !== 'pending') return;
  if (on ?? !marked.has(s.id)) marked.add(s.id);
  else marked.delete(s.id);
}

function clickRow(e: MouseEvent, i: number) {
  if ((e.target as Element).closest('input[type=checkbox]')) return;
  if (e.shiftKey) {
    const [lo, hi] = anchor < i ? [anchor, i] : [i, anchor];
    for (let j = lo; j <= hi; j++) toggle(j, true);
  } else {
    anchor = i;
  }
  pick(i);
}

function tick(i: number, e: Event) {
  toggle(i, (e.target as HTMLInputElement).checked);
  anchor = i;
}

function keys(e: KeyboardEvent) {
  const to = ({ ArrowDown: picked.value + 1, ArrowUp: picked.value - 1, Home: 0, End: items.value.length - 1 } as
              Record<string, number>)[e.key];
  if (to !== undefined) {
    e.preventDefault();
    pick(to, true);
    anchor = picked.value;
  } else if (e.key === ' ') {
    e.preventDefault();
    toggle(picked.value);
  }
}

function tickAll(e: Event) {
  marked.clear();
  if ((e.target as HTMLInputElement).checked) pendingIds.value.forEach(id => marked.add(id));
}

/* each operation names itself in its own confirm */
async function bulk(what: 'apply' | 'reject') {
  const ids = chosen.value;
  if (!ids.length) return;
  if (!(await confirmModal({ title: t(`op.sel.${what}Confirm.title`),
    body: t(`op.sel.${what}Confirm.body`, { n: ids.length, scope: props.scope.title }),
    okLabel: t(`op.sel.${what}Confirm.ok`) }))) return;
  try {
    const body = { ...props.scope.body, ids };
    if (what === 'apply') reportApplied(await client.optimization.applyAll(body));
    else toast(t('op.toast.rejectedN', { n: (await client.optimization.rejectAll(body)).rejected }), 'ok');
    marked.clear();
    void load();
  } catch (err) { failed('err.optimize', err); }
}

const retry = () => { state.value = 'loading'; void load(); };
</script>

<template>
  <div class="opt-shell">
    <div class="opt-bar">
      <BackButton :label="scope.back.label" @click="upTo(scope.back.to)" />
      <h2 class="opt-bar-title">{{ scope.title }} <em class="opt-run-when">· {{ sub }}</em></h2>
    </div>
    <div class="opt-two">
      <div id="optList" class="opt-list">
        <div v-if="state === 'loading'" class="loading"><span class="spin"></span></div>
        <LoadFailed v-else-if="state === 'failed'" :message="error" @retry="retry" />
        <div v-else-if="!items.length" class="empty">{{ scope.emptyMsg }}</div>
        <template v-else>
          <div class="opt-list-head">
            <input id="optSelAll" type="checkbox" :aria-label="t('op.sel.all')"
                   :checked="pendingIds.length > 0 && chosen.length === pendingIds.length"
                   :indeterminate="chosen.length > 0 && chosen.length < pendingIds.length" @change="tickAll">
            <span id="optSelCount" class="mg-label">{{ t('op.sel.ofN', { n: chosen.length, all: pendingIds.length }) }}</span>
          </div>
          <!-- a grid and not a listbox: a row owns a checkbox, which an option may not contain -->
          <div ref="rows" class="opt-rows" role="grid" aria-multiselectable="true" :aria-label="scope.listAria"
               @keydown="keys">
            <div v-for="(s, i) in items" :key="s.id" class="opt-row" :class="{ picked: i === picked, marked: marked.has(s.id) }"
                 role="row" :data-i="i" :aria-selected="marked.has(s.id)" :tabindex="i === picked ? 0 : -1"
                 @click="clickRow($event, i)">
              <span class="opt-row-box" role="gridcell"><input v-if="s.status === 'pending'" type="checkbox"
                    tabindex="-1" :aria-label="t('op.sel.one')" :checked="marked.has(s.id)" @change="tick(i, $event)"><AppIcon
                    v-else :name="s.status === 'applied' ? 'confirmed' : 'close'" cls="opt-row-mark" /></span>
              <span class="opt-row-body" role="gridcell">
                <span class="opt-row-name">{{ rowName(s) }}</span>
                <span class="opt-row-meta"><span v-if="s.target_uid" class="opt-row-uid">{{ s.target_uid }}</span><span
                      v-if="rewriteShare(s)">{{ rewriteShare(s) }}</span><span v-if="s.status === 'applied'"
                      class="opt-row-done">{{ t('op.applied') }}</span><span v-else-if="s.status === 'rejected'"
                      class="opt-row-done">{{ t('op.rejected') }}</span><span v-else-if="s.verified" class="opt-row-vf">{{
                      t('op.vf.checked') }}</span><span v-else class="opt-row-nvf">{{ t('op.vf.unchecked') }}</span></span>
              </span>
            </div>
          </div>
          <div id="optFoot" class="opt-list-foot">
            <span class="opt-foot-n">{{ t('op.sel.n', { n: chosen.length }) }}</span>
            <span class="opt-foot-gap"></span>
            <button type="button" class="btn btn-sm" data-selreject :disabled="!chosen.length" @click="bulk('reject')">{{
              t('op.sel.reject', { n: chosen.length }) }}</button>
            <button type="button" class="btn btn-solid btn-sm" data-selapply :disabled="!chosen.length"
                    @click="bulk('apply')">{{ t('op.sel.apply', { n: chosen.length }) }}</button>
          </div>
          <p class="hint-sm opt-keyhint">{{ t('op.sel.hint') }}</p>
        </template>
      </div>
      <div id="optDetail" class="panel opt-detail">
        <template v-if="state === 'ready' && items.length">
          <SuggestionEvidence v-if="current" :key="`${current.id}:${current.status}`" :s="current" :at="picked"
                              :total="items.length" :decide-here="!scope.day" :busy="busy"
                              @apply="act(client.optimization.apply, t('op.toast.applied1'), 'applied', true)"
                              @reject="act(client.optimization.reject, t('op.toast.rejected1'), 'rejected', false)"
                              @revert="act(client.optimization.revert, t('op.toast.reverted'), 'pending', false)" />
          <div v-else class="empty">{{ t('op.pickOne') }}</div>
        </template>
      </div>
    </div>
  </div>
</template>
