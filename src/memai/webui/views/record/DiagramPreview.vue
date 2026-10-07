<script setup lang="ts">
/* A diagram record's body: its graph drawn read-only, with editing sent to the canvas editor. */
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { go } from '../../core/router.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import { DiagramEditor } from '../../engines/diagram-engine.ts';
import type { DiagramData } from '../../engines/diagram-engine.ts';
import AppIcon from '../../components/AppIcon.vue';

const props = defineProps<{ uid: string; title: string; content: string }>();

const canvas = ref<HTMLCanvasElement | null>(null);
/* the generated text is the fallback, shown when the graph cannot be fetched */
const unreadable = ref(false);
let engine: DiagramEditor | null = null;
let alive = true;

onMounted(async () => {
  try {
    const data = await client.diagrams.get(props.uid);
    if (alive && canvas.value) engine = new DiagramEditor(canvas.value, data as unknown as DiagramData, {});
  } catch (err) {
    console.error(err);
    if (alive) unreadable.value = true;
  }
});
/* the engine listens on window and holds a ResizeObserver, which dropping its canvas does not end */
onBeforeUnmount(() => {
  alive = false;
  try { engine?.destroy(); } catch (err) { console.error(err); }
  engine = null;
});
</script>

<template>
  <div class="rf">
    <header class="rf-head">
      <span class="rf-label">{{ t('dr.content') }}</span>
      <button id="dOpenEditor" type="button" class="rf-edit" @click="go('diagram', { uid })"><AppIcon
              name="pencil" />{{ t('dr.openEditor') }}</button>
    </header>
    <div id="dRecordStage" class="dg-stage dg-stage-record" :hidden="unreadable">
      <canvas id="dRecordCanvas" ref="canvas" role="img" :aria-label="t('dr.canvasAlt', { title: title || uid })"></canvas>
    </div>
    <pre id="dContent" class="content-pre" :hidden="!unreadable">{{ content }}</pre>
    <div class="dg-empty">{{ t('dr.generated') }}</div>
  </div>
</template>
