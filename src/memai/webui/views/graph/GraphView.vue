<script setup lang="ts">
/* The relations graph: the chrome around engines/graph-2d.ts, which draws and owns the camera, the
   selection and the spotlight. The view holds the filters, the toggles, the cards and the link dialog. */
import { onBeforeUnmount, onMounted, reactive, ref, shallowRef } from 'vue';
import { debounce, esc, fmtInt } from '../../core/dom.ts';
import { openRecord } from '../../core/nav.ts';
import { go, refreshBehind, replaceParams } from '../../core/router.ts';
import { getDomains, TYPE_LABEL, TYPE_ORDER, typeColor, typeItems } from '../../core/shared.js';
import { openDialog, tipHide, tipShow } from '../../core/ui.js';
import type { ViewProps } from '../../core/vue.ts';
import { GraphCanvas } from '../../engines/graph-2d.ts';
import { ARRANGEMENTS, arrangement } from '../../engines/graph-arrange.ts';
import type { GraphNode as Drawn, Hit } from '../../engines/graph-arrange.ts';
import { t } from '../../i18n.ts';
import type { I18nKey } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { DomainEntry, GraphNode } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import DomainPicker from '../../components/DomainPicker.vue';
import Picker from '../../components/Picker.vue';
import TbField from '../../components/TbField.vue';
import DomainCard from './DomainCard.vue';
import LinkDialog from './LinkDialog.vue';
import NodeCard from './NodeCard.vue';
import { graphParams, initialMode, initialShow, readPrefs, writePrefs } from './graph.ts';
import type { GraphFilters } from './graph.ts';

const props = defineProps<ViewProps>();

/* what floats over the drawing, which the engine keeps names out from under */
const CHROME = ['.graph-mode', '.graph-legend', '.graph-card', '.link-banner', '.graph-settle'];

const prefs = readPrefs();
const filters: GraphFilters = {
  status: props.params.has('status') ? props.params.get('status') ?? '' : 'active',
  domain: props.params.get('domain') || '',
  type: props.params.get('type') || '',
  mode: initialMode(props.params.get('mode'), prefs),
};
const show = reactive(initialShow(prefs));

const [domains, data] = await Promise.all([
  getDomains().catch(() => []) as Promise<DomainEntry[]>,
  client.graph({ status: filters.status, domain: filters.domain, type: filters.type }),
]);

const counts: Record<string, number> = {};
data.nodes.forEach(n => { counts[n.type] = (counts[n.type] || 0) + 1; });
const legend = TYPE_ORDER.filter((tp: string) => counts[tp]);
const types = typeItems({ any: t('mem.f.allM') });
const modeItems = ARRANGEMENTS.map(a => ({ value: a.id, label: t(`g.mode.${a.id}` as I18nKey) }));

const root = ref<HTMLElement | null>(null);
const canvas = ref<HTMLCanvasElement | null>(null);
const engine = shallowRef<GraphCanvas | null>(null);
const blocked = ref<Error | null>(null);
const mode = ref(filters.mode);
const linkMode = ref(false);
const banner = ref('');
const settle = reactive<{ shown: boolean; progress: number | null }>({ shown: false, progress: null });
const find = ref('');
const findCount = ref('');
type Card = { kind: 'node'; node: Drawn & GraphNode; peers: Drawn[] } | { kind: 'domain'; hit: { domain: string; count: number } };
const card = shallowRef<Card | null>(null);
/* the card keeps its class once shown, hidden or not */
const carded = ref(false);

const nav = (patch: Partial<GraphFilters>) => go('graph', graphParams({ ...filters, mode: mode.value, ...patch }));

function onHover(target: Hit | null, x = 0, y = 0) {
  if (!target) { tipHide(); return; }
  if (target.uid === undefined) {
    tipShow(`<b>${esc(target.domain)}</b><br>${t('g.domainHolds', { n: fmtInt(target.count) })}`, x, y);
    return;
  }
  tipShow(`<b>${esc(target.name)}</b><br>${esc(target.type)} · ${esc(target.uid)}`
    + `${target.domain ? `<br><span style="color:var(--ink-3)">${esc(target.domain)}</span>` : ''}`, x, y);
}

function showCard(next: Card | null) {
  card.value = next;
  if (next) carded.value = true;
}

async function promptLink(from: Drawn, to: Drawn) {
  const created = await openDialog(LinkDialog, { from, to });
  if (created) refreshBehind();
  else engine.value?.clearLinkFrom();
}

