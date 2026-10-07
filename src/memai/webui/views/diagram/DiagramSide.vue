<script setup lang="ts">
/* The inspector: with no step selected, the flow's summary and the jumps aimed at the whole flow;
   with one, its fields, its connections, the memories that explain it and where it continues. */
import { computed } from 'vue';
import { peerName, typeClass } from '../../core/shared.js';
import { openRecord } from '../../core/nav.ts';
import { t } from '../../i18n.ts';
import AppIcon from '../../components/AppIcon.vue';
import JumpRow from './JumpRow.vue';
import StepPanel from './StepPanel.vue';
import { useActions } from './actions.ts';
import { useEditor } from './editor.ts';

const { uid, data, selected, editing, version } = useEditor();
const actions = useActions();

const node = computed(() => data.value.nodes.find(n => n.key === selected.value) ?? null);
const loose = computed(() => data.value.jumps.filter(j => !j.node_key));
const edges = computed(() => node.value ? [
  ...data.value.edges.filter(e => e.from === node.value?.key).map(e => ({ e, dir: 'out' as const, key: e.to })),
  ...data.value.edges.filter(e => e.to === node.value?.key).map(e => ({ e, dir: 'in' as const, key: e.from })),
] : []);
const links = computed(() => data.value.links.filter(l => l.node_key === node.value?.key)
  .map(l => ({ l, name: peerName(l.peer) as { text: string; hover: string; named: boolean } })));
const peerType = (p: object): string => ('type' in p ? String(p.type) : '');
const jumps = computed(() => data.value.jumps.filter(j => j.node_key === node.value?.key));
</script>

<template>
  <div class="dg-side" id="dgSide">
    <template v-if="!node">
      <div class="dg-panel">
        <h3>{{ t('dg.step') }}</h3>
        <div class="dg-empty">{{ t('dg.selectPrompt') }}</div>
      </div>
      <div class="dg-panel">
        <h3>{{ t('dg.summary') }} <button v-if="editing" class="btn btn-sm" id="dgMetaBtn" @click="actions.editMeta">{{
          t('common.edit') }}</button></h3>
        <div class="dg-empty">{{ data.summary || t('dg.noSummary') }}</div>
      </div>
      <!-- jumps aimed at the whole flow belong to no step, so no selection would show them -->
      <div v-if="loose.length" class="dg-panel">
        <h3>{{ t('dg.jumps.whole') }}</h3>
        <div class="dg-jumps"><JumpRow v-for="(j, i) in loose" :key="i" :j="j" :index="i" :uid="uid"
                                       :editing="editing" @remove="actions.deleteJump(j)" /></div>
      </div>
    </template>
    <template v-else>
      <StepPanel :key="`${node.key}:${version}:${editing}`" :node="node" :editing="editing" />

      <div class="dg-panel">
        <h3>{{ t('dg.edges') }}</h3>
        <div class="dg-edges">
          <div v-for="r in edges" :key="`${r.dir}:${r.e.from}:${r.e.to}`" class="dg-edge">
            <span class="dg-arrow"><AppIcon :name="r.dir === 'out' ? 'arrow-right' : 'arrow-left'" /></span>
            <span class="dg-key">{{ r.key }}</span>
            <span v-if="r.e.label" class="dg-label" :title="r.e.label">{{ r.e.label }}</span>
            <span class="spacer"></span>
            <template v-if="editing">
              <button class="icon-btn" :data-editedge="`${r.e.from}|${r.e.to}`" :title="t('dg.edge.editLabel')"
                      @click="actions.editEdgeLabel(r.e)"><AppIcon name="pencil" /></button>
              <button class="icon-btn danger" :data-deledge="`${r.e.from}|${r.e.to}`" :title="t('dg.edge.remove')"
                      @click="actions.deleteEdge(r.e)"><AppIcon name="close" /></button>
            </template>
          </div>
          <div v-if="!edges.length" class="dg-empty">{{ t('dg.edges.empty') }}</div>
        </div>
      </div>

      <div class="dg-panel">
        <h3>{{ t('dg.links') }}</h3>
        <div class="dg-empty">{{ t('dg.linksHint') }}</div>
        <div class="dg-links">
          <div v-for="{ l, name } in links" :key="l.target_uid" class="dg-link">
            <span class="type-tag" :class="typeClass(peerType(l.peer)) || undefined" style="flex:none"><span
              class="dot"></span>{{ peerType(l.peer) || '?' }}</span>
            <button type="button" class="snippet clickable" :class="{ 'mem-named': name.named }" :data-open="l.target_uid"
                    :title="name.hover || name.text || l.target_uid" @click="openRecord(l.target_uid)">{{
              name.text || l.target_uid }}</button>
            <button v-if="editing" class="icon-btn danger" :data-dellink="l.target_uid" :title="t('dg.link.remove')"
                    @click="actions.unlink(node.key, l.target_uid)"><AppIcon name="close" /></button>
          </div>
          <div v-if="!links.length" class="dg-empty">{{ t('dg.links.empty') }}</div>
        </div>
        <div v-if="editing" class="act-row">
          <button class="btn btn-sm" id="dgAttach" @click="actions.attach(node.key, links.map(x => x.l.target_uid))">{{
            t('dg.link.attach') }}</button>
        </div>
      </div>

      <!-- a linked memory is something to read about the step; a jump is somewhere to go from it -->
      <div class="dg-panel">
        <h3>{{ t('dg.jumps') }}</h3>
        <div class="dg-empty">{{ t('dg.jumpsHint') }}</div>
        <div class="dg-jumps">
          <JumpRow v-for="(j, i) in jumps" :key="i" :j="j" :index="i" :uid="uid" :editing="editing"
                   @remove="actions.deleteJump(j)" />
          <div v-if="!jumps.length" class="dg-empty">{{ t('dg.jumps.empty') }}</div>
        </div>
        <div v-if="editing" class="act-row">
          <button class="btn btn-sm" id="dgAddJump" @click="actions.addJump(node.key)">{{ t('dg.jump.add') }}</button>
        </div>
      </div>
    </template>
  </div>
</template>
