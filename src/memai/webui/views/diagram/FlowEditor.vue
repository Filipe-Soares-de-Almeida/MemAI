<script setup lang="ts">
/* One flow being read or edited: the toolbar, the canvas the DiagramEditor engine draws, and the
   inspector. The engine owns geometry and drawing and calls back through its hooks. */
import { computed, onBeforeUnmount, onMounted, provide, ref } from 'vue';
import { openRecord } from '../../core/nav.ts';
import { openCtxMenu, tipHide, tipShow } from '../../core/ui.js';
import { DiagramEditor } from '../../engines/diagram-engine.ts';
import type { ContextHit } from '../../engines/diagram-engine.ts';
import { t } from '../../i18n.ts';
import type { DiagramRecord } from '../../api/types.ts';
import AppIcon from '../../components/AppIcon.vue';
import DiagramSide from './DiagramSide.vue';
import JumpNav from './JumpNav.vue';
import { ACTIONS, flowActions } from './actions.ts';
import { jumpHref, navOffers } from './diagram.ts';
import type { Arrival } from './diagram.ts';
import { EDITOR, useDiagramEditor } from './editor.ts';

const props = defineProps<{ uid: string; diagram: DiagramRecord; landOn: string; came: Arrival }>();

const ed = useDiagramEditor(props.uid, props.diagram, props.landOn);
const actions = flowActions(ed);
provide(EDITOR, ed);
provide(ACTIONS, actions);
const { data, selected, editing, engine, connecting, hint } = ed;

const canvas = ref<HTMLCanvasElement | null>(null);
const nav = ref<{ $el: HTMLElement } | null>(null);
const overlay = ref<HTMLElement | null>(null);

const offers = computed(() => navOffers(data.value, props.uid, selected.value, props.came));
const sub = computed(() => t('dg.sub', { n: data.value.nodes.length, m: data.value.edges.length,
                                         l: data.value.links.length, j: data.value.jumps.length }));
const LEGEND = [
  { sample: 'line-plain', color: 'var(--ink-2)', label: t('dg.legend.plain') },
  { sample: 'line-loop', color: 'var(--warn)', label: t('dg.legend.loop') },
  { sample: 'line-hot', color: 'var(--accent)', label: t('dg.legend.selected') },
];

function setEditing(on: boolean) {
  editing.value = on;
  engine.value?.setReadOnly(!on);
  ed.paintHint();
}

function toggleConnect() {
  engine.value?.toggleConnectMode();
  ed.paintHint();
}

/* Right-click: what is under the pointer decides the menu. Following a jump is reading, so it leads
   the menu in read-only too. */
function canvasMenu({ world, x, y, node, edge }: ContextHit) {
  const jumpItems = (node ? data.value.jumps.filter(j => j.node_key === node.key) : []).map(j => ({
    label: t('dg.ctx.openJump', { title: j.peer_title }),
    run: () => { location.hash = jumpHref(j, props.uid); },
  }));
  if (!editing.value) {
    openCtxMenu(x, y, [
      ...jumpItems, jumpItems.length && { sep: true },
      { label: t('dg.enableEditing'), run: () => setEditing(true) },
      { label: t('dg.center'), run: () => engine.value?.fit() },
    ]);
    return;
  }
  if (edge) {
    openCtxMenu(x, y, [
      { label: edge.label ? t('dg.ctx.edgeLabelEdit') : t('dg.ctx.edgeLabelAdd'), run: () => actions.editEdgeLabel(edge) },
      edge.label && { label: t('dg.ctx.edgeLabelClear'), run: () => actions.saveEdgeLabel(edge, '') },
      { sep: true },
      { label: t('dg.ctx.edgeDelete'), danger: true, run: () => actions.deleteEdge(edge) },
    ]);
    return;
  }
  if (node) {
    const stored = data.value.nodes.find(n => n.key === node.key);
    openCtxMenu(x, y, [
      ...jumpItems, jumpItems.length && { sep: true },
      { label: t('dg.ctx.nodeEdit'), run: () => actions.editStep(node) },
      { label: stored?.note ? t('dg.ctx.nodeNoteEdit') : t('dg.ctx.nodeNoteAdd'), run: () => actions.editNote(node.key) },
      /* the engine reports the pending end through onConnectProgress, which paints the hint */
      { label: t('dg.ctx.nodeConnect'), run: () => { engine.value?.startConnectFrom(node.key); connecting.value = true; } },
      (stored?.w != null || stored?.h != null) && { label: t('dg.ctx.resetSize'), run: () => actions.resetCardSize(node.key) },
      { sep: true },
      { label: t('dg.deleteStep'), danger: true, run: () => actions.deleteStep(node.key) },
    ]);
    return;
  }
  openCtxMenu(x, y, [
    { label: t('dg.ctx.addHere'), run: () => actions.addStepAt(world) },
    { label: t('dg.arrange'), run: actions.arrange },
    { label: t('dg.center'), run: () => engine.value?.fit() },
  ]);
}