function obstacles(): [number, number, number, number][] {
  const host = root.value;
  if (!host || !canvas.value) return [];
  const frame = canvas.value.getBoundingClientRect();
  return CHROME.flatMap(sel => {
    const el = host.querySelector<HTMLElement>(sel);
    if (!el || el.hidden || !el.offsetParent) return [];
    const r = el.getBoundingClientRect();
    return [[r.left - frame.left, r.top - frame.top, r.width, r.height] as [number, number, number, number]];
  });
}

onMounted(() => {
  if (!canvas.value) return;
  try {
    const drawn = new GraphCanvas(canvas.value, {
      nodes: data.nodes, edges: data.edges, colorOf: typeColor, mode: mode.value, show: { ...show },
      onSelect: node => showCard(node && drawn
        ? { kind: 'node', node: node as Drawn & GraphNode, peers: drawn.neighbours(node.uid) } : null),
      onSelectDomain: hit => showCard(hit ? { kind: 'domain', hit } : null),
      onOpen: node => openRecord(node.uid),
      onHover,
      onLink: (step, from, to) => {
        if (step === 'from') banner.value = t('g.banner.target', { uid: from.uid });
        else if (to) void promptLink(from, to);
      },
      onSettle: (progress, done) => {
        settle.shown = !done;
        settle.progress = Math.min(1, progress);
      },
      obstacles,
    });
    engine.value = drawn;
  } catch (err) {
    /* the browser's own words go on screen; a refused canvas is often a passing state, hence Retry */
    console.error('relations graph:', err);
    blocked.value = err instanceof Error ? err : new Error(String(err));
  }
});
onBeforeUnmount(() => { engine.value?.destroy(); tipHide(); });

/* swapped in place: the request, the selection and the spotlight all survive an arrangement */
function pickMode(next: string) {
  if (!engine.value) return;
  mode.value = engine.value.setMode(next);
  writePrefs({ mode: mode.value });
  replaceParams('graph', graphParams({ ...filters, mode: mode.value }));
}

function toggle(key: 'links' | 'domains' | 'names') {
  show[key] = !show[key];
  engine.value?.setShow({ [key]: show[key] });
  writePrefs({ [key]: show[key] });
}

function toggleLink() {
  if (!engine.value) return;
  linkMode.value = engine.value.toggleLinkMode();
  if (linkMode.value) banner.value = t('g.banner.source');
}

let match: Drawn | null = null;
function spot() {
  if (!engine.value) return;
  const hit = engine.value.spotlight(find.value);
  match = hit.first;
  findCount.value = find.value.trim()
    ? t('g.findCount', { n: fmtInt(hit.count), total: fmtInt(engine.value.nodes.length) }) : '';
}
const spotSoon = debounce(spot, 160);

function onFindKey(e: KeyboardEvent) {
  if (e.key !== 'Enter') return;
  e.preventDefault();
  spot();
  if (match) engine.value?.select(match.uid);
}
</script>

