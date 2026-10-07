/* The diagram editor's state: the flow as the store last returned it, the selection, read-only or
   editing, and act(), which runs a write and reads the flow back, since the store decides the layout. */

import { inject, onBeforeUnmount, reactive, ref, shallowRef } from 'vue';
import type { InjectionKey } from 'vue';
import { failed, toast } from '../../core/ui.js';
import type { DiagramEditor, DiagramNode } from '../../engines/diagram-engine.ts';
import { t } from '../../i18n.ts';
import * as client from '../../api/client.ts';
import type { DiagramRecord } from '../../api/types.ts';

type Positions = Record<string, { x: number; y: number; w?: number; h?: number }>;

const LAYOUT_DELAY = 450;

export function useDiagramEditor(uid: string, initial: DiagramRecord, landOn: string) {
  const data = shallowRef(initial);
  const selected = ref<string | null>(landOn && initial.nodes.some(n => n.key === landOn) ? landOn : null);
  /* read-only until asked: a stray drag while reading rewrites a position every reader sees */
  const editing = ref(false);
  const engine = shallowRef<DiagramEditor | null>(null);
  const connecting = ref(false);
  const hint = reactive({ shown: false, text: '', warn: false });
  /* bumps on every read-back, so a form drawn from the old flow is drawn again */
  const version = ref(0);
  let alive = true;

  /* Says something only when there is something to say: which end of a connection is being picked,
     the selected line in words, or the steps the flow cannot reach. */
  function paintHint(connectFrom: DiagramNode | null = null) {
    const ed = engine.value;
    const edge = ed?.selectedEdge;
    const orphans = ed ? [...ed.orphans] : [];
    connecting.value = !!ed?.connectMode;
    if (ed?.connectMode) {
      Object.assign(hint, { shown: true, warn: false, text: connectFrom
        ? t('dg.hint.connectTarget', { key: connectFrom.key }) : t('dg.hint.connectSource') });
    } else if (edge) {
      Object.assign(hint, { shown: true, warn: false, text: t('dg.hint.edgeSelected', {
        from: edge.from, to: edge.to, label: edge.label ? ` · ${edge.label}` : '' }) });
    } else if (orphans.length) {
      Object.assign(hint, { shown: true, warn: true, text: t('dg.hint.orphans', { keys: orphans.join(', ') }) });
    } else {
      Object.assign(hint, { shown: false, text: '' });
    }
  }

  async function reload({ fit = false }: { fit?: boolean } = {}) {
    const next = await client.diagrams.get(uid);
    if (!alive) return;
    data.value = next;
    engine.value?.setData(next as never, { fit });
    if (selected.value && !next.nodes.some(n => n.key === selected.value)) selected.value = null;
    if (selected.value) engine.value?.select(selected.value);
    version.value++;
    paintHint();
  }

  async function act(write: () => Promise<unknown>, okMsg = ''): Promise<boolean> {
    try {
      await write();
      if (okMsg) toast(okMsg, 'ok');
      await reload();
      return true;
    } catch (err) {
      failed('err.diagram', err);
      return false;
    }
  }

  /* A drag moves the card at once and is written in batches; one still waiting when the view goes
     is written then rather than dropped. */
  const pending: Positions = {};
  let timer: ReturnType<typeof setTimeout> | undefined;
  async function flushLayout() {
    timer = undefined;
    const positions = { ...pending };
    for (const k of Object.keys(pending)) delete pending[k];
    if (!Object.keys(positions).length) return;
    try {
      await client.diagrams.layout(uid, { positions });
    } catch (err) {
      /* the canvas is now wrong about where things are; take the store's word */
      failed('err.diagram', err);
      if (alive) void reload();
    }
  }
  function queueLayout(positions: Positions) {
    Object.assign(pending, positions);
    clearTimeout(timer);
    timer = setTimeout(() => void flushLayout(), LAYOUT_DELAY);
  }

  onBeforeUnmount(() => {
    alive = false;
    if (timer !== undefined) { clearTimeout(timer); void flushLayout(); }
  });

  return { uid, data, selected, editing, engine, connecting, hint, version, paintHint, reload, act, queueLayout };
}

export type Editor = ReturnType<typeof useDiagramEditor>;

export const EDITOR: InjectionKey<Editor> = Symbol('diagram-editor');

export function useEditor(): Editor {
  const ed = inject(EDITOR);
  if (!ed) throw new Error('a diagram editor part outside the editor');
  return ed;
}