/* The legend and the corner are drawn before the engine starts, since its first fit asks insets()
   how much of each corner they take. */
onMounted(() => {
  if (!canvas.value) return;
  const drawn = new DiagramEditor(canvas.value, data.value as never, {
    tipShow, tipHide,
    readOnly: true,
    onEditEdgeLabel: edge => void actions.editEdgeLabel(edge),
    onContextMenu: canvasMenu,
    insets: () => ({ top: (nav.value?.$el.offsetHeight || 0) + 18, bottom: (overlay.value?.offsetHeight || 0) + 18 }),
    onSelect: node => { selected.value = node ? node.key : null; },
    onSelectEdge: () => ed.paintHint(),
    onMove: positions => ed.queueLayout(positions),
    onConnectProgress: from => ed.paintHint(from),
    onConnect: (from, to) => void actions.connect(from, to),
  });
  engine.value = drawn;
  /* arrived from a jump: land on the step it named, which `selected` holds only if the flow has it */
  if (selected.value) drawn.focusNode(selected.value);
  ed.paintHint();
});
onBeforeUnmount(() => engine.value?.destroy());
</script>

<template>
  <div class="anim">
    <div class="view-head">
      <h2 class="view-title" :class="{ 'dg-editable': editing }" id="dgTitle" :title="editing ? t('dg.titleEdit') : ''"
          @click="editing && actions.editMeta()">{{ data.title }}</h2>
      <div class="view-sub"><a href="#/diagrams" class="dgl-back">{{ t('dgl.title') }}</a> · <span id="dgSub">{{
        sub }}</span></div>
    </div>
    <div class="dg-shell">
      <div class="dg-main">
        <div class="dg-tools" id="dgTools">
          <button class="btn btn-sm" :class="{ 'btn-solid': editing }" id="dgMode" :aria-pressed="editing"
                  @click="setEditing(!editing)">{{ editing ? t('dg.doneEditing') : t('dg.enableEditing') }}</button>
          <button class="btn btn-sm" id="dgAdd" data-editonly :disabled="!editing" @click="actions.addStep">{{
            t('dg.add') }}</button>
          <button class="btn btn-sm" :class="{ 'btn-solid': connecting }" id="dgConnect" data-editonly
                  :disabled="!editing" :aria-pressed="connecting" @click="toggleConnect">{{ t('dg.connect') }}</button>
          <button class="btn btn-sm" id="dgArrange" data-editonly :disabled="!editing" @click="actions.arrange">{{
            t('dg.arrange') }}</button>
          <button class="btn btn-sm" id="dgFont" data-editonly :disabled="!editing" :title="t('dg.font.switch')"
                  @click="actions.fontMenu($event.currentTarget as HTMLElement)">{{
            `Aa ${Math.round((data.font_scale || 1) * 100)}%` }}</button>
          <button class="btn btn-sm" id="dgFit" @click="engine?.fit()">{{ t('dg.center') }}</button>
          <button class="btn btn-sm" id="dgMermaid" @click="actions.mermaid">{{ t('dg.mermaid') }}</button>
          <button class="btn btn-sm" id="dgRecord" @click="openRecord(uid)">{{ t('dg.record') }}</button>
        </div>
        <div class="dg-stage" id="dgStage">
          <!-- a drawing; the inspector and the Mermaid export are the flow as text -->
          <canvas ref="canvas" id="dgCanvas" role="img" :aria-label="t('dg.canvasAlt', { title: diagram.title })"></canvas>
          <JumpNav ref="nav" :offers="offers" />
          <div ref="overlay" class="dg-overlay" id="dgOverlay">
            <div class="dg-hint" :class="{ warn: hint.warn }" id="dgHint" :hidden="!hint.shown">{{ hint.text }}</div>
            <div class="dg-legend" id="dgLegend"><span v-for="r in LEGEND" :key="r.sample" class="dg-legend-item"><span
              :style="{ color: r.color, display: 'inline-flex' }"><AppIcon :name="r.sample" cls="ico-line" /></span>{{
              r.label }}</span></div>
          </div>
        </div>
      </div>
      <DiagramSide />
    </div>
  </div>
</template>