<template>
  <div ref="root" class="anim">
    <h2 class="sr-only">{{ t('g.title') }}</h2>
    <div class="view-note">{{ t('g.sub', { n: fmtInt(data.nodes.length), m: fmtInt(data.edges.length) })
      + (data.truncated ? ' · ' : '') }}<span v-if="data.truncated" style="color:var(--warn)">{{
      t('g.truncated', { n: fmtInt(data.nodes.length), total: fmtInt(data.total) }) }}</span></div>
    <!-- The controls sit above the drawing, not on it; the arrangement stays on the canvas, being a
         property of the drawing. -->
    <div class="list-toolbar toolbar-sm tb-labeled graph-bar">
      <!-- the spotlight fades what does not match and moves nothing; Enter travels to the best match -->
      <TbField :label="t('g.f.find')" cls="tb-grow graph-find-field"><input v-model="find" type="search" id="gFind"
               class="graph-find" spellcheck="false" autocomplete="off" :placeholder="t('g.find')" :aria-label="t('g.find')"
               @input="spotSoon" @keydown="onFindKey"></TbField>
      <TbField :label="t('mem.f.domain')"><DomainPicker id="gDomain" :model-value="filters.domain" :domains="domains"
               :aria-label="t('common.allDomains')" :any-label="t('mem.f.allM')"
               @update:model-value="d => nav({ domain: d })" /></TbField>
      <TbField :label="t('mem.f.type')"><Picker id="gType" :model-value="filters.type" :items="types"
               :aria-label="t('common.allTypes')" @pick="type => nav({ type })" /></TbField>
      <TbField :label="t('mem.f.status')"><div class="seg" role="group" :aria-label="t('mem.status.aria')">
        <button type="button" data-v="active" :aria-pressed="filters.status === 'active'"
                @click="nav({ status: 'active' })">{{ t('common.active') }}</button>
        <button type="button" data-v="" :aria-pressed="filters.status === ''" @click="nav({ status: '' })">{{
          t('common.all') }}</button>
      </div></TbField>
      <TbField :label="t('g.f.show')"><div class="seg" role="group" :aria-label="t('g.show')">
        <button type="button" id="gShowLinks" :aria-pressed="show.links" :title="t('g.show.links.hint')"
                @click="toggle('links')"><AppIcon name="relation" />{{ t('g.show.links') }}</button>
        <button type="button" id="gShowDomains" :aria-pressed="show.domains" :title="t('g.show.domains.hint')"
                @click="toggle('domains')"><AppIcon name="folder" />{{ t('g.show.domains') }}</button>
        <button type="button" id="gShowNames" :aria-pressed="show.names" :title="t('g.show.names.hint')"
                @click="toggle('names')"><AppIcon name="label" />{{ t('g.show.names') }}</button>
      </div></TbField>
      <TbField><button type="button" class="btn btn-sm" :class="{ 'btn-solid': linkMode }" id="gLink"
               :aria-pressed="linkMode" @click="toggleLink"><AppIcon name="pencil" />{{ t('g.linkMode') }}</button>
      <button type="button" class="btn btn-sm" id="gFit" @click="engine?.fit()">{{ t('g.center') }}</button></TbField>
      <!-- last, so a count appearing on the first keystroke does not shove the row sideways -->
      <span id="gFindCount" class="graph-find-count" aria-live="polite">{{ findCount }}</span>
    </div>
    <div class="graph-wrap" id="gWrap">
      <div v-if="blocked" class="graph-blocked" role="alert">
        <p>{{ t('g.startFailed') }}</p>
        <p v-if="blocked.message" class="failed-detail">{{ blocked.message }}</p>
        <div class="act-row">
          <button type="button" class="btn btn-solid" id="gRetry" @click="refreshBehind()">{{ t('common.retry') }}</button>
          <button type="button" class="btn" id="gToList" @click="go('memories')">{{ t('nav.memories') }}</button>
        </div>
      </div>
      <template v-else>
        <!-- a drawing, labelled as one; the same records are a list in Memories -->
        <canvas ref="canvas" id="gGl" role="img"
                :aria-label="t('g.canvasAlt', { n: fmtInt(data.nodes.length), m: fmtInt(data.edges.length) })"></canvas>
        <div class="graph-mode">
          <Picker id="gMode" :model-value="mode" :items="modeItems" :aria-label="t('g.mode')" @pick="pickMode" />
        </div>
        <div class="graph-settle" id="gSettle" :hidden="!settle.shown">
          <span>{{ t('g.settling', { n: fmtInt(data.nodes.length) }) }}</span>
          <span class="gs-track"><span class="gs-fill" id="gSettleFill"
                :style="settle.progress === null ? undefined : { transform: `scaleX(${settle.progress.toFixed(3)})` }"></span></span>
        </div>
        <div class="graph-legend">
          <template v-if="legend.length"><span v-for="tp in legend" :key="tp" class="legend-item"><span class="dot"
            :style="{ '--c': typeColor(tp) }"></span>{{ TYPE_LABEL[tp] }} <b>{{ counts[tp] }}</b></span></template>
          <template v-else>{{ t('g.emptyLegend') }}</template>
          <span class="legend-note" id="gLegendNote">{{ t(arrangement(mode).note as I18nKey) }}</span>
        </div>
        <div id="gBanner" class="link-banner" :hidden="!linkMode">{{ banner }}</div>
        <div id="gCard" :class="{ 'graph-card': carded }" :hidden="!card">
          <NodeCard v-if="card?.kind === 'node'" :key="card.node.uid" :node="card.node" :peers="card.peers"
                    @close="engine?.select(null)" @hop="uid => engine?.select(uid)" />
          <DomainCard v-else-if="card?.kind === 'domain'" :key="card.hit.domain" :hit="card.hit"
                      @close="engine?.selectDomain(null)" @scope="d => nav({ domain: d })" />
        </div>
      </template>
    </div>
  </div>
</template>
